import os
import json
import shutil
import subprocess
import logging
import sys
import asyncio
import time
import re
import string

try:
    from rapidfuzz import process as rfprocess
    def _fuzzy_extract_one(query, choices):
        r = rfprocess.extractOne(query, choices, score_cutoff=0)
        return (r[0], r[1]) if r else (None, 0)
except ImportError:
    from fuzzywuzzy import process as fwprocess
    def _fuzzy_extract_one(query, choices):
        r = fwprocess.extractOne(query, choices)
        return r if r else (None, 0)

try:
    from livekit.agents import function_tool
except ImportError:
    def function_tool(func):
        return func

try:
    import win32gui
    import win32con
except ImportError:
    win32gui = None
    win32con = None

try:
    import pygetwindow as gw
except ImportError:
    gw = None

sys.stdout.reconfigure(encoding='utf-8')
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Fast-path app shortcuts (spoken-name → command)
# ──────────────────────────────────────────────
APP_MAPPINGS = {
    "notepad": "notepad", "calculator": "calc", "calc": "calc",
    "chrome": "chrome", "google chrome": "chrome",
    "vlc": "vlc", "command prompt": "cmd", "cmd": "cmd",
    "terminal": "wt", "control panel": "control",
    "settings": "ms-settings:", "paint": "mspaint",
    "vs code": r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe",
    "code": r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe",
    "postman": r"%LOCALAPPDATA%\Postman\Postman.exe",
    "edge": "msedge", "firefox": "firefox", "spotify": "spotify",
    "word": "winword", "excel": "excel", "powerpoint": "powerpnt",
    "explorer": "explorer", "task manager": "taskmgr",
    "camera": "microsoft.windows.camera:",
    # v4 additions — UWP/protocol apps + common installs
    "discord": "discord:", "teams": "msteams:", "zoom": "zoom:",
    "whatsapp desktop": "whatsapp:", "slack": "slack:",
    "snipping tool": "ms-screenclip:", "wordpad": "write",
    "mail": "outlookmail:", "store": "ms-windows-store:",
    "notifications": "ms-actioncenter:", "clipboard history": "ms-clipboard:",
    "start menu": "ms-start:", "search": "ms-search:",
    "bluetooth settings": "ms-settings:bluetooth",
    "wifi settings": "ms-settings:network-wifi",
    "display settings": "ms-settings:display",
    "sound settings": "ms-settings:sound",
    "battery settings": "ms-settings:batterysaver",
    "apps installed": "ms-settings:appsfeatures",
    "uninstall app": "ms-settings:appsfeatures",
    "downloads": "%USERPROFILE%\\Downloads",
    "documents": "%USERPROFILE%\\Documents",
    "pictures": "%USERPROFILE%\\Pictures",
    "desktop": "%USERPROFILE%\\Desktop",
    "recycle bin": "shell:RecycleBinFolder",
    "startup folder": "shell:startup",
    "device manager": "devmgmt.msc",
    "disk management": "diskmgmt.msc",
    "services": "services.msc",
    "registry editor": "regedit",
    "network connections": "ncpa.cpl",
    "programs and features": "appwiz.cpl",
    "power options": "powercfg.cpl",
    "date and time": "timedate.cpl",
}

KNOWN_SITES = {
    "youtube": "https://www.youtube.com", "google": "https://www.google.com",
    "gmail": "https://mail.google.com", "facebook": "https://www.facebook.com",
    "instagram": "https://www.instagram.com", "twitter": "https://x.com",
    "x": "https://x.com", "whatsapp": "https://web.whatsapp.com",
    "linkedin": "https://www.linkedin.com", "netflix": "https://www.netflix.com",
    "amazon": "https://www.amazon.com", "github": "https://github.com",
    "wikipedia": "https://www.wikipedia.org", "reddit": "https://www.reddit.com",
    "tiktok": "https://www.tiktok.com", "chatgpt": "https://chatgpt.com",
}

# ──────────────────────────────────────────────
# App discovery — persistent JSON cache
# ──────────────────────────────────────────────
_APP_CACHE = None
_APP_CACHE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "jarvis_temp", "app_cache.json"
)


def _load_app_cache_from_disk():
    try:
        if os.path.exists(_APP_CACHE_FILE):
            age = time.time() - os.path.getmtime(_APP_CACHE_FILE)
            if age < 86400:  # 24h — re-scan once a day
                with open(_APP_CACHE_FILE, encoding="utf-8") as f:  # type: ignore[call-arg]
                    return json.load(f)
    except Exception:
        pass
    return None


def _save_app_cache_to_disk(cache: dict):
    try:
        os.makedirs(os.path.dirname(_APP_CACHE_FILE), exist_ok=True)
        with open(_APP_CACHE_FILE, "w", encoding="utf-8") as f:  # type: ignore[call-arg]
            json.dump(cache, f, ensure_ascii=False)
    except Exception:
        pass


def discover_apps():
    """Dynamically build name → executable map by scanning install dirs.
    Result is persisted to disk; re-scan only once per 24 hours."""
    global _APP_CACHE
    if _APP_CACHE is not None:
        return _APP_CACHE

    cached = _load_app_cache_from_disk()
    if cached:
        _APP_CACHE = cached
        return _APP_CACHE

    found = {}
    search_roots = []
    for base in [
        os.path.expandvars("%ProgramFiles%"),
        os.path.expandvars("%ProgramFiles(x86)%"),
        os.path.expandvars(r"%LOCALAPPDATA%\Programs"),
        os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs"),
        os.path.expandvars(r"%ProgramData%\Microsoft\Windows\Start Menu\Programs"),
    ]:
        if base and os.path.isdir(base):
            search_roots.append(base)

    for root in search_roots:
        for dirpath, _, files in os.walk(root):
            for f in files:
                lower = f.lower()
                if lower.endswith(".exe") or lower.endswith(".lnk"):
                    name = os.path.splitext(f)[0].lower()
                    path = os.path.join(dirpath, f)
                    if name not in found:
                        found[name] = path

    _APP_CACHE = found
    _save_app_cache_to_disk(found)
    return found


def resolve_app_command(query: str) -> str:
    """Resolve a spoken app name to a launchable command."""
    if query in APP_MAPPINGS:
        cmd = os.path.expandvars(APP_MAPPINGS[query])
        if os.path.isabs(cmd) and not os.path.exists(cmd):
            base = os.path.basename(cmd)
            which = shutil.which(base) or shutil.which(os.path.splitext(base)[0])
            return which or query
        return cmd

    apps = discover_apps()
    if query in apps:
        return apps[query]

    if apps:
        best, score = _fuzzy_extract_one(query, list(apps.keys()))
        if best and score >= 75:
            return apps[best]

    return query  # let Windows App Paths / PATH resolve it


# ──────────────────────────────────────────────
# Window focus — with retry
# ──────────────────────────────────────────────
async def focus_window(title_keyword: str) -> bool:
    title_keyword = (title_keyword or "").lower().strip()
    if not title_keyword:
        return False

    for attempt in range(3):  # 3 fast attempts, ~120ms apart
        if attempt > 0:
            await asyncio.sleep(0.12)

        if attempt == 0:
            await asyncio.sleep(0.1)  # small settle so a just-launched window appears

        if win32gui:
            found = []
            def enum_cb(hwnd, _):
                if win32gui.IsWindowVisible(hwnd):
                    if title_keyword in win32gui.GetWindowText(hwnd).lower():
                        found.append(hwnd)
                return True
            try:
                win32gui.EnumWindows(enum_cb, None)
            except Exception:
                pass
            if found:
                try:
                    win32gui.ShowWindow(found[0], win32con.SW_RESTORE)
                except Exception:
                    pass
                try:
                    win32gui.SetForegroundWindow(found[0])
                    return True
                except Exception:
                    pass

        if gw:
            for window in gw.getAllWindows():
                if title_keyword in window.title.lower():
                    try:
                        if window.isMinimized:
                            window.restore()
                        window.activate()
                        return True
                    except Exception:
                        pass

    return False


async def focus_browser(domain_hint: str = None) -> bool:
    hints = []
    if domain_hint:
        hints.append(domain_hint.lower().replace("www.", ""))
    hints += ["youtube", "chrome", "edge", "firefox"]
    seen = set()
    for hint in hints:
        if hint in seen:
            continue
        seen.add(hint)
        if await focus_window(hint):
            return True
    return False


# ──────────────────────────────────────────────
# File / folder index (shared across modules)
# ──────────────────────────────────────────────
def get_search_locations():
    locations = []
    user_home = os.path.expanduser("~")
    for name in ["Desktop", "Documents", "Downloads", "Pictures", "Music", "Videos", "OneDrive"]:
        p = os.path.join(user_home, name)
        if os.path.isdir(p):
            locations.append((p, 4))
    for d in string.ascii_uppercase:
        root = f"{d}:\\"
        if os.path.isdir(root):
            locations.append((root, 2))
    return locations


_INDEX_CACHE = {"items": None, "time": 0}
_INDEX_TTL   = 300  # 5 minutes (was 60s)


def _build_index_sync():
    item_index = []
    for base, max_depth in get_search_locations():
        try:
            for root, dirs, files in os.walk(base):
                depth = root[len(base):].count(os.sep)
                if depth > max_depth:
                    dirs[:] = []
                    continue
                for d in dirs:
                    item_index.append({"name": d, "path": os.path.join(root, d), "type": "folder"})
                for f in files:
                    item_index.append({"name": f, "path": os.path.join(root, f), "type": "file"})
        except Exception:
            continue
    return item_index


async def smart_index():
    now = time.time()
    if _INDEX_CACHE["items"] is not None and now - _INDEX_CACHE["time"] < _INDEX_TTL:
        return _INDEX_CACHE["items"]
    item_index = await asyncio.to_thread(_build_index_sync)
    _INDEX_CACHE["items"] = item_index
    _INDEX_CACHE["time"]  = now
    logger.info(f"✅ {len(item_index)} اشیاء انڈیکس ہو گئیں۔")
    return item_index


async def smart_search(query: str, prefer_type: str = None):
    index = await smart_index()
    if not index:
        return None

    def best_of(item_type):
        choices = [i["name"] for i in index if i["type"] == item_type]
        if not choices:
            return None, 0
        match, score = _fuzzy_extract_one(query, choices)
        return match, score

    if prefer_type == "folder":
        name, score = best_of("folder")
        target_type = "folder"
        if score < 60:
            name2, score2 = best_of("file")
            if score2 > score:
                name, score, target_type = name2, score2, "file"
    else:
        name, score = best_of("file")
        target_type = "file"
        if score < 60:
            name2, score2 = best_of("folder")
            if score2 > score:
                name, score, target_type = name2, score2, "folder"

    if score < 60:
        return None

    for item in index:
        if item["name"] == name and item["type"] == target_type:
            return item
    return None


# ──────────────────────────────────────────────
# File / folder actions
# ──────────────────────────────────────────────
async def open_folder(path):
    try:
        os.startfile(path) if os.name == "nt" else subprocess.call(["xdg-open", path])
        await focus_window(os.path.basename(path))
    except Exception as e:
        logger.error(f"فائل کھولنے میں خرابی: {e}")


async def play_file(path):
    try:
        os.startfile(path) if os.name == "nt" else subprocess.call(["xdg-open", path])
        await focus_window(os.path.basename(path))
    except Exception as e:
        logger.error(f"فائل چلانے میں خرابی: {e}")


async def create_folder(path):
    try:
        os.makedirs(path, exist_ok=True)
        return f"✅ فولڈر بن گیا: {path}"
    except Exception as e:
        return f"❌ فولڈر نہیں بن سکا: {e}"


async def rename_item(old_path, new_path):
    try:
        os.rename(old_path, new_path)
        return f"✅ نام بدل کر {new_path} کر دیا۔"
    except Exception as e:
        return f"❌ نام تبدیل ناکام: {e}"


async def delete_item(path):
    """Delete to Recycle Bin (safe, reversible) when send2trash is available."""
    try:
        try:
            from send2trash import send2trash
            await asyncio.to_thread(send2trash, path)
            return f"🗑️ Recycle Bin میں بھیج دیا: {path}"
        except ImportError:
            pass
        if os.path.isdir(path):
            os.rmdir(path)
        else:
            os.remove(path)
        return f"🗑️ حذف ہو گیا: {path}"
    except Exception as e:
        return f"❌ ڈیلیٹ ناکام: {e}"


# ──────────────────────────────────────────────
# Controlled-browser open helper
# ──────────────────────────────────────────────
async def _open_in_controlled_browser(url: str) -> str:
    try:
        from jarvis_browser import open_url
        return await open_url(url)
    except Exception:
        try:
            os.startfile(url)
            domain = url.split("//")[-1].split("/")[0] if "://" in url else None
            await (focus_browser(domain) if domain else focus_window(url))
            return f"🌐 براؤزر میں کھولا: {url}"
        except Exception as e:
            return f"❌ {url} نہیں کھل سکا: {e}"


# ──────────────────────────────────────────────
# Exported tools
# ──────────────────────────────────────────────
@function_tool
async def open(app_title: str) -> str:
    """کوئی ایپ، فائل راستہ، یا ویب سائٹ کھولیں ('chrome', 'notepad', 'youtube', URL)۔"""
    app_title = app_title.strip()
    lowered   = app_title.lower()

    if lowered.startswith(("http://", "https://", "www.")):
        url = app_title if "://" in app_title else "https://" + app_title
        return await _open_in_controlled_browser(url)

    if lowered in KNOWN_SITES:
        return await _open_in_controlled_browser(KNOWN_SITES[lowered])

    if os.path.exists(app_title):
        os.startfile(app_title)
        await focus_window(os.path.basename(app_title))
        return f"✅ کھولا: {app_title}"

    command = await asyncio.to_thread(resolve_app_command, lowered)

    if command.lower().startswith(("http", "ms-", "shell:", "file:")) or command.endswith(":"):
        try:
            os.startfile(command)
            domain = command.split("//")[-1].split("/")[0] if "://" in command else None
            await (focus_browser(domain) if domain else focus_window(command))
            return f"🚀 کھولا: {app_title}"
        except Exception as e:
            return f"❌ {app_title} نہیں کھل سکا: {e}"

    try:
        proc = await asyncio.create_subprocess_shell(f'start "" "{command}"')
        await proc.wait()
        if proc.returncode != 0:
            return f"❌ {app_title} لانچ ناکام (cmd: {command})"
        focused = await focus_window(lowered)
        return (f"🚀 {app_title} لانچ + فوکس ہے۔" if focused
                else f"🚀 {app_title} لانچ ہوئی (فوکس نہ ہو سکا)۔")
    except Exception as e:
        return f"❌ {app_title} لانچ ناکام: {e}"


@function_tool
async def close(window_title: str) -> str:
    """کسی کھلی ونڈو کو title سے بند کریں۔"""
    if not win32gui:
        return "❌ win32gui دستیاب نہیں"
    closed = []
    def handler(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            if window_title.lower() in win32gui.GetWindowText(hwnd).lower():
                try:
                    win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
                    closed.append(hwnd)
                except Exception:
                    pass
    win32gui.EnumWindows(handler, None)
    return (f"✅ بند کیا: {window_title}" if closed
            else f"❌ ونڈو نہیں ملی: {window_title}")


@function_tool
async def folder_file(command: str) -> str:
    """فائل/فولڈر آپریشنز: کھولیں، بنائیں (create folder X)، نام بدلیں (rename A to B)، حذف کریں (delete X)۔"""
    command = command.strip()
    lowered = command.lower()

    if "create folder" in lowered or "make folder" in lowered or "فولڈر بنائیں" in command:
        name = command
        for token in ["create folder", "make folder", "فولڈر بنائیں"]:
            name = name.replace(token, "", 1)
        name = name.strip(" :,-")
        if not name:
            return "❌ فولڈر کا نام نہیں دیا۔"
        path = os.path.join(os.path.expanduser("~/Desktop"), name)
        return await create_folder(path)

    if "rename" in lowered or "نام بدلیں" in command:
        rest = command
        for w in ["rename", "نام بدلیں"]:
            rest = rest.replace(w, "", 1)
        rest = rest.strip(" :,-")
        parts = None
        if re.search(r"\s+to\s+", rest, re.IGNORECASE):
            parts = re.split(r"\s+to\s+", rest, flags=re.IGNORECASE)
        elif " کو " in rest:
            parts = rest.split(" کو ")
        if parts and len(parts) == 2:
            old_name, new_name = parts[0].strip(), parts[1].strip()
            item = await smart_search(old_name)
            if item:
                new_path = os.path.join(os.path.dirname(item["path"]), new_name)
                return await rename_item(item["path"], new_path)
            return "❌ rename کے لیے آئٹم نہیں ملا۔"
        return "❌ rename کمانڈ درست نہیں ہے۔"

    if "delete" in lowered or "ڈیلیٹ" in command or "حذف" in command:
        q = command
        for w in ["delete file", "delete folder", "delete", "ڈیلیٹ", "حذف"]:
            q = q.replace(w, "", 1)
        q = q.strip(" :,-")
        item = await smart_search(q)
        if item:
            return await delete_item(item["path"])
        return "❌ ڈیلیٹ کے لیے آئٹم نہیں ملا۔"

    prefer = "folder" if "folder" in lowered else None
    item = await smart_search(command, prefer_type=prefer)
    if item:
        if item["type"] == "folder":
            await open_folder(item["path"])
            return f"✅ فولڈر کھولا: {item['name']}"
        await play_file(item["path"])
        return f"✅ فائل کھولی: {item['name']}"

    return "⚠ کچھ بھی match نہیں ہوا۔"


@function_tool
async def focus_window_tool(window_title: str) -> str:
    """کسی ایپ/ونڈو کو فوکس میں لائیں (mouse/keyboard input سے پہلے)۔"""
    ok = await focus_window(window_title)
    return (f"✅ فوکس: {window_title}" if ok
            else f"❌ فوکس ناکام: {window_title}")


@function_tool
async def focus_browser_tool(url_hint: str = "") -> str:
    """براؤزر ونڈو کو فوکس میں لائیں؛ url_hint سے بہتر match ہوگا۔"""
    ok = await focus_browser(url_hint)
    return "✅ براؤزر فوکس میں ہے۔" if ok else "❌ براؤزر فوکس ناکام۔"


@function_tool
async def get_active_window_tool() -> str:
    """اس وقت فوکس میں کون سی ونڈو ہے — معلوم کریں۔"""
    if win32gui:
        try:
            hwnd  = win32gui.GetForegroundWindow()
            title = win32gui.GetWindowText(hwnd)
            return f"🪟 فعال ونڈو: {title or '(بغیر عنوان)'}"
        except Exception:
            pass
    if gw:
        try:
            active = gw.getActiveWindow()
            return f"🪟 فعال ونڈو: {active.title if active else '(نہیں ملی)'}"
        except Exception:
            pass
    return "❌ فعال ونڈو معلوم نہیں ہو سکی۔"


@function_tool
async def minimize_window_tool(window_title: str) -> str:
    """کسی ونڈو کو minimize کریں۔"""
    if not win32gui:
        return "❌ win32gui دستیاب نہیں"
    found = []
    def enum_cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and window_title.lower() in win32gui.GetWindowText(hwnd).lower():
            found.append(hwnd)
        return True
    win32gui.EnumWindows(enum_cb, None)
    if found:
        try:
            win32gui.ShowWindow(found[0], win32con.SW_MINIMIZE)
            return f"✅ minimize کیا: {window_title}"
        except Exception as e:
            return f"❌ minimize ناکام: {e}"
    return f"❌ ونڈو نہیں ملی: {window_title}"


@function_tool
async def maximize_window_tool(window_title: str) -> str:
    """کسی ونڈو کو maximize کریں۔"""
    if not win32gui:
        return "❌ win32gui دستیاب نہیں"
    found = []
    def enum_cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and window_title.lower() in win32gui.GetWindowText(hwnd).lower():
            found.append(hwnd)
        return True
    win32gui.EnumWindows(enum_cb, None)
    if found:
        try:
            win32gui.ShowWindow(found[0], win32con.SW_MAXIMIZE)
            return f"✅ maximize کیا: {window_title}"
        except Exception as e:
            return f"❌ maximize ناکام: {e}"
    return f"❌ ونڈو نہیں ملی: {window_title}"
# ──────────────────────────────────────────────
# v4 PRO — window snapping / arrangement
# ──────────────────────────────────────────────
@function_tool
async def window_snap_tool(window_title: str, position: str) -> str:
    """ونڈو کو snap کریں: left, right, top, bottom, center, maximize, restore۔
    مثال: window_title='chrome', position='left' → آدھی اسکرین بائیں۔"""
    if not win32gui:
        return "❌ win32gui دستیاب نہیں"
    pos = (position or "").strip().lower()
    title = (window_title or "").lower().strip()

    def _find():
        found = []
        def cb(hwnd, _):
            if win32gui.IsWindowVisible(hwnd):
                if title in win32gui.GetWindowText(hwnd).lower():
                    found.append(hwnd)
            return True
        win32gui.EnumWindows(cb, None)
        return found

    hwnds = await asyncio.to_thread(_find)
    if not hwnds:
        return f"❌ ونڈو نہیں ملی: {window_title}"
    hwnd = hwnds[0]

    def _snap():
        work = win32gui.SystemParametersInfo(win32con.SPI_GETWORKAREA, 0)
        left, top, right, bottom = work
        w, h = right - left, bottom - top
        areas = {
            "left":     (left, top, w // 2, h),
            "right":    (left + w // 2, top, w // 2, h),
            "top":      (left, top, w, h // 2),
            "bottom":   (left, top + h // 2, w, h // 2),
            "top-left":     (left, top, w // 2, h // 2),
            "top-right":    (left + w // 2, top, w // 2, h // 2),
            "bottom-left":  (left, top + h // 2, w // 2, h // 2),
            "bottom-right": (left + w // 2, top + h // 2, w // 2, h // 2),
            "center":   (left + w // 4, top + h // 4, w // 2, h // 2),
        }
        if pos == "maximize":
            win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
        elif pos == "restore":
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        elif pos in areas:
            x, y, ww, hh = areas[pos]
            try:
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            except Exception:
                pass
            win32gui.SetWindowPos(hwnd, 0, x, y, ww, hh, 0x0004)  # SWP_NOZORDER
        else:
            raise ValueError(f"نامعلوم position: {pos}")

    try:
        await asyncio.to_thread(_snap)
        return f"🪟 '{window_title}' → {pos}"
    except Exception as e:
        return f"❌ snap ناکام: {e}"
