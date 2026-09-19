import asyncio
import os
import time
from datetime import datetime
from typing import List, Optional
from pynput.keyboard import Key, Controller as KeyboardController
from livekit.agents import function_tool

# ──────────────────────────────────────────────
# Timing constants — tuned for speed + reliability
# ──────────────────────────────────────────────
_MOVE_DURATION = 0.04   # mouse move duration (was 0.06)
_POST_DELAY    = 0.015  # settle delay after action (was 0.03)
_CLIP_THRESHOLD = 20    # chars — above this use clipboard paste instead of char-by-char

try:
    from Jarvis_window_CTRL import focus_window as _focus_window
except Exception:
    _focus_window = None

# pyautogui imported lazily so we can wrap every call in to_thread
import pyautogui
pyautogui.FAILSAFE = True


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────
async def _run(fn, *args, **kwargs):
    """Run a blocking pyautogui call in a worker thread (non-blocking)."""
    return await asyncio.to_thread(fn, *args, **kwargs)


async def _maybe_focus(app: str):
    if app and _focus_window is not None:
        try:
            await _focus_window(app)
        except Exception:
            pass


def _log(action: str):
    try:
        try:
            from jarvis_temp import temp_path
            path = temp_path("control_log.txt")
        except Exception:
            path = "control_log.txt"
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now()}: {action}\n")
    except Exception:
        pass


# ──────────────────────────────────────────────
# Keyboard controller (singleton)
# ──────────────────────────────────────────────
_keyboard = KeyboardController()

_SPECIAL_KEYS = {
    "enter": Key.enter, "return": Key.enter, "space": Key.space, "tab": Key.tab,
    "shift": Key.shift, "shift_left": Key.shift_l, "shift_right": Key.shift_r,
    "ctrl": Key.ctrl, "ctrl_left": Key.ctrl_l, "ctrl_right": Key.ctrl_r,
    "alt": Key.alt, "alt_left": Key.alt_l, "alt_right": Key.alt_r,
    "win": Key.cmd, "windows": Key.cmd, "cmd": Key.cmd, "super": Key.cmd,
    "esc": Key.esc, "escape": Key.esc, "backspace": Key.backspace, "delete": Key.delete,
    "up": Key.up, "down": Key.down, "left": Key.left, "right": Key.right,
    "caps_lock": Key.caps_lock, "home": Key.home, "end": Key.end,
    "page_up": Key.page_up, "page_down": Key.page_down,
    "insert": Key.insert, "menu": Key.menu, "pause": Key.pause,
    "print_screen": Key.print_screen, "scroll_lock": Key.scroll_lock,
    "f1": Key.f1, "f2": Key.f2, "f3": Key.f3, "f4": Key.f4,
    "f5": Key.f5, "f6": Key.f6, "f7": Key.f7, "f8": Key.f8,
    "f9": Key.f9, "f10": Key.f10, "f11": Key.f11, "f12": Key.f12,
}


def _resolve_key(key: str):
    return _SPECIAL_KEYS.get(str(key).lower(), key)


# ──────────────────────────────────────────────
# Tool implementations
# ──────────────────────────────────────────────

@function_tool
async def list_windows_tool() -> str:
    """List currently open windows — use to pick the right title before focusing."""
    try:
        import pygetwindow as gw
        titles = [w.title for w in gw.getAllWindows() if w.title and w.title.strip()]
    except Exception as e:
        return f"❌ ونڈوز فہرست نہیں ملی: {e}"
    if not titles:
        return "کوئی ونڈو نہیں ملی۔"
    return "🪟 کھلی ونڈوز:\n" + "\n".join(f"• {t}" for t in titles[:30])


@function_tool
async def get_screen_size_tool() -> str:
    """اسکرین کا resolution (چوڑائی × اونچائی) حاصل کریں — coordinate math کے لیے۔"""
    w, h = await _run(pyautogui.size)
    return f"🖥️ اسکرین سائز: {w} × {h} pixels"


@function_tool
async def take_screenshot_tool() -> str:
    """عارضی (temp) screenshot لیں — صرف agent کے اندرونی/ڈیبگ استعمال کے لیے۔
    User سے screenshot مانگے جانے پر اس کے بجائے take_screenshot_desktop_tool
    استعمال کریں (وہ Desktop پر save کرتا ہے)۔"""
    try:
        from jarvis_temp import temp_path
        path = temp_path(f"screenshot_{int(time.time())}.png")
    except Exception:
        path = os.path.join(os.getcwd(), "screenshot.png")

    def _snap():
        img = pyautogui.screenshot()
        img.save(path)

    try:
        await _run(_snap)
        return f"📸 اسکرین شاٹ محفوظ: {path}"
    except Exception as e:
        return f"❌ اسکرین شاٹ ناکام: {e}"


@function_tool
async def move_cursor_tool(direction: str, distance: int = 100,
                          duration: float | None = None):
    """ماؤس کو direction (left/right/up/down) میں منتقل کریں۔"""
    d = _MOVE_DURATION if duration is None else duration
    x, y = await _run(pyautogui.position)
    offsets = {"left": (-distance, 0), "right": (distance, 0),
               "up": (0, -distance), "down": (0, distance)}
    dx, dy = offsets.get(direction, (0, 0))
    await _run(pyautogui.moveTo, x + dx, y + dy, d)
    await asyncio.sleep(_POST_DELAY)
    _log(f"Mouse moved {direction} {distance}")
    return f"🖱️ ماؤس {direction} منتقل کر دیا ({distance}px)۔"


@function_tool
async def move_cursor_to_tool(x: int, y: int, duration: float | None = None):
    """ماؤس کو مخصوص (x, y) کوآرڈینیٹ پر لے جائیں۔"""
    d = _MOVE_DURATION if duration is None else duration
    await _run(pyautogui.moveTo, x, y, d)
    await asyncio.sleep(_POST_DELAY)
    _log(f"Mouse to {x},{y}")
    return f"🖱️ ماؤس ({x}, {y}) پر لے گیا۔"


@function_tool
async def click_at_tool(x: int, y: int, button: str = "left", clicks: int = 1):
    """مخصوص (x, y) پر کلک کریں۔"""
    btn = {"left": "left", "right": "right", "middle": "middle"}.get(button, "left")
    await _run(pyautogui.click, x, y, clicks, 0.0, btn)
    await asyncio.sleep(_POST_DELAY)
    _log(f"Click {button}×{clicks} at {x},{y}")
    return f"🖱️ ({x}, {y}) پر {button} کلک ({clicks}×)۔"


@function_tool
async def mouse_click_tool(button: str = "left", clicks: int = 1, app: str = ""):
    """موجودہ cursor position پر کلک کریں۔"""
    await _maybe_focus(app)
    if button == "double" or clicks == 2:
        await _run(pyautogui.doubleClick)
    elif button == "triple" or clicks == 3:
        await _run(pyautogui.click, clicks=3)
    else:
        btn = {"left": "left", "right": "right", "middle": "middle"}.get(button, "left")
        await _run(pyautogui.click, button=btn, clicks=clicks)
    await asyncio.sleep(_POST_DELAY)
    _log(f"Click {button}×{clicks}")
    return f"🖱️ {button} کلک ({clicks}×)۔"


@function_tool
async def drag_mouse_tool(direction: str, distance: int = 100,
                           duration: float = 0.15, button: str = "left"):
    """ماؤس کو drag کریں۔"""
    btn = {"left": "left", "right": "right", "middle": "middle"}.get(button, "left")
    offsets = {"left": (-distance, 0), "right": (distance, 0),
               "up": (0, -distance), "down": (0, distance)}
    dx, dy = offsets.get(direction, (0, 0))
    await _run(pyautogui.drag, dx, dy, duration, btn)
    await asyncio.sleep(_POST_DELAY)
    _log(f"Drag {direction} {distance}")
    return f"🖱️ ماؤس {direction} drag کیا ({distance}px)۔"


@function_tool
async def scroll_cursor_tool(direction: str, amount: int = 10):
    """اسکرول کریں (up/down)۔"""
    steps = max(1, int(amount))
    dy = steps * 10 if direction == "up" else -steps * 10
    await _run(pyautogui.scroll, dy)
    await asyncio.sleep(_POST_DELAY)
    _log(f"Scroll {direction} {amount}")
    return f"🖱️ اسکرول {direction}۔"


@function_tool
async def get_cursor_position_tool() -> str:
    """ماؤس کی موجودہ پوزیشن (x, y) معلوم کریں۔"""
    x, y = await _run(pyautogui.position)
    return f"🖱️ کرسر پوزیشن: ({x}, {y})"


@function_tool
async def type_text_tool(text: str, delay: float = 0.015, app: str = ""):
    """متن ٹائپ کریں۔ لمبے متن کے لیے clipboard paste استعمال ہوتا ہے (تیز)۔"""
    await _maybe_focus(app)

    # Fast path: use clipboard paste for long texts
    if len(text) > _CLIP_THRESHOLD:
        try:
            import pyperclip
            pyperclip.copy(text)
            # Press Ctrl+V in a thread
            await _run(pyautogui.hotkey, "ctrl", "v")
            await asyncio.sleep(0.1)
            _log(f"Typed (clipboard): {text[:40]}…")
            return f"⌨️ clipboard paste کیا ({len(text)} chars)۔"
        except Exception:
            pass  # fall through to char-by-char

    # Char-by-char fallback
    def _type():
        try:
            _keyboard.type(text)
        except Exception:
            for char in text:
                try:
                    _keyboard.press(char)
                    _keyboard.release(char)
                    time.sleep(delay)
                except Exception:
                    continue

    await asyncio.to_thread(_type)
    _log(f"Typed: {text[:40]}")
    return f"⌨️ ٹائپ کیا: {text[:40]}{'…' if len(text) > 40 else ''}"


@function_tool
async def press_key_tool(key: str, app: str = ""):
    """کوئی key دبائیں (enter, tab, space, f5, esc وغیرہ)۔"""
    await _maybe_focus(app)
    k = _resolve_key(key)

    def _press():
        _keyboard.press(k)
        _keyboard.release(k)

    try:
        await asyncio.to_thread(_press)
    except Exception as e:
        return f"❌ key دبانے میں ناکامی: {key} — {e}"
    await asyncio.sleep(_POST_DELAY)
    _log(f"Key: {key}")
    return f"⌨️ key '{key}' دبائی۔"


@function_tool
async def press_hotkey_tool(keys: List[str], app: str = ""):
    """شارٹ کٹ keys دبائیں (مثلاً ['ctrl', 'c'] یا ['alt', 'f4'])۔"""
    await _maybe_focus(app)
    resolved = [_resolve_key(k) for k in keys]

    def _hotkey():
        for k in resolved:
            _keyboard.press(k)
        for k in reversed(resolved):
            _keyboard.release(k)

    await asyncio.to_thread(_hotkey)
    await asyncio.sleep(0.08)
    _log(f"Hotkey: {' + '.join(keys)}")
    return f"⌨️ {' + '.join(keys)} دبایا۔"


@function_tool
async def control_volume_tool(action: str):
    """والیوم کنٹرول کریں (up / down / mute)۔"""
    key_map = {"up": "volumeup", "down": "volumedown", "mute": "volumemute"}
    k = key_map.get(action)
    if k:
        await _run(pyautogui.press, k)
    await asyncio.sleep(_POST_DELAY)
    _log(f"Volume: {action}")
    return f"🔊 والیوم {action}۔"


@function_tool
async def swipe_gesture_tool(direction: str):
    """اسکرین پر swipe gesture کریں (up/down/left/right)۔"""
    w, h = await _run(pyautogui.size)
    x, y = w // 2, h // 2
    offsets = {
        "up":    (x, y + 200, x, y - 200),
        "down":  (x, y - 200, x, y + 200),
        "left":  (x + 200, y, x - 200, y),
        "right": (x - 200, y, x + 200, y),
    }
    coords = offsets.get(direction)
    if coords:
        sx, sy, ex, ey = coords
        def _swipe():
            pyautogui.moveTo(sx, sy)
            pyautogui.dragTo(ex, ey, duration=0.3)
        await asyncio.to_thread(_swipe)
    await asyncio.sleep(0.1)
    _log(f"Swipe: {direction}")
    return f"🖱️ swipe {direction} مکمل۔"
