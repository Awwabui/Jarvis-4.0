# ============================================================================
# jarvis_ui.py — Jarvis v4.1 PRECISION layer (Windows UI Automation)
#
# Replaces fragile coordinate clicking with reliable UI Automation (UIA):
#   • ui_list_windows_tool     — top-level windows (title / type / pid)
#   • ui_list_controls_tool    — all controls of a window (click targets!)
#   • ui_find_tool             — locate a control by name / auto_id / type
#   • ui_click_tool            — CLICK AN ELEMENT BY NAME (no coordinates)
#   • ui_type_tool             — type into a SPECIFIC element
#   • ui_get_text_tool         — read text/value from an element
#   • ui_wait_tool             — wait for element / window to appear/disappear
#
# Backend: pywinauto 'uia' — works with Win32, WinForms, WPF, and most
# modern apps (including Chrome's accessibility tree when enabled).
# Old mouse/keyboard tools remain as fallback (see keyboard_mouse_CTRL.py).
#
# Conventions: async, blocking work in asyncio.to_thread, Urdu/English
# results, never raises, COM init per thread (pywinauto requirement).
# ============================================================================
import asyncio
import logging

from livekit.agents import function_tool

import jarvis_safety as safety

logger = logging.getLogger(__name__)

try:  # console-safe output (cp1252 consoles choke on Urdu / emoji)
    import sys as _sys

    # getattr: the stubs type stdout as TextIO, which has no `reconfigure`
    # (it exists only on the real TextIOWrapper) — keeps type-checkers clean.
    _reconfigure = getattr(_sys.stdout, "reconfigure", None)
    if callable(_reconfigure):
        _reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    from pywinauto import Desktop
    from pywinauto.application import Application
    from pywinauto.timings import TimeoutError as PWATimeoutError
except Exception as _e:  # pragma: no cover
    Desktop = None
    _IMPORT_ERROR = _e

_MAX_CONTROLS = 60       # cap listed controls (result size)
_UI_TIMEOUT = 8          # default seconds for find/wait operations


# ──────────────────────────────────────────────
# Thread helpers (pywinauto + COM need per-thread init)
# ──────────────────────────────────────────────
def _coinit_job(fn):
    """Run a blocking pywinauto job in a COM-initialized worker thread."""
    def wrapper():
        try:
            import comtypes
            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        except Exception:
            try:
                import comtypes
                comtypes.CoInitialize()
            except Exception:
                pass
        try:
            return fn()
        finally:
            try:
                import comtypes
                comtypes.CoUninitialize()
            except Exception:
                pass
    return wrapper


def _desktop():
    if Desktop is None:
        raise RuntimeError(f"pywinauto unavailable: {_IMPORT_ERROR}")
    return Desktop(backend="uia")


def _norm(s) -> str:
    return (s or "").strip().lower()


def _match_title(w, query: str) -> bool:
    """Case-insensitive substring window-title match (empty query matches all)."""
    q = _norm(query)
    if not q:
        return True
    return q in _norm(w.window_text())


def _window_process_name(w) -> str:
    """Process exe name (lowercase, no .exe) for a window wrapper. '' on error."""
    try:
        import psutil
        p = psutil.Process(w.element_info.process_id)
        return (p.name() or "").lower().removesuffix(".exe")
    except Exception:
        return ""


def _find_window(query: str):
    """Find a top-level window by (partial) title OR process/app name.
    Raises LookupError when missing."""
    q = _norm(query)
    if not q:
        raise LookupError("window query خالی ہے")
    wins = [w for w in _desktop().windows() if _match_title(w, query)]
    if not wins:
        # Fallback: match by process name (Notepad titles can be empty via UIA)
        for w in _desktop().windows():
            pname = _window_process_name(w)
            if pname and (q == pname or q in pname or pname in q):
                wins.append(w)
    if not wins:
        raise LookupError(f"ونڈو نہیں ملی: '{query}'")
    if len(wins) > 1:
        exact = [w for w in wins if _norm(w.window_text()) == _norm(query)]
        wins = exact or wins
    return wins[0]


# Modern UWP/WPF apps use different type names for the same concept
_CT_SYNONYMS = {
    "Edit": ["Edit", "TextArea", "Document"],
    "TextArea": ["TextArea", "Edit", "Document"],
    "Document": ["Document", "TextArea", "Edit"],
    "Button": ["Button", "SplitButton", "Hyperlink"],
    "MenuItem": ["MenuItem", "Button"],
    "Tab": ["Tab", "TabItem"],
    "TabItem": ["TabItem", "Tab"],
}


def _find_control(win, name="", control_type="", automation_id="",
                  timeout: float = _UI_TIMEOUT):
    """Locate a descendant control by any combination of
    name (title), control_type (Button/Edit/ListItem/…) and automation_id.
    Tries control-type synonyms (Edit↔TextArea↔Document) for modern apps.
    Raises LookupError when nothing matches."""
    if not (name or control_type or automation_id):
        raise ValueError("کم از کم ایک شناخت دیں: name / control_type / automation_id")
    ct = control_type.capitalize() if control_type else None
    ct_candidates = ([ct] if ct else []) or None
    if ct:
        ct_candidates = _CT_SYNONYMS.get(ct, [ct])
    # 1) spec-based search (fast path) across synonyms
    for cand in (ct_candidates or [None]):
        kwargs = {}
        if name:
            kwargs["best_match"] = name
        if cand:
            kwargs["control_type"] = cand
        if automation_id:
            kwargs["auto_id"] = automation_id
        try:
            return win.child_window(**kwargs).wait("visible ready", timeout=timeout)
        except Exception:
            continue
    # 2) manual descendant scan (handles dynamic/renamed titles)
    for d in win.descendants():
        try:
            dtype = d.element_info.control_type
            if ct_candidates and dtype not in ct_candidates:
                continue
            if automation_id and d.element_info.automation_id != automation_id:
                continue
            if name and _norm(name) not in _norm(d.window_text()):
                continue
            return d
        except Exception:
            continue
    raise LookupError(
        f"کنٹرول نہیں ملا (name='{name}', type='{control_type}', "
        f"auto_id='{automation_id}') — ui_list_controls_tool سے درست نام لیں۔")


def _ctrl_info(d) -> str:
    """Compact one-line description of a control for listing."""
    try:
        ei = d.element_info
        t = ei.control_type or "?"
        txt = (d.window_text() or "").strip()
        aid = ei.automation_id or ""
        enabled = "" if d.element_info.enabled else " [disabled]"
        line = f"{t}: '{txt}'"
        if aid:
            line += f" (auto_id={aid})"
        return line + enabled
    except Exception:
        return "? (unreadable control)"


def _err(e) -> str:
    return f"❌ UI Automation error: {e}"

# ──────────────────────────────────────────────
# Tools — discover windows & controls
# ──────────────────────────────────────────────
@function_tool
async def ui_list_windows_tool() -> str:
    """UI Automation سے تمام top-level windows فہرست کریں (title + type)۔
    کسی ایپ پر UIA click/type سے پہلے درست window title یہاں سے لیں۔
    (precision-first: پرانے list_windows_tool سے بہتر معلومات دیتا ہے)۔"""
    def _list():
        rows = []
        for w in _desktop().windows():
            try:
                title = w.window_text() or "(بلا عنوان)"
                ct = w.element_info.control_type or "Window"
                rows.append(f"• [{ct}] {title}")
            except Exception:
                continue
        return rows
    try:
        rows = await asyncio.to_thread(_coinit_job(_list))
        if not rows:
            return "کوئی window نہیں ملی۔"
        return "🪟 UIA windows:\n" + "\n".join(rows[:40])
    except Exception as e:
        return _err(e)


@function_tool
async def ui_list_controls_tool(window_title: str) -> str:
    """کسی window کے تمام visible controls فہرست کریں (type + نام + auto_id)۔
    ui_click_tool / ui_type_tool سے پہلے یہ کال کر کے درست کنٹرول نام/automation_id
    لیں۔ مثال: window_title='chrome' → اس کے تمام buttons، tabs، edits۔"""
    def _dump():
        win = _find_window(window_title)
        items = [_ctrl_info(d) for d in win.descendants()]
        return win.window_text(), items
    try:
        title, items = await asyncio.to_thread(_coinit_job(_dump))
        shown = items[:_MAX_CONTROLS]
        extra = "" if len(items) <= _MAX_CONTROLS else \
            f"\n… (+{len(items) - _MAX_CONTROLS} مزید controls موجود ہیں)"
        return f"🧩 '{title}' کے controls:\n" + "\n".join(shown) + extra
    except Exception as e:
        return _err(e)


@function_tool
async def ui_find_tool(window_title: str, control_name: str = "",
                       control_type: str = "", automation_id: str = "") -> str:
    """کوئی control تلاش کریں (window_title + اختیاری name/type/auto_id)۔
    ملا تو اس کی تفصیل بتاتا ہے — click سے پہلے verify کرنے کے لیے مفید۔"""
    def _find():
        win = _find_window(window_title)
        ctrl = _find_control(win, control_name, control_type, automation_id)
        return win.window_text(), _ctrl_info(ctrl)
    try:
        wtitle, info = await asyncio.to_thread(_coinit_job(_find))
        return f"🎯 ملا گیا: {info}\n(window: '{wtitle}')"
    except Exception as e:
        return _err(e)


# ──────────────────────────────────────────────
# Tools — actions: click / type / read / wait
# ──────────────────────────────────────────────
@function_tool
async def ui_click_tool(window_title: str, control_name: str = "",
                        control_type: str = "", automation_id: str = "") -> str:
    """UI Automation سے کنٹرول پر click کریں — COORDINATES کی ضرورت نہیں۔
    name (visible text), control_type (Button/MenuItem/TabItem…) یا automation_id
    سے پہچانیں۔ یہ پرانے click_at_tool (x,y) سے کہیں زیادہ قابلِ اعتماد ہے —
    جب بھی ممکن ہو یہی استعمال کریں۔"""
    blocked = safety.gate("ui_click_tool", cooldown=1.0)
    if blocked:
        return blocked
    if not (control_name or control_type or automation_id):
        return "❌ control_name / control_type / automation_id میں سے کچھ دیں۔"

    def _click():
        win = _find_window(window_title)
        win.set_focus()
        ctrl = _find_control(win, control_name, control_type, automation_id)
        info = _ctrl_info(ctrl)
        ctrl.click_input()  # real mouse event at element position (works everywhere)
        return info
    try:
        info = await asyncio.to_thread(_coinit_job(_click))
        safety.record_action("ui_click", f"{window_title} → {info}", True)
        return f"🖱️ click کیا: {info}"
    except Exception as e:
        safety.record_action("ui_click", f"{window_title} → {e}", False)
        return _err(e)


@function_tool
async def ui_type_tool(window_title: str, text: str, control_name: str = "",
                       control_type: str = "Edit", automation_id: str = "",
                       clear_first: bool = True) -> str:
    """مخصوص element (Edit box) میں متن ٹائپ کریں — focus guess کے بغیر۔
    control_name/automation_id سے exact field چنیں، پہلے پرانا text clear
    (clear_first=True)۔ لنک long text کے لیے clipboard paste استعمال ہوتا ہے۔"""
    blocked = safety.gate("ui_type_tool", cooldown=1.0)
    if blocked:
        return blocked
    if not text:
        return "❌ text خالی ہے۔"

    def _type():
        win = _find_window(window_title)
        ctrl = _find_control(win, control_name, control_type, automation_id)
        info = _ctrl_info(ctrl)
        ctrl.set_focus()
        if clear_first:
            try:
                ctrl.set_edit_text("")
            except Exception:
                try:
                    ctrl.type_keys("^a{DELETE}", pause=0.01)
                except Exception:
                    pass
        # long text → clipboard paste (fast, unicode-safe)
        if len(text) > 20:
            import pyperclip
            pyperclip.copy(text)
            ctrl.type_keys("^v", pause=0.05)
        else:
            ctrl.type_keys(text, with_spaces=True, pause=0.01)
        return info
    try:
        info = await asyncio.to_thread(_coinit_job(_type))
        safety.record_action("ui_type", f"{window_title} → {info}: {text[:40]}", True)
        return f"⌨️ '{info}' میں ٹائپ کیا ({len(text)} chars)۔"
    except Exception as e:
        safety.record_action("ui_type", f"{window_title} → {e}", False)
        return _err(e)


@function_tool
async def ui_get_text_tool(window_title: str, control_name: str = "",
                           control_type: str = "", automation_id: str = "") -> str:
    """کسی کنٹرول کا text/value پڑھیں (label, status, list item, document…)۔
    پورے window کا متن چاہیے تو control_name خالی چھوڑیں (window text +
    تمام بچوں کا text ملے گا)۔"""
    def _read():
        win = _find_window(window_title)
        if not (control_name or control_type or automation_id):
            texts = [win.window_text()] + [
                d.window_text().strip() for d in win.descendants()
                if d.window_text() and d.window_text().strip()]
            return "\n".join(texts[:200])
        ctrl = _find_control(win, control_name, control_type, automation_id)
        # value pattern first, fallback to window text
        try:
            val = ctrl.get_value()
            if val:
                return f"{_ctrl_info(ctrl)}\nVALUE: {val}"
        except Exception:
            pass
        return f"{_ctrl_info(ctrl)}\nTEXT: {ctrl.window_text()}"
    try:
        out = await asyncio.to_thread(_coinit_job(_read))
        return f"📄 متن:\n{out[:2500]}"
    except Exception as e:
        return _err(e)


@function_tool
async def ui_wait_tool(window_title: str, control_name: str = "",
                       control_type: str = "", automation_id: str = "",
                       timeout: int = 10, disappear: bool = False) -> str:
    """کنٹرول یا window کے appear/disappear ہونے کا انتظار کریں — verification
    کے لیے: action کے بعد ui_wait_tool سے نتیجہ confirm کریں (جیسے progress
    dialog غائب ہو، نئی window آ جائے)۔ disappear=True تو غائب ہونے کا انتظار۔"""
    timeout = max(1, min(60, int(timeout)))

    def _visible(win, name="", ct="", aid=""):
        """Return info string when control/window is visible, else ''."""
        try:
            if name or ct or aid:
                ctrl = _find_control(win, name, ct, aid, timeout=0.1)
                return _ctrl_info(ctrl) if ctrl.element_info.enabled or True else ""
            return f"window '{win.window_text()}'" if win.is_visible() else ""
        except Exception:
            return ""

    def _wait():
        import time as _t
        deadline = _t.time() + timeout
        last_err = ""
        while _t.time() < deadline:
            try:
                win = _find_window(window_title)
                if disappear:
                    if not _visible(win, control_name, control_type, automation_id):
                        return (f"window '{win.window_text()}'"
                                if not (control_name or control_type or automation_id)
                                else "control")
                else:
                    info = _visible(win, control_name, control_type, automation_id)
                    if info:
                        return info
            except LookupError:
                # window not found at all
                if disappear:
                    return f"window '{window_title}'"
                last_err = f"ونڈو نہیں ملی: '{window_title}'"
            except Exception as e:
                last_err = str(e)
            _t.sleep(0.5)
        raise TimeoutError(last_err or "timeout")

    try:
        target = await asyncio.to_thread(_coinit_job(_wait))
        state = "غائب ہو گیا" if disappear else "نظر آ گیا"
        safety.record_action("ui_wait", f"{window_title} → {state}", True)
        return f"✅ {target} {state}۔"
    except Exception as e:
        state = "غائب نہیں ہوا" if disappear else "نظر نہیں آیا"
        safety.record_action("ui_wait", f"{window_title} → {state}", False)
        return f"⏱️ {timeout}s میں {state} ({window_title}): {e}"
