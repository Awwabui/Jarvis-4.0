# ============================================================================
# Browser-automation layer (Playwright) — v4 PRO
#
# Key upgrades vs v3:
#  - NATIVE ACCOUNT CONTROL: attaches to the user's REAL Chrome/Edge over CDP
#    (remote debugging port) → logged-in Gmail/YouTube/etc. work out of the box.
#    Chain: CDP attach → real profile (if browser closed) → fallback profile.
#  - browser_restart_native: relaunches Chrome with the debug port + real
#    profile (used when Chrome is already open without the port).
#  - browser_run_js: run arbitrary JavaScript on the page (pro power tool).
#  - browser_close is CDP-safe: only disconnects, never kills the user's browser.
#  - NAV_TIMEOUT 12s, fast blank-recovery kept from v3.
# ============================================================================
import asyncio
import logging
import os
import re
import subprocess
import time

from livekit.agents import function_tool

logger = logging.getLogger(__name__)

try:
    from playwright.async_api import async_playwright
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    async_playwright = None
    _PLAYWRIGHT_AVAILABLE = False

# ──────────────────────────────────────────────
# Site aliases
# ──────────────────────────────────────────────
_SITE_ALIASES = {
    "youtube": "https://www.youtube.com",
    "google": "https://www.google.com",
    "gmail": "https://mail.google.com",
    "mail": "https://mail.google.com",
    "maps": "https://maps.google.com",
    "google maps": "https://maps.google.com",
    "drive": "https://drive.google.com",
    "google drive": "https://drive.google.com",
    "facebook": "https://www.facebook.com",
    "instagram": "https://www.instagram.com",
    "twitter": "https://x.com",
    "x": "https://x.com",
    "whatsapp": "https://web.whatsapp.com",
    "linkedin": "https://www.linkedin.com",
    "netflix": "https://www.netflix.com",
    "amazon": "https://www.amazon.com",
    "github": "https://github.com",
    "stackoverflow": "https://stackoverflow.com",
    "stack overflow": "https://stackoverflow.com",
    "wikipedia": "https://www.wikipedia.org",
    "reddit": "https://www.reddit.com",
    "tiktok": "https://www.tiktok.com",
    "chatgpt": "https://chatgpt.com",
    "gemini": "https://gemini.google.com",
    "twitch": "https://www.twitch.tv",
    "pinterest": "https://www.pinterest.com",
    "yahoo": "https://www.yahoo.com",
    "bing": "https://www.bing.com",
}

_state = {
    "pw": None, "browser": None, "context": None, "pages": [],
    "active": 0, "dead": False, "profile": None, "mode": None,
}
_lock       = asyncio.Lock()
_NAV_TIMEOUT = 12000   # ms
_CDP_PORT    = int(os.getenv("JARVIS_CDP_PORT", "9222") or 9222)
_ARGS = [
    "--start-maximized",
    "--disable-blink-features=AutomationControlled",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-features=Translate",
]
_PROC_CACHE = {"time": 0.0, "running": set()}


# ──────────────────────────────────────────────
# URL helpers
# ──────────────────────────────────────────────
def normalize_url(target: str) -> str:
    t = (target or "").strip()
    if not t:
        return "https://www.google.com"
    if t.lower().startswith(("http://", "https://")):
        return t
    host = re.sub(r"^www\.", "", t.lower().split("/")[0])
    base = host.split(".")[0]
    if base in _SITE_ALIASES:
        url  = _SITE_ALIASES[base]
        rest = t.split("/", 1)[1] if "/" in t else ""
        return f"{url}/{rest}" if rest else url
    if "." in host:
        return "https://" + t
    return f"https://www.{host}.com"


# ──────────────────────────────────────────────
# Process / profile helpers
# ──────────────────────────────────────────────
async def _running_processes() -> set:
    now = time.time()
    if now - _PROC_CACHE["time"] < 5 and _PROC_CACHE["running"]:
        return _PROC_CACHE["running"]
    running = set()
    try:
        proc = await asyncio.create_subprocess_shell(
            "tasklist /FO CSV /NH",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        for line in out.decode(errors="ignore").splitlines():
            parts = line.split('"')
            if len(parts) >= 2 and parts[1]:
                running.add(parts[1].lower())
    except Exception:
        pass
    _PROC_CACHE["time"]    = now
    _PROC_CACHE["running"] = running
    return running


def _real_profiles():
    return [
        ("chrome.exe",  os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data")),
        ("msedge.exe",  os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\User Data")),
        ("brave.exe",   os.path.expandvars(r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\User Data")),
    ]


async def _profiles_to_try():
    out     = []
    ov      = (os.getenv("BROWSER_PROFILE_DIR") or "").strip()
    if ov:
        out.append((os.path.expandvars(ov), "override"))
    running = await _running_processes()
    for proc_name, path in _real_profiles():
        if path and os.path.isdir(path) and proc_name not in running:
            out.append((path, proc_name))
    fallback = os.path.join(os.path.expandvars("%LOCALAPPDATA%"), "jarvis_browser_profile")
    out.append((fallback, "fallback"))
    return out


def _channels_for(label: str):
    if label == "chrome.exe":  return ("chrome", None)
    if label == "msedge.exe":  return ("msedge", None)
    if label == "fallback":    return ("chrome", None)
    return (None,)


# ──────────────────────────────────────────────
# Browser state management
# ──────────────────────────────────────────────
def _track_page(page):
    def _on_close(p):
        try:
            if p in _state["pages"]:
                i = _state["pages"].index(p)
                _state["pages"].pop(i)
                _state["active"] = max(0, min(_state["active"], len(_state["pages"]) - 1))
        except Exception:
            pass
    try:
        page.on("close", _on_close)
    except Exception:
        pass


def _mark_dead(_ctx=None):
    _state["dead"] = True


# ──────────────────────────────────────────────
# CDP attach — control the user's REAL browser (native account)
# ──────────────────────────────────────────────
async def _try_cdp_attach():
    """Attach to an already-running Chrome/Edge with --remote-debugging-port.
    This controls the user's actual browser WITH their logged-in profile.
    Returns a live page or None."""
    if os.getenv("JARVIS_CDP", "1").strip() == "0":
        return None
    try:
        if _state["pw"] is None:
            _state["pw"] = await async_playwright().start()
    except Exception:
        return None
    url = f"http://127.0.0.1:{_CDP_PORT}"
    try:
        browser = await asyncio.wait_for(
            _state["pw"].chromium.connect_over_cdp(url, timeout=2500), timeout=6
        )
    except Exception:
        return None

    context = browser.contexts[0] if browser.contexts else await browser.new_context()
    pages = [p for p in context.pages if not p.is_closed()]
    if not pages:
        try:
            pages = [await context.new_page()]
        except Exception:
            return None
    for p in pages:
        _track_page(p)
    _state.update({
        "browser": browser, "context": context, "pages": pages,
        "active": len(pages) - 1, "dead": False, "profile": "(native CDP)",
        "mode": "cdp",
    })
    try:
        browser.on("disconnected", _mark_dead)
    except Exception:
        pass
    logger.info("✅ CDP attach (native browser, port %s) — %s tab(s)", _CDP_PORT, len(pages))
    return pages[_state["active"]]


def _find_browser_exe(chrome_first=True):
    """Locate a real Chrome/Edge executable for native relaunch."""
    candidates = []
    if chrome_first:
        candidates += [
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        ]
    candidates += [
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
    ]
    if not chrome_first:
        candidates = candidates[3:] + candidates[:3]
    for p in candidates:
        if p and os.path.exists(p):
            return p
    return None


async def close_browser():
    """Close browser state. CDP-safe: in CDP mode we only disconnect —
    the user's real browser stays open."""
    mode = _state.get("mode")
    if mode != "cdp":
        for p in list(_state["pages"]):
            try:
                await p.close()
            except Exception:
                pass
    try:
        if _state["browser"]:
            await _state["browser"].close()  # disconnect for CDP; close for owned
    except Exception:
        pass
    try:
        if _state["context"] and mode != "cdp":
            await _state["context"].close()
    except Exception:
        pass
    try:
        if _state["pw"]:
            await _state["pw"].stop()
    except Exception:
        pass
    _state.update({"pw": None, "browser": None, "context": None, "pages": [],
                   "active": 0, "dead": False, "profile": None, "mode": None})


async def _close_browser():
    await close_browser()


async def _healthy_active_page():
    if _state["dead"] or not _state["context"]:
        return None
    try:
        live = [p for p in _state["context"].pages if not p.is_closed()]
    except Exception:
        return None
    for p in list(_state["pages"]):
        if p not in live:
            try:
                _state["pages"].remove(p)
            except ValueError:
                pass
    for p in live:
        if p not in _state["pages"]:
            _track_page(p)
            _state["pages"].append(p)
    if not _state["pages"]:
        try:
            page = await _state["context"].new_page()
            _track_page(page)
            _state["pages"] = [page]
        except Exception:
            return None
    _state["active"] = max(0, min(_state["active"], len(_state["pages"]) - 1))
    page = _state["pages"][_state["active"]]
    return page if not page.is_closed() else None


async def _ensure_browser():
    if not _PLAYWRIGHT_AVAILABLE:
        raise RuntimeError(
            "Playwright موجود نہیں۔ نصب کریں:\n"
            "pip install playwright\nplaywright install chromium"
        )
    async with _lock:
        page = await _healthy_active_page()
        if page:
            return page

        await _close_browser()

        # ── 1) BEST: attach to the user's real browser (native account) ──
        page = await _try_cdp_attach()
        if page:
            return page

        # ── 2) Launch with the real/fallback profile ──
        last_err = None
        for profile, label in await _profiles_to_try():
            try:
                os.makedirs(profile, exist_ok=True)
            except Exception:
                continue
            for channel in _channels_for(label):
                variant = {"channel": channel} if channel else {}
                try:
                    _state["pw"] = await async_playwright().start()
                    _state["context"] = await _state["pw"].chromium.launch_persistent_context(
                        user_data_dir=profile,
                        headless=os.getenv("JARVIS_BROWSER_HEADLESS", "") == "1",
                        args=list(_ARGS),
                        no_viewport=True,
                        timeout=20000,
                        ignore_default_args=["--enable-automation"],
                        **variant,
                    )
                    _state["profile"] = profile
                    _state["mode"] = "fallback" if label == "fallback" else "profile"
                    try:
                        _state["context"].on("close", _mark_dead)
                    except Exception:
                        pass
                    pages = [p for p in _state["context"].pages if not p.is_closed()]
                    if not pages:
                        pages = [await _state["context"].new_page()]
                    for p in pages:
                        _track_page(p)
                    _state["pages"]  = pages
                    _state["active"] = len(pages) - 1
                    logger.info("✅ براؤزر لانچ (profile=%s, %s)", label, variant or "bundled")
                    return pages[_state["active"]]
                except Exception as e:
                    last_err = e
                    await _close_browser()
        raise RuntimeError(f"براؤزر شروع ناکام: {last_err}")


async def warmup():
    try:
        # _ensure_browser tries CDP attach (native browser) first — zero launch cost
        await _ensure_browser()
        page = _active_page()
        if page and page.url in ("about:blank", ""):
            try:
                await page.goto("https://www.google.com",
                                wait_until="domcontentloaded", timeout=15000)
            except Exception:
                pass
        logger.info("✅ براؤزر pre-warm مکمل۔")
    except Exception as e:
        logger.warning(f"براؤزر pre-warm ناکام (بعد میں retry): {e}")


def _active_page():
    return _state["pages"][_state["active"]] if _state["pages"] else None


async def _focus_os_window(url: str):
    try:
        from Jarvis_window_CTRL import focus_browser
        domain = url.split("//").pop().split("/")[0].replace("www.", "")
        await focus_browser(domain)
    except Exception:
        pass


# ──────────────────────────────────────────────
# Navigation helpers
# ──────────────────────────────────────────────
async def _goto(page, url: str):
    """Fast, resilient navigation. Returns error string or None."""
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT)
    except Exception as e:
        if not await _render_ok(page):
            return f"نیویگیشن ناکام {url}: {e}"
    try:
        await page.bring_to_front()
    except Exception:
        pass
    return None


async def _render_ok(page) -> bool:
    try:
        return await page.evaluate(
            "() => { const t = (document.body && document.body.innerText || '').trim();"
            " const h = document.documentElement ? document.documentElement.innerHTML.length : 0;"
            " return t.length > 0 || h > 50000; }"
        )
    except Exception:
        return False


async def _recover_if_blank(page) -> bool:
    """Poll 3× for SPA rendering (0.9s max), then one reload. Returns True if still blank."""
    for _ in range(3):          # was 5 × 0.5s = 2.5s; now 3 × 0.3s = 0.9s
        if await _render_ok(page):
            return False
        await asyncio.sleep(0.3)
    try:
        await page.reload(wait_until="domcontentloaded", timeout=_NAV_TIMEOUT)
    except Exception:
        pass
    for _ in range(3):          # was 4 × 0.5s; now 3 × 0.3s
        if await _render_ok(page):
            return False
        await asyncio.sleep(0.3)
    return True


async def _new_page(url=None):
    page = await _state["context"].new_page()
    _track_page(page)
    if url:
        err = await _goto(page, url)
        if err:
            try:
                await page.close()
            except Exception:
                pass
            raise RuntimeError(err)
    _state["pages"].append(page)
    _state["active"] = len(_state["pages"]) - 1
    return page


async def open_url(url: str) -> str:
    page   = await _ensure_browser()
    target = normalize_url(url)
    err    = await _goto(page, target)
    if err:
        return f"❌ {err}"
    if "youtube.com" in page.url:
        try:
            await page.wait_for_selector("ytd-app", timeout=6000)
        except Exception:
            pass
    blank = await _recover_if_blank(page)
    await _focus_os_window(page.url)
    title = (await page.title()).strip() or "(blank)"
    warn  = "\n⚠ صفحہ blank ہے — دوبارہ کہیں تو reload کروں گا۔" if blank else ""
    return f"🌐 کھلا:\n🔗 {page.url}\n📄 {title}{warn}"


# ──────────────────────────────────────────────
# ── Tools ────────────────────────────────────
# ──────────────────────────────────────────────

@function_tool
async def browser_open(url: str) -> str:
    """کسی ویب سائٹ یا URL کو براؤزر میں کھولیں۔ سائٹ کا نام کافی ہے ('youtube', 'gmail')۔"""
    return await open_url(url)


@function_tool
async def browser_get_current_url() -> str:
    """موجودہ browser tab کا URL اور title بتائیں۔"""
    await _ensure_browser()
    page = _active_page()
    if not page:
        return "❌ کوئی tab نہیں کھلا۔"
    try:
        title = (await page.title()).strip() or "(blank)"
        return f"🔗 URL: {page.url}\n📄 Title: {title}"
    except Exception as e:
        return f"❌ URL نہیں ملا: {e}"


@function_tool
async def browser_get_page_summary() -> str:
    """موجودہ صفحے کا تیز خلاصہ — title، meta description، اور پہلے 500 حروف۔"""
    await _ensure_browser()
    page = _active_page()
    try:
        info = await page.evaluate("""() => {
            const title = document.title || '';
            const meta  = (document.querySelector('meta[name="description"]') || {}).content || '';
            const text  = (document.body ? document.body.innerText : '').replace(/\\s+/g,' ').trim().slice(0,500);
            return {title, meta, text};
        }""")
        return (
            f"📄 Title: {info['title']}\n"
            f"📝 Description: {info['meta'] or '(none)'}\n"
            f"📰 Preview: {info['text']}"
        )
    except Exception as e:
        return f"❌ خلاصہ نہیں ملا: {e}"


@function_tool
async def browser_google_search(query: str) -> str:
    """براؤزر میں Google تلاش کریں اور top نتائج دکھائیں۔"""
    await _ensure_browser()
    page = _active_page()
    q    = query.replace(" ", "+")
    err  = await _goto(page, f"https://www.google.com/search?q={q}")
    if err:
        return f"❌ {err}"
    try:
        await page.wait_for_selector("a h3", timeout=5000)
    except Exception:
        pass
    try:
        items = await page.evaluate(
            """() => Array.from(document.querySelectorAll('a h3')).slice(0,5).map(h => {
                const a = h.closest('a'); return a ? (h.innerText.trim() + '\\n' + a.href) : '';
            })"""
        )
    except Exception:
        items = []
    items = [i for i in items if i]
    await _focus_os_window(page.url)
    if not items:
        return ("🔎 نتائج نہیں ملے (شاید bot-check)۔ google_search ٹول آزمائیں یا browser_refresh کریں۔")
    return "🔎 Google نتائج:\n" + "\n\n".join(items)


@function_tool
async def browser_search_youtube(query: str) -> str:
    """YouTube پر تلاش کریں اور top ویڈیوز دکھائیں۔"""
    await _ensure_browser()
    page = _active_page()
    err  = await _goto(page, "https://www.youtube.com")
    if err:
        return f"❌ {err}"
    try:
        await page.wait_for_selector("input#search", timeout=10000)
    except Exception:
        return "❌ YouTube search باکس نہیں ملا۔"
    await page.fill("input#search", query)
    await page.keyboard.press("Enter")
    try:
        await page.wait_for_selector("ytd-video-renderer", timeout=10000)
    except Exception:
        return f"📺 '{query}' کے لیے کوئی نتیجہ نہیں ملا۔"
    results = []
    items   = await page.query_selector_all("ytd-video-renderer")
    for it in items[:5]:
        title_el = await it.query_selector("#video-title")
        if not title_el:
            continue
        title = (await title_el.inner_text()).strip()
        href  = await title_el.get_attribute("href") or ""
        results.append(f"• {title}\n  {href}")
    await _focus_os_window(page.url)
    return "📺 YouTube نتائج:\n" + ("\n".join(results) if results else "(کوئی نتیجہ نہیں)")


@function_tool
async def browser_play_youtube(query: str) -> str:
    """YouTube پر query تلاش کر کے پہلی ویڈیو چلائیں۔"""
    await _ensure_browser()
    page = _active_page()
    q    = query.replace(" ", "+")
    err  = await _goto(page, f"https://www.youtube.com/results?search_query={q}")
    if err:
        return f"❌ {err}"
    try:
        first = page.locator("ytd-video-renderer a#video-title").first
        await first.wait_for(timeout=10000)
        await first.click()
    except Exception:
        return f"❌ '{query}' کی ویڈیو نہیں ملی۔"
    # Wait for video element — more reliable than URL change
    try:
        await page.wait_for_selector("video", timeout=15000)
    except Exception:
        try:
            await page.wait_for_url("**/watch**", timeout=10000)
        except Exception:
            pass
    await _focus_os_window(page.url)
    return f"▶ چل رہا ہے:\n📄 {await page.title()}\n🔗 {page.url}"


@function_tool
async def browser_click_text(text: str) -> str:
    """صفحے پر کسی visible متن پر کلک کریں۔"""
    await _ensure_browser()
    page = _active_page()
    try:
        await page.get_by_text(text, exact=False).first.click(timeout=8000)
        return f"✅ کلک: '{text}'"
    except Exception as e:
        return f"❌ کلک ناکام '{text}': {e}"


@function_tool
async def browser_click_selector(selector: str) -> str:
    """CSS selector پر کلک کریں۔"""
    await _ensure_browser()
    page = _active_page()
    try:
        await page.locator(selector).first.click(timeout=8000)
        return f"✅ selector کلک: {selector}"
    except Exception as e:
        return f"❌ selector کلک ناکام {selector}: {e}"


@function_tool
async def browser_find_and_click(description: str) -> str:
    """قدرتی زبان میں element تلاش کر کے کلک کریں (button، link، text match)۔"""
    await _ensure_browser()
    page = _active_page()
    # Try ARIA roles first (button, link), then text match
    for locator in [
        page.get_by_role("button", name=description),
        page.get_by_role("link",   name=description),
        page.get_by_text(description, exact=False),
        page.locator(f"[aria-label*='{description}']"),
        page.locator(f"[title*='{description}']"),
    ]:
        try:
            await locator.first.click(timeout=3000)
            return f"✅ کلک کیا: '{description}'"
        except Exception:
            continue
    return f"❌ '{description}' نہیں ملا — selector یا exact text آزمائیں۔"


@function_tool
async def browser_type(selector: str, text: str) -> str:
    """کسی input/form field میں text بھریں۔"""
    await _ensure_browser()
    page = _active_page()
    try:
        await page.fill(selector, text)
        return f"✅ بھرا '{text}' ← {selector}"
    except Exception as e:
        return f"❌ fill ناکام: {e}"


@function_tool
async def browser_fill_and_submit(selector: str, text: str) -> str:
    """Form field بھریں اور فوراً Enter دبائیں — search یا login کے لیے مفید۔"""
    await _ensure_browser()
    page = _active_page()
    try:
        await page.fill(selector, text)
        await page.keyboard.press("Enter")
        await asyncio.sleep(0.5)  # brief settle
        return f"✅ بھرا + Enter: '{text}' ← {selector}"
    except Exception as e:
        return f"❌ fill_and_submit ناکام: {e}"


@function_tool
async def browser_press_key(key: str) -> str:
    """براؤزر میں key دبائیں (Enter, Escape, ArrowDown وغیرہ)۔"""
    await _ensure_browser()
    page = _active_page()
    try:
        await page.keyboard.press(key)
        return f"✅ key دبی: {key}"
    except Exception as e:
        return f"❌ key ناکام {key}: {e}"


@function_tool
async def browser_scroll(direction: str, amount: int = 500) -> str:
    """صفحہ اسکرول کریں (up / down)۔"""
    await _ensure_browser()
    page = _active_page()
    dy   = amount if direction.lower() == "down" else -amount
    await page.mouse.wheel(0, dy)
    return f"📜 اسکرول {direction} ({amount}px)"


@function_tool
async def browser_scroll_to_bottom() -> str:
    """صفحے کے بالکل نیچے اسکرول کریں۔"""
    await _ensure_browser()
    page = _active_page()
    await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
    await page.wait_for_timeout(400)
    return "📜 نیچے اسکرول مکمل۔"


@function_tool
async def browser_wait(ms: int = 1000) -> str:
    """مخصوص milliseconds انتظار کریں (dynamic content لوڈ کے لیے)۔"""
    await asyncio.sleep(max(0, ms) / 1000)
    return f"⏳ {ms}ms انتظار مکمل۔"


@function_tool
async def browser_get_text(max_chars: int = 6000) -> str:
    """صفحے کا readable متن حاصل کریں (nav/header/footer noise کم کیا گیا)۔"""
    await _ensure_browser()
    page = _active_page()
    try:
        # Priority: main content areas first, fall back to full body
        text = await page.evaluate("""() => {
            const sel = ['main', 'article', '[role="main"]', '#content', '.content', 'body'];
            for (const s of sel) {
                const el = document.querySelector(s);
                if (el) {
                    const t = el.innerText.replace(/\\s+/g,' ').trim();
                    if (t.length > 200) return t;
                }
            }
            return (document.body ? document.body.innerText : '').replace(/\\s+/g,' ').trim();
        }""")
    except Exception as e:
        return f"❌ متن نہیں ملا: {e}"
    text = text.strip()
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n… [{len(text) - max_chars} مزید حروف]"
    return f"📄 صفحہ ({page.url}):\n{text}"


@function_tool
async def browser_extract_links() -> str:
    """صفحے کے تمام links نکالیں۔"""
    await _ensure_browser()
    page = _active_page()
    try:
        links = await page.evaluate(
            """() => Array.from(document.querySelectorAll('a[href]'))
                .map(a => (a.innerText.trim().slice(0,60) + ' | ' + a.href))
                .filter(t => t.includes('http'))"""
        )
    except Exception as e:
        return f"❌ links نہیں ملے: {e}"
    seen, out = set(), []
    for l in links:
        if l not in seen:
            seen.add(l)
            out.append("• " + l)
        if len(out) >= 25:
            break
    return "🔗 Links:\n" + ("\n".join(out) if out else "(کوئی link نہیں)")


@function_tool
async def browser_screenshot(path: str = "") -> str:
    """موجودہ tab کا screenshot لیں۔"""
    await _ensure_browser()
    page = _active_page()
    if not path:
        try:
            from jarvis_temp import temp_path
            path = temp_path(f"screenshot_{int(time.time())}.png")
        except Exception:
            path = os.path.join(os.getcwd(), "jarvis_screenshot.png")
    try:
        await page.screenshot(path=path)
        return f"📸 Screenshot: {path}"
    except Exception as e:
        return f"❌ Screenshot ناکام: {e}"


@function_tool
async def browser_get_info() -> str:
    """موجودہ tab کا URL، title اور مختصر preview۔"""
    await _ensure_browser()
    page  = _active_page()
    title = await page.title()
    try:
        text = await page.evaluate(
            "() => document.body.innerText.replace(/\\s+/g,' ').slice(0,600)"
        )
    except Exception:
        text = ""
    return f"🔗 {page.url}\n📄 {title}\n📝 {text}"


@function_tool
async def browser_new_tab(url: str = "") -> str:
    """نیا tab کھولیں (اختیاری: URL دیں)۔"""
    await _ensure_browser()
    target = normalize_url(url) if url else None
    try:
        page = await _new_page(target)
    except Exception as e:
        return f"❌ نیا tab نہیں کھلا: {e}"
    try:
        await page.bring_to_front()
    except Exception:
        pass
    await _focus_os_window(page.url)
    idx   = len(_state["pages"]) - 1
    title = (await page.title()).strip() or "(blank)"
    return f"🗂️ نیا tab #{idx}:\n🔗 {page.url}\n📄 {title}"


@function_tool
async def browser_list_tabs() -> str:
    """تمام کھلے tabs کی فہرست دکھائیں۔"""
    await _ensure_browser()
    lines = []
    for i, p in enumerate(_state["pages"]):
        try:
            t = await p.title()
        except Exception:
            t = "?"
        mark = "▶" if i == _state["active"] else "  "
        lines.append(f"{mark} [{i}] {t}")
    return "🗂️ Tabs:\n" + "\n".join(lines)


@function_tool
async def browser_switch_tab(index: int) -> str:
    """tab نمبر پر switch کریں۔"""
    await _ensure_browser()
    if 0 <= index < len(_state["pages"]):
        _state["active"] = index
        try:
            await _state["pages"][index].bring_to_front()
        except Exception:
            pass
        return f"✅ Tab #{index} پر switch کیا۔"
    return f"❌ Tab index غلط: {index}"


@function_tool
async def browser_close_tab(index: int = -1) -> str:
    """موجودہ یا مخصوص tab بند کریں۔"""
    await _ensure_browser()
    if not _state["pages"]:
        return "❌ کوئی tab نہیں۔"
    idx = index if index >= 0 else _state["active"]
    if not (0 <= idx < len(_state["pages"])):
        return f"❌ Tab index غلط: {idx}"
    try:
        await _state["pages"][idx].close()
    except Exception:
        pass
    if idx < len(_state["pages"]):
        _state["pages"].pop(idx)
    _state["active"] = max(0, min(_state["active"], len(_state["pages"]) - 1))
    return f"✅ Tab #{idx} بند کیا۔ (باقی: {len(_state['pages'])})"


@function_tool
async def browser_go_back() -> str:
    """براؤزر میں پچھلے صفحے پر واپس جائیں۔"""
    await _ensure_browser()
    page = _active_page()
    await page.go_back()
    return f"⬅ واپس: {page.url}"


@function_tool
async def browser_refresh() -> str:
    """موجودہ tab reload کریں (blank page ٹھیک کرنے کے لیے)۔"""
    await _ensure_browser()
    page = _active_page()
    try:
        await page.reload(wait_until="domcontentloaded", timeout=_NAV_TIMEOUT)
    except Exception as e:
        return f"❌ Refresh ناکام: {e}"
    try:
        await page.bring_to_front()
    except Exception:
        pass
    return f"🔄 Refresh مکمل:\n🔗 {page.url}\n📄 {(await page.title()).strip() or '(blank)'}"


@function_tool
async def browser_research(query: str, results: int = 3) -> str:
    """خودکار تحقیق: Google کریں، top صفحات tabs میں کھولیں (parallel)، متن پڑھ کر دیں۔"""
    results = max(1, min(5, int(results)))
    await _ensure_browser()
    page = _active_page()
    q    = query.replace(" ", "+")
    err  = await _goto(page, f"https://www.google.com/search?q={q}")
    if err:
        return f"❌ {err}"
    try:
        await page.wait_for_selector("a h3", timeout=8000)
    except Exception:
        pass
    try:
        hrefs = await page.evaluate(
            """() => Array.from(document.querySelectorAll('a h3')).slice(0, 8)
                .map(h => { const a = h.closest('a'); return a ? a.href : ''; })
                .filter(h => h.startsWith('http') && !h.includes('google.com'))"""
        )
    except Exception:
        hrefs = []
    hrefs = hrefs[:max(1, results)]

    # Semaphore: open at most 2 tabs concurrently (prevents _lock congestion)
    sem = asyncio.Semaphore(2)

    async def _read_one(i, href):
        async with sem:
            try:
                tp    = await _new_page(href)
                try:
                    await tp.bring_to_front()
                except Exception:
                    pass
                title = (await tp.title()).strip() or "(blank)"
                text  = await tp.evaluate(
                    """() => {
                        const sel = ['main','article','[role="main"]','#content','.content','body'];
                        for (const s of sel) {
                            const el = document.querySelector(s);
                            if (el) { const t = el.innerText.replace(/\\s+/g,' ').trim(); if(t.length>200) return t; }
                        }
                        return (document.body ? document.body.innerText : '').replace(/\\s+/g,' ').trim();
                    }"""
                )
                text = text.strip()[:2500]
                return f"[{i}] {title}\n{text}"
            except Exception as e:
                return f"[{i}] (خطا: {e})"

    collected = await asyncio.gather(*[_read_one(i, h) for i, h in enumerate(hrefs, 1)])
    summary   = "\n\n— — — — —\n\n".join(collected)
    try:
        from jarvis_temp import temp_path
        out_path = temp_path(f"research_{int(time.time())}.txt")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(f"Research: {query}\n\n{summary}")
        return (
            f"🧠 تحقیق: '{query}'\n📄 فائل: {out_path}\n\n"
            f"📝 خلاصہ:\n{summary[:4000]}"
        )
    except Exception:
        return f"🧠 تحقیق: '{query}'\n\n{summary[:9000]}"


@function_tool
async def browser_close() -> str:
    """Jarvis کا براؤزر کنٹرول بند کریں۔ (native CDP mode میں آپ کا browser کھلا رہے گا —
    صرف کنٹرول disconnect ہوگا۔)"""
    await _close_browser()
    return "✅ براؤزر کنٹرول بند کر دیا۔"
# ──────────────────────────────────────────────
# v4 PRO tools — native relaunch + JS execution
# ──────────────────────────────────────────────
@function_tool
async def browser_restart_native(browser: str = "chrome") -> str:
    """آپ کے اصلی Chrome/Edge کو (native profile کے ساتھ) automation port کے ساتھ
    دوبارہ لانچ کریں تاکہ Jarvis logged-in account کنٹرول کر سکے۔
    browser='chrome' یا 'edge'۔ نوٹ: کھلے Chrome کی windows بند ہو جائیں گی۔"""
    exe = _find_browser_exe(chrome_first=(browser.lower() != "edge"))
    if not exe:
        return "❌ Chrome/Edge executable نہیں ملا۔"
    proc_name = "chrome.exe" if "chrome" in exe.lower() else "msedge.exe"
    running = await _running_processes()
    if proc_name in running:
        # Graceful close (WM_CLOSE, no /F) so sessions save properly
        killer = await asyncio.create_subprocess_shell(
            f'taskkill /IM {proc_name}',
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        try:
            await asyncio.wait_for(killer.communicate(), timeout=8)
        except Exception:
            pass
        await asyncio.sleep(1.5)
    try:
        subprocess.Popen(
            [exe, f"--remote-debugging-port={_CDP_PORT}"],
            cwd=os.path.dirname(exe),
        )
    except Exception as e:
        return f"❌ براؤزر لانچ ناکام: {e}"
    # Wait for the debug port to come up
    for _ in range(10):
        await asyncio.sleep(0.6)
        page = await _try_cdp_attach()
        if page:
            title = (await page.title()).strip() or "(blank)"
            return (f"✅ Native browser کنٹرول میں ہے (port {_CDP_PORT})۔\n"
                    f"🔗 {page.url}\n📄 {title}\n"
                    "ℹ️ آئندہ ہر بار اسی port کے ساتھ Chrome کھولیں "
                    "(desktop پر 'Jarvis Chrome' shortcut بنائیں: python setup_jarvis_chrome.py)")
    return ("⚠ براؤزر لانچ ہو گیا مگر debug port دستیاب نہ ہو سکا۔ "
            "دوبارہ کوشش کریں یا setup_jarvis_chrome.py چلائیں۔")


@function_tool
async def browser_run_js(expression: str) -> str:
    """موجودہ صفحے پر JavaScript چلائیں (JSON-serializable result واپس)۔
    مثال: document.title یا Array.from(document.querySelectorAll('h2')).map(h=>h.innerText)"""
    await _ensure_browser()
    page = _active_page()
    try:
        result = await asyncio.wait_for(page.evaluate(expression), timeout=10)
        shown = str(result)
        if len(shown) > 3000:
            shown = shown[:3000] + " …"
        return f"🧮 JS نتیجہ:\n{shown}"
    except Exception as e:
        return f"❌ JS ناکام: {e}"

