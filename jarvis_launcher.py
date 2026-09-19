# ============================================================================
# jarvis_launcher.py — Jarvis v5 INTELLIGENT OPEN SYSTEM
#
# Replaces ALL hardcoded lookup tables (APP_MAPPINGS / KNOWN_SITES /
# _SITE_ALIASES) with real system discovery + reasoning:
#
#   1. DISCOVER  → scan Start Menu, Program Files ×2, LocalAppData\Programs,
#                  Desktop, Steam library, Epic Games manifests, Xbox
#                  (GamingRoots), UWP/Store apps (Get-StartApps).
#   2. CACHE     → jarvis_cache/app_index.json (NOT jarvis_temp — that folder
#                  is wiped hourly). TTL env: JARVIS_APP_CACHE_TTL (default 1h).
#   3. MATCH     → rapidfuzz fuzzy + token matching with a confidence policy:
#                  strong single match → launch; ambiguous → ask the user.
#   4. WEBSITES  → no fixed list: learned-site cache → intelligent URL
#                  construction with DNS + HTTP validation → web-search for
#                  the official site → tiny emergency fallback only.
#   5. MEMORY    → launch history so "the game I played yesterday" works.
#
# Single entry point for the agent:  smart_open(target)
# Helper tools: refresh_app_index_tool, list_discovered_apps_tool,
#               resolve_website_tool
# ============================================================================
import asyncio
import json
import logging
import os
import re
import socket
import subprocess
import sys
import time

logger = logging.getLogger(__name__)

try:
    _reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(_reconfigure):
        _reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    from livekit.agents import function_tool
except ImportError:  # tools used outside the LiveKit agent process (tests)
    from typing import Any as _Any, cast as _cast

    def _passthrough_tool(func):
        return func

    # cast(Any, …) keeps type-checkers happy against livekit's overloaded
    # import signature while behaving as a plain identity decorator.
    function_tool = _cast(_Any, _passthrough_tool)

import jarvis_safety as safety

from typing import Any

# fuzz: rapidfuzz module | fuzzywuzzy module | None (both unavailable).
# Declared as Any so the fallback chain stays type-checker clean; all
# fuzz.* usage is guarded by _HAVE_FUZZ at runtime.
fuzz: Any

try:
    from rapidfuzz import fuzz
    _HAVE_FUZZ = True
except ImportError:
    try:
        from fuzzywuzzy import fuzz
        _HAVE_FUZZ = True
    except ImportError:
        fuzz = None
        _HAVE_FUZZ = False

# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_CACHE_DIR = os.path.join(_HERE, "jarvis_cache")

APP_INDEX_FILE = os.path.join(_CACHE_DIR, "app_index.json")
LEARNED_SITES_FILE = os.path.join(_CACHE_DIR, "learned_sites.json")
LAUNCH_HISTORY_FILE = os.path.join(_CACHE_DIR, "launch_history.json")

try:
    CACHE_TTL = float(os.getenv("JARVIS_APP_CACHE_TTL", "") or 3600)  # 1 hour
except ValueError:
    CACHE_TTL = 3600.0

# Matching policy
_MATCH_MIN   = 62   # below this → not even a candidate
_CONFIDENT   = 88   # >= this → launch immediately
_GOOD        = 80   # >= this + clear winner over runner-up → launch
_ASK         = 75   # >= this but not strong → ask the user to pick
_GAP_NEEDED  = 12   # clear winner over the runner-up

# ──────────────────────────────────────────────
# TINY emergency fallbacks (LAST resort only — the primary paths are all
# dynamic discovery / intelligent resolution above).
#   • _EMERGENCY_PROTOCOLS: Windows built-in shell targets that CANNOT be
#     discovered by scanning (ms-settings pages, shell: folders, core .msc).
#   • _EMERGENCY_SITES: 3 core sites for the rare case DNS + search both
#     fail (offline machine).
# ──────────────────────────────────────────────
_EMERGENCY_PROTOCOLS = {
    "settings": "ms-settings:", "recycle bin": "shell:RecycleBinFolder",
    "downloads": "%USERPROFILE%\\Downloads", "documents": "%USERPROFILE%\\Documents",
    "desktop": "%USERPROFILE%\\Desktop", "pictures": "%USERPROFILE%\\Pictures",
    "startup folder": "shell:startup", "explorer": "explorer",
    "task manager": "taskmgr", "command prompt": "cmd", "cmd": "cmd",
    "device manager": "devmgmt.msc", "disk management": "diskmgmt.msc",
    "services": "services.msc", "registry editor": "regedit",
    "store": "ms-windows-store:", "camera": "microsoft.windows.camera:",
}

_EMERGENCY_SITES = {
    "google": "https://www.google.com",
    "youtube": "https://www.youtube.com",
    "gmail": "https://mail.google.com",
}

# Speech-noise to strip from a spoken target before reasoning
_FILLER_RE = re.compile(
    r"\b(open|kholo|khol|launch|please|plz|jaldi|now|abhi|my|mera|meri|the|a|an"
    r"|app|application|website|site|page|kar|do|de|wala|wali|ka|ki|ko)\b",
    re.IGNORECASE,
)

# Executable-name noise (never treated as a launchable app)
_EXE_NOISE_RE = re.compile(
    r"(unins|setup|installer|_install|update|updater|upgrade|crash|report"
    r"|eula|helper|licen[cs]e|readme|redist|dxsetup|dotnet|vcredist|autoplay"
    r"|registration|activation|patcher)",
    re.IGNORECASE,
)

# Start-menu entry names that are never apps
_LNK_NOISE_RE = re.compile(
    r"uninstall|documentation|release notes|license|readme|website|"
    r"visit .* online|check for updates",
    re.IGNORECASE,
)

# Program Files directories that never contain a user-facing launcher
_PRUNE_DIRS = {"common files", "microsoft shared", "windowsapps", "packages",
               "installer", "reference assemblies", "windows defender",
               "windows defender advanced threat protection", "windows nt"}

_RECENT_PHRASE_RE = re.compile(
    r"\b(last|previous|recent|pichla|pichle|pichli|pehle|kal|yesterday)\b"
    r".*?\b(game|khel|app|application)\b"
    r"|\b(game|khel)\b.*?\b(last|previous|recent|pichla|pichle|kal|yesterday)\b"
    r"|پچھلے?.*(گیم|ایپ)|آخری.*(گیم|ایپ)",
    re.IGNORECASE,
)


def _ensure_cache_dir():
    os.makedirs(_CACHE_DIR, exist_ok=True)


def _expand(p: str) -> str:
    return os.path.expandvars(os.path.expanduser((p or "").strip().strip('"')))


def _name_key(display: str) -> str:
    """Normalize a display name into a match key (lowercase, no junk)."""
    s = (display or "").lower().strip()
    s = re.sub(r"\.(exe|lnk|url|appref-ms|bat|cmd)$", "", s)
    s = re.sub(r"[._\-–—()\[\]]+", " ", s)
    s = re.sub(r"\b(x64|x86|win32|win64|64 bit|32 bit)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def clean_query(raw: str) -> str:
    """Strip speech noise from a spoken target ("open my vs code please" → "vs code")."""
    s = (raw or "").strip().strip('"').lower()
    s = re.sub(r"^(jarvis[, ]+)+", "", s)
    s = _FILLER_RE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


# ──────────────────────────────────────────────
# DISCOVERY — real system scanning (the PRIMARY source of truth)
# ──────────────────────────────────────────────
_LNK_SHELL = None


def _resolve_lnk(path: str) -> str:
    """Resolve a .lnk shortcut to its real target (best effort)."""
    global _LNK_SHELL
    try:
        import win32com.client  # noqa
        if _LNK_SHELL is None:
            _LNK_SHELL = win32com.client.Dispatch("WScript.Shell")
        lnk = _LNK_SHELL.CreateShortCut(path)
        t = lnk.Targetpath or ""
        return t if t and os.path.exists(t) else ""
    except Exception:
        return ""


def _scan_start_menu():
    """Start Menu (.lnk/.url) + Desktop shortcuts — highest quality names."""
    entries = []
    roots = [
        _expand(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs"),
        _expand(r"%ProgramData%\Microsoft\Windows\Start Menu\Programs"),
        _expand(r"%USERPROFILE%\Desktop"),
        _expand(r"%PUBLIC%\Desktop"),
    ]
    seen = set()
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, _, files in os.walk(root):
            for f in files:
                low = f.lower()
                if not low.endswith((".lnk", ".url")):
                    continue
                display = os.path.splitext(f)[0]
                if _LNK_NOISE_RE.search(display):
                    continue
                key = _name_key(display)
                if not key or key in seen:
                    continue
                seen.add(key)
                path = os.path.join(dirpath, f)
                target = _resolve_lnk(path)
                entries.append({
                    "name": key, "display": display,
                    "launch": target or path,
                    "kind": "app", "source": "startmenu",
                })
    return entries


def _steam_libraries():
    libs = []
    for vdf in (
        _expand(r"%ProgramFiles(x86)%\Steam\steamapps\libraryfolders.vdf"),
        _expand(r"%ProgramFiles%\Steam\steamapps\libraryfolders.vdf"),
        _expand(r"%ProgramFiles(x86)%\Steam\config\libraryfolders.vdf"),
    ):
        if not os.path.isfile(vdf):
            continue
        try:
            with open(vdf, encoding="utf-8", errors="ignore") as fh:
                txt = fh.read()
            for m in re.finditer(r'"path"\s+"([^"]+)"', txt):
                p = m.group(1).replace("\\\\", "\\")
                if p and p not in libs:
                    libs.append(p)
        except Exception as e:
            logger.warning(f"steam vdf read failed: {e}")
    return libs


def _scan_steam():
    """Installed Steam games → launch via steam://rungameid protocol."""
    entries = []
    seen = set()
    for lib in _steam_libraries():
        manifest_dir = os.path.join(lib, "steamapps")
        if not os.path.isdir(manifest_dir):
            continue
        try:
            manifests = [f for f in os.listdir(manifest_dir)
                         if f.startswith("appmanifest_") and f.endswith(".acf")]
        except Exception:
            continue
        for mf in manifests:
            appid = mf[len("appmanifest_"):-len(".acf")]
            try:
                with open(os.path.join(manifest_dir, mf),
                          encoding="utf-8", errors="ignore") as fh:
                    txt = fh.read()
            except Exception:
                continue
            m = re.search(r'"name"\s+"([^"]+)"', txt)
            if not m:
                continue
            display = m.group(1)
            key = _name_key(display)
            if not key or key in seen:
                continue
            seen.add(key)
            entries.append({
                "name": key, "display": display,
                "launch": f"steam://rungameid/{appid}",
                "kind": "game", "source": "steam",
            })
    return entries


def _scan_epic():
    """Epic Games installed manifests (.item) → direct exe launch."""
    entries = []
    seen = set()
    mdir = _expand(r"%ProgramData%\Epic\EpicGamesLauncher\Data\Manifests")
    if not os.path.isdir(mdir):
        return entries
    for f in os.listdir(mdir):
        if not f.endswith(".item"):
            continue
        try:
            with open(os.path.join(mdir, f), encoding="utf-8", errors="ignore") as fh:
                data = json.load(fh)
        except Exception:
            continue
        display = data.get("DisplayName") or ""
        exe = data.get("LaunchExecutable") or ""
        install = data.get("InstallLocation") or ""
        if not display or not exe:
            continue
        key = _name_key(display)
        if not key or key in seen:
            continue
        seen.add(key)
        launch = os.path.join(install, exe) if install else exe
        entries.append({
            "name": key, "display": display, "launch": launch,
            "kind": "game", "source": "epic",
        })
    return entries


def _xbox_roots():
    roots = []
    try:
        import winreg
        for view in (0, winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                    r"SOFTWARE\Microsoft\GamingRoots",
                                    0, winreg.KEY_READ | view) as k:
                    i = 0
                    while True:
                        try:
                            val = winreg.EnumValue(k, i)[1]
                        except OSError:
                            break
                        i += 1
                        p = str(val).replace("/", "\\").rstrip("\\.")
                        if p and os.path.isdir(p) and p not in roots:
                            roots.append(p)
            except OSError:
                continue
    except Exception:
        pass
    default = "C:\\XboxGames"
    if os.path.isdir(default) and default not in roots:
        roots.append(default)
    return roots


def _scan_xbox():
    """Xbox / Microsoft Store PC games (GamingRoots + C:\\XboxGames layout)."""
    entries = []
    seen = set()
    for root in _xbox_roots():
        try:
            games = [d for d in os.listdir(root)
                     if os.path.isdir(os.path.join(root, d))
                     and d.lower() not in ("commonredist", "temp", "logs")]
        except Exception:
            continue
        for game in games:
            content = os.path.join(root, game, "Content")
            base = content if os.path.isdir(content) else os.path.join(root, game)
            if not os.path.isdir(base):
                continue
            try:
                exes = [f for f in os.listdir(base) if f.lower().endswith(".exe")]
            except Exception:
                continue
            pick = ""
            for f in exes:  # prefer an exe matching the game folder name
                if _name_key(f) == _name_key(game):
                    pick = f
                    break
            if not pick:
                for f in exes:
                    if not _EXE_NOISE_RE.search(os.path.splitext(f)[0]):
                        pick = f
                        break
            if not pick:
                continue
            key = _name_key(game)
            if not key or key in seen:
                continue
            seen.add(key)
            entries.append({
                "name": key, "display": game,
                "launch": os.path.join(base, pick),
                "kind": "game", "source": "xbox",
            })
    return entries


def _scan_program_dirs():
    """Program Files / LocalAppData\\Programs — direct .exe scan (depth ≤ 3)."""
    entries = []
    seen = set()
    roots = [
        _expand("%ProgramFiles%"),
        _expand("%ProgramFiles(x86)%"),
        _expand(r"%LOCALAPPDATA%\Programs"),
    ]
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, dirs, files in os.walk(root):
            depth = dirpath[len(root):].count(os.sep)
            if depth >= 3:
                dirs[:] = []
                continue
            dirs[:] = [d for d in dirs if d.lower() not in _PRUNE_DIRS
                       and not d.startswith(("$", "."))]
            for f in files:
                if not f.lower().endswith(".exe"):
                    continue
                base = os.path.splitext(f)[0]
                if _EXE_NOISE_RE.search(base):
                    continue
                key = _name_key(base)
                if not key or len(key) < 2 or key in seen:
                    continue
                seen.add(key)
                entries.append({
                    "name": key, "display": base,
                    "launch": os.path.join(dirpath, f),
                    "kind": "app", "source": "programfiles",
                })
    return entries


def _scan_uwp():
    """UWP / Store / Settings entries via Get-StartApps → shell:AppsFolder."""
    entries = []
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-StartApps | Where-Object {$_.AppID -like '*!*'} | "
             "ConvertTo-Json -Compress"],
            capture_output=True, timeout=40,
        )
        data = json.loads(out.stdout.decode("utf-8", "ignore").strip() or "[]")
        if isinstance(data, dict):
            data = [data]
    except Exception as e:
        logger.warning(f"Get-StartApps failed: {e}")
        return entries
    for item in data:
        name = (item.get("Name") or "").strip()
        appid = (item.get("AppID") or "").strip()
        if not name or "!" not in appid:
            continue
        key = _name_key(name)
        if not key or key in ("desktop", "application", "search", "task view"):
            continue
        entries.append({
            "name": key, "display": name,
            "launch": f"shell:AppsFolder\\{appid}",
            "kind": "uwp", "source": "uwp",
        })
    return entries


def _merge_entries(groups):
    """Merge scanner results, de-duplicating by name key (earlier wins)."""
    seen = set()
    out = []
    for entries in groups:
        for e in entries:
            key = e.get("name")
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(e)
    return out


# ──────────────────────────────────────────────
# App index — persistent cache with TTL
# ──────────────────────────────────────────────
_APP_INDEX = None       # list[dict] when loaded/scaned
_APP_INDEX_TIME = 0.0


def _load_index_cache():
    try:
        if os.path.isfile(APP_INDEX_FILE):
            age = time.time() - os.path.getmtime(APP_INDEX_FILE)
            if age < CACHE_TTL:
                with open(APP_INDEX_FILE, encoding="utf-8") as fh:
                    data = json.load(fh)
                return data.get("apps") or []
    except Exception as e:
        logger.warning(f"app index cache read failed: {e}")
    return None


def _save_index_cache(apps):
    try:
        _ensure_cache_dir()
        tmp = APP_INDEX_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"time": time.time(), "apps": apps}, fh, ensure_ascii=False)
        os.replace(tmp, APP_INDEX_FILE)
    except Exception as e:
        logger.warning(f"app index cache write failed: {e}")


def scan_installed_apps_sync(force: bool = False):
    """Full system scan (blocking) — Start Menu, Steam, Epic, Xbox, Program
    Files, UWP. Cached with a TTL; force=True re-scans immediately."""
    global _APP_INDEX, _APP_INDEX_TIME
    if not force and _APP_INDEX is not None:
        return _APP_INDEX
    if not force:
        cached = _load_index_cache()
        if cached:
            _APP_INDEX = cached
            _APP_INDEX_TIME = time.time()
            return _APP_INDEX

    started = time.time()
    apps = _merge_entries([
        _scan_start_menu(),
        _scan_steam(),
        _scan_epic(),
        _scan_xbox(),
        _scan_program_dirs(),
        _scan_uwp(),
    ])
    _APP_INDEX = apps
    _APP_INDEX_TIME = time.time()
    _save_index_cache(apps)
    logger.info(f"✅ App discovery: {len(apps)} entries in {time.time() - started:.1f}s")
    return apps


async def get_app_index(force: bool = False):
    if force or _APP_INDEX is None:
        return await asyncio.to_thread(scan_installed_apps_sync, force)
    return _APP_INDEX


# ──────────────────────────────────────────────
# MATCHING — fuzzy + token with a confidence policy
# ──────────────────────────────────────────────
def _score_entry(query: str, entry: dict) -> float:
    ql = query.lower().strip()
    name = entry["name"]
    display = entry["display"].lower()
    if not _HAVE_FUZZ:
        if ql == name or ql == display:
            return 100.0
        if ql in name or ql in display:
            return 80.0
        return 0.0
    s = max(fuzz.WRatio(ql, name), 0.92 * fuzz.WRatio(ql, display))
    # Whole-word containment boosts (semantic-lite)
    if re.search(rf"(?<![a-z0-9]){re.escape(ql)}(?![a-z0-9])", name):
        s = max(s, 92.0)
    elif len(ql) >= 4 and ql in name:
        s = max(s, 78.0)
    if ql == name or ql == display:
        s = 100.0
    # Last-word match ("vs code" → "visual studio code"): the distinctive
    # final token agreeing is a strong signal when fuzzy score is decent.
    ql_toks, name_toks = ql.split(), name.split()
    if ql_toks and name_toks and ql_toks[-1] == name_toks[-1] and s >= 70:
        s = max(s, 90.0)
    # Acronym match ("vs" → "visual studio"): initials agreement.
    if 2 <= len(ql) <= 5 and len(name_toks) > 1 and ql.isalpha():
        initials = "".join(w[0] for w in name_toks if w)
        if ql == initials:
            s = max(s, 88.0)
    return s


def match_apps(query: str, apps=None) -> list:
    """Return [(entry, score), ...] sorted best-first (score ≥ _MATCH_MIN)."""
    q = _name_key(query)
    if not q:
        return []
    apps = apps if apps is not None else (_APP_INDEX or [])
    scored = []
    for e in apps:
        s = _score_entry(q, e)
        if s >= _MATCH_MIN:
            scored.append((e, round(s, 1)))
    scored.sort(key=lambda t: (-t[1], len(t[0]["name"])))
    return scored


# ──────────────────────────────────────────────
# LAUNCH MEMORY — history so "the game I played yesterday" works
# ──────────────────────────────────────────────
def _record_history(entry: dict):
    try:
        _ensure_cache_dir()
        hist = []
        if os.path.isfile(LAUNCH_HISTORY_FILE):
            with open(LAUNCH_HISTORY_FILE, encoding="utf-8") as fh:
                hist = json.load(fh)
        hist.insert(0, {
            "name": entry["name"], "display": entry["display"],
            "kind": entry["kind"], "source": entry["source"],
            "launch": entry["launch"], "ts": time.time(),
        })
        with open(LAUNCH_HISTORY_FILE, "w", encoding="utf-8") as fh:
            json.dump(hist[:150], fh, ensure_ascii=False)
    except Exception as e:
        logger.debug(f"history write failed: {e}")


def _last_played(kind: str | None = None):
    """Most recent launch from history (optionally filtered: kind='game')."""
    try:
        if os.path.isfile(LAUNCH_HISTORY_FILE):
            with open(LAUNCH_HISTORY_FILE, encoding="utf-8") as fh:
                hist = json.load(fh)
            for h in hist:
                if kind and h.get("kind") != kind:
                    continue
                return h
    except Exception:
        pass
    return None


# ──────────────────────────────────────────────
# LAUNCH
# ──────────────────────────────────────────────
async def _launch_entry(entry: dict) -> str:
    launch = entry["launch"]
    try:
        await asyncio.to_thread(os.startfile, launch)
    except (AttributeError, OSError):
        try:
            subprocess.Popen(["cmd", "/c", "start", "", launch],
                             creationflags=subprocess.CREATE_NO_WINDOW)
        except Exception as e:
            safety.record_action("smart_open", f"{entry['display']}: {e}", False)
            return f"❌ {entry['display']} نہیں کھل سکی: {e}"
    except Exception as e:
        safety.record_action("smart_open", f"{entry['display']}: {e}", False)
        return f"❌ {entry['display']} نہیں کھل سکی: {e}"

    _record_history(entry)
    safety.record_action("smart_open",
                         f"{entry['display']} ({entry['source']}: {launch})", True)

    # Best-effort focus once the window appears
    try:
        from Jarvis_window_CTRL import focus_window
        await asyncio.sleep(0.6)
        await focus_window(_name_key(entry["display"]).split()[0])
    except Exception:
        pass

    icon = {"game": "🎮", "uwp": "🪟"}.get(entry["kind"], "🚀")
    return (f"{icon} کھول دیا: {entry['display']} "
            f"({entry['source']} سے کھوج نکالی)\n🛠️ {launch}")


async def _open_web(url: str) -> str:
    """Open a URL through Jarvis's real-browser (CDP) control."""
    try:
        from jarvis_browser import open_url
        return await open_url(url)
    except Exception:
        try:
            await asyncio.to_thread(os.startfile, url)
            return f"🌐 کھولا: {url}"
        except Exception as e:
            return f"❌ {url} نہیں کھل سکا: {e}"


# ──────────────────────────────────────────────
# WEBSITE INTELLIGENCE — no hardcoded site list
#   learned cache → construct+validate (DNS/HTTP) → web search → emergency
# ──────────────────────────────────────────────
_LEARNED = None


def _get_learned() -> dict:
    global _LEARNED
    if _LEARNED is None:
        try:
            if os.path.isfile(LEARNED_SITES_FILE):
                with open(LEARNED_SITES_FILE, encoding="utf-8") as fh:
                    _LEARNED = json.load(fh)
            else:
                _LEARNED = {}
        except Exception:
            _LEARNED = {}
    return _LEARNED


def _remember_site(name: str, url: str, how: str):
    try:
        learned = _get_learned()
        learned[name] = {"url": url, "how": how, "ts": time.time()}
        _ensure_cache_dir()
        with open(LEARNED_SITES_FILE, "w", encoding="utf-8") as fh:
            json.dump(learned, fh, ensure_ascii=False)
    except Exception as e:
        logger.debug(f"learned-site save failed: {e}")


def _dns_ok(host: str) -> bool:
    old = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(2.5)
        socket.getaddrinfo(host, None)
        return True
    except Exception:
        return False
    finally:
        socket.setdefaulttimeout(old)


def _host_of(url: str) -> str:
    m = re.search(r"://([^/]+)", url)
    return (m.group(1) if m else url).lower().replace("www.", "", 1)


def _site_search_candidates(query: str):
    """One web-search result claiming to be the official site (best effort)."""
    try:
        from ddgs import DDGS
        with DDGS(timeout=8) as d:
            for r in d.text(f"{query} official website", max_results=6):
                href = (r.get("href") or r.get("url") or "").strip()
                if href.startswith(("http://", "https://")) and "duckduckgo" not in href:
                    yield href
    except Exception as e:
        logger.debug(f"site search failed: {e}")


async def _verify_site(url: str) -> bool:
    """HTTP HEAD (redirect-following) — must respond like a real site."""
    try:
        import aiohttp
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as s:
            async with s.head(url, allow_redirects=True, ssl=False) as resp:
                return resp.status < 500
    except Exception:
        return False


async def resolve_website(name: str, allow_search: bool = True):
    """Intelligently resolve ANY name to an official URL.

    Returns (url, how) — url is None when truly unresolvable.
    Order: explicit URL → learned cache → constructed domains validated by
    DNS + HTTP → web search for the official site → emergency fallback.
    """
    t = (name or "").strip().strip('"')
    if not t:
        return None, ""

    low = t.lower()
    if low.startswith(("http://", "https://")):
        return t, "explicit-url"

    q = clean_query(t)
    if not q:
        q = low

    # Learned memory — Jarvis remembers every site it has resolved before
    learned = _get_learned()
    if q in learned:
        return learned[q]["url"], f"learned-cache"

    # Looks like a domain already ("example.com", "mail.proton.me")
    if re.match(r"^[a-z0-9-]+(\.[a-z0-9-]+)+", q) and "." in q:
        url = q if q.startswith("http") else "https://" + q
        return url, "explicit-domain"

    slug = re.sub(r"[^a-z0-9]", "", q.replace(" ", "")) or re.sub(r"\s+", "-", q)

    # Construct candidate domains and validate them with DNS (+ HTTP)
    candidates = [f"https://www.{slug}{tld}"
                  for tld in (".com", ".org", ".net", ".io", ".ai", ".co", ".dev")]
    for url in candidates:
        ok = await asyncio.to_thread(_dns_ok, _host_of(url))
        if ok and await _verify_site(url):
            _remember_site(q, url, "constructed")
            return url, "constructed+verified"

    # Web search — find the official site and open that
    if allow_search:
        for href in _site_search_candidates(q):
            if await _verify_site(href):
                _remember_site(q, href, "web-search")
                return href, "web-search"

    # Tiny emergency fallback (offline machine / DNS broken)
    for key, url in _EMERGENCY_SITES.items():
        if key in q or q in key:
            return url, "emergency-fallback"
    return None, ""


def resolve_website_sync(name: str):
    """Fast SYNC resolver for internal callers (e.g. normalize_url):
    learned cache → emergency fallback → generic construction."""
    t = (name or "").strip()
    q = clean_query(t) or t.lower()
    if re.match(r"^[a-z0-9-]+(\.[a-z0-9-]+)+", q):
        return (q if q.startswith("http") else "https://" + q), "explicit-domain"
    learned = _get_learned()
    if q in learned:
        return learned[q]["url"], "learned-cache"
    for key, url in _EMERGENCY_SITES.items():
        if key == q:
            return url, "emergency-fallback"
    slug = re.sub(r"[^a-z0-9]", "", q.replace(" ", ""))
    if slug:
        return f"https://www.{slug}.com", "constructed"
    return "https://www.google.com", "fallback"


# ──────────────────────────────────────────────
# SMART OPEN — the single intelligent entry point
# ──────────────────────────────────────────────
async def smart_open_impl(target: str) -> str:
    """Resolve ANY natural-language target and open it.

    Order of reasoning:
      1. URL / domain                → open in Jarvis's real browser
      2. Real path (file/folder)     → open directly
      3. Memory phrases              → last played game/app from history
      4. Discovered app / game       → launch by its real type
      5. Ambiguous                   → ask the user to pick
      6. File/folder smart search    → open the best match
      7. Website intelligence        → construct / validate / search the URL
      8. Windows shell protocol      → tiny emergency fallback
    """
    if safety.is_stopped():
        return "🛑 EMERGENCY STOP فعال ہے — پہلے 'Jarvis continue' کہیں۔"

    raw = (target or "").strip().strip('"')
    if not raw:
        return "❌ بتائیں کیا کھولنا ہے۔"
    low = raw.lower()

    # 1 ── URL-ish → browser
    if low.startswith(("http://", "https://", "www.")):
        return await _open_web(raw if "://" in low else "https://" + raw)

    # 2 ── Real path (quotes/expandvars tolerated)
    p = _expand(raw)
    if os.path.exists(p):
        try:
            await asyncio.to_thread(os.startfile, p)
            safety.record_action("smart_open", f"path: {p}", True)
            try:
                from Jarvis_window_CTRL import focus_window
                await focus_window(os.path.basename(p))
            except Exception:
                pass
            return f"📂 کھول دیا: {p}"
        except Exception as e:
            return f"❌ {p} نہیں کھل سکا: {e}"

    # 3 ── Memory: "the game I played yesterday" / "آخری گیم"
    if _RECENT_PHRASE_RE.search(low):
        kind = "game" if re.search(r"\b(game|khel)\b|گیم", low, re.I) else None
        recent = _last_played(kind)
        if recent:
            e = {"name": recent["name"], "display": recent["display"],
                 "launch": recent["launch"], "kind": recent["kind"],
                 "source": f"memory({recent['source']})"}
            return await _launch_entry(e)
        return "🧠 کھیلنے کی کوئی یادداشت نہیں — نام بتا دیں میں ڈھونڈ لیتا ہوں۔"

    q = clean_query(raw) or low

    # 4/5 ── Discovered app / game (fuzzy + confidence policy)
    apps = await get_app_index()
    matches = match_apps(q, apps)
    if matches:
        top, score = matches[0]
        runner = matches[1][1] if len(matches) > 1 else 0.0
        strong = (score >= _CONFIDENT
                  or (score >= _GOOD and (len(matches) == 1
                                          or score - runner >= _GAP_NEEDED)))
        if strong:
            return await _launch_entry(top)
        if score >= _ASK:
            opts = "\n".join(
                f"  {i + 1}. {e['display']} ({e['source']}, {s:g}%)"
                for i, (e, s) in enumerate(matches[:4]))
            safety.record_action("smart_open", f"ambiguous: {raw}", False)
            return (f"❓ '{raw}' سے کون سی چیز مراد ہے؟\n{opts}\n"
                    "M. Awwab sir سے پوچھیں، پھر selected نام کے ساتھ "
                    "smart_open دوبارہ کال کریں۔")
        # weak match (score < _ASK) → fall through to file / website paths

    # 6 ── File / folder smart search (existing index, high-confidence only)
    try:
        from Jarvis_window_CTRL import smart_search
        item = await smart_search(q, min_score=82)
        if item:
            try:
                await asyncio.to_thread(os.startfile, item["path"])
                safety.record_action("smart_open", f"file: {item['path']}", True)
                try:
                    from Jarvis_window_CTRL import focus_window
                    await focus_window(os.path.basename(item["path"]))
                except Exception:
                    pass
                return (f"📄 {'📁' if item['type'] == 'folder' else '📄'} "
                        f"کھول دیا: {item['path']}")
            except Exception as e:
                return f"❌ {item['path']} نہیں کھل سکا: {e}"
    except Exception:
        pass

    # 7 ── Website intelligence (construct → verify → search → learn)
    url, how = await resolve_website(q)
    if url:
        result = await _open_web(url)
        if how in ("constructed+verified", "web-search"):
            return f"🌐 سائٹ intelligently resolve ہوئی ({how})\n{result}"
        return result

    # 8 ── Tiny Windows-protocol emergency fallback
    cmd = _EMERGENCY_PROTOCOLS.get(q)
    if cmd:
        cmd = _expand(cmd)
        try:
            await asyncio.to_thread(os.startfile, cmd)
            safety.record_action("smart_open", f"protocol: {q}", True)
            return f"🪟 کھول دیا: {q}"
        except Exception as e:
            return f"❌ {q} نہیں کھل سکا: {e}"

    safety.record_action("smart_open", f"not found: {raw}", False)
    return (f"❌ '{raw}' کہیں نہیں ملا۔\n"
            "کوشش کریں: exact نام بتائیں، یا refresh_app_index_tool چلائیں "
            "(نئی ایپ نصب ہو تو)، یا full URL دیں।")


@function_tool
async def smart_open(target: str) -> str:
    """کوئی بھی چیز ذہانت سے کھولیں — ایپ، گیم (Steam/Epic/Xbox)، ویب سائٹ،
    فائل یا فولڈر۔ صرف نام دیں ('gta 5', 'vs code', 'netflix', 'racing game')۔
    Jarvis خود system discovery سے ڈھونڈتا ہے؛ ambiguous ہو تو options پوچھتا ہے۔
    نئی ایپ نہ ملے تو پہلے refresh_app_index_tool چلائیں۔"""
    return await smart_open_impl(target)


@function_tool
async def refresh_app_index_tool() -> str:
    """Installed apps/games کی discovery cache فوراً refresh کریں — جب کوئی
    نئی ایپ/گیم install ہو اور smart_open اسے نہ ڈھونڈ سکے۔"""
    apps = await asyncio.to_thread(scan_installed_apps_sync, True)
    return (f"🔄 Discovery مکمل — {len(apps)} entries ملے۔ "
            "(Start Menu, Program Files, Steam, Epic, Xbox, UWP scan ہوا۔)")


@function_tool
async def list_discovered_apps_tool(pattern: str = "", limit: int = 25) -> str:
    """Discovered apps/games کی فہرست (optional نام pattern)۔ User کو بتانے
    کے لیے کہ اُس کے PC پر کیا نصب/کھولا جا سکتا ہے۔"""
    apps = await get_app_index()
    pat = (pattern or "").strip().lower()
    if pat:
        hits = [a for a in apps if pat in a["name"] or pat in a["display"].lower()]
        if not hits:
            hits = [m[0] for m in match_apps(pat, apps)]
    else:
        hits = list(apps)
    if not hits:
        return (f"❌ '{pattern}' سے کوئی میل نہیں — "
                "refresh_app_index_tool آزمائیں۔")
    hits = hits[:max(1, min(50, int(limit)))]
    lines = [f"• {a['display']}  ({a['source']}, {a['kind']})" for a in hits]
    return f"🔎 {len(hits)} چیریں (کل {len(apps)} indexed):\n" + "\n".join(lines)


@function_tool
async def resolve_website_tool(name: str) -> str:
    """کسی سائٹ کے نام کو official URL میں intelligently resolve کریں (بغیر
    کھلے) — learned cache / DNS validation / web search کے ذریعے۔"""
    url, how = await resolve_website(name)
    if url:
        return f"🌐 '{name}' → {url}  (طریقہ: {how})"
    return f"❌ '{name}' کی official سائٹ resolve نہیں ہو سکی۔"






