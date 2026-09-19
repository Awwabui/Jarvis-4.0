# ============================================================================
# vision/screen_capture.py — REAL Windows screen capture + Desktop handling
#
# - capture_screen(): primary monitor via pyautogui (returns PIL Image, RGB)
# - get_desktop_path(): resolves the REAL Windows Desktop via
#   SHGetKnownFolderPath(FOLDERID_Desktop) — this correctly follows
#   OneDrive-redirected Desktops. Never hard-codes a user path.
# - save_screenshot_to_desktop(): explicit screenshot → unique PNG on Desktop
#
# NOTE: the background screen watcher does NOT use save_screenshot_to_desktop.
# Background frames are processed in memory and discarded (privacy).
# ============================================================================
import io
import os
from datetime import datetime

import pyautogui

pyautogui.FAILSAFE = False

try:  # console-safe output (cp1252 consoles choke on Urdu / emoji)
    import sys

    # getattr: the stubs type stdout as TextIO, which has no `reconfigure`
    # (it exists only on the real TextIOWrapper) — keeps type-checkers clean.
    _reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(_reconfigure):
        _reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


# ──────────────────────────────────────────────
# Capture
# ──────────────────────────────────────────────
def screen_size() -> tuple:
    """(width, height) of the primary monitor."""
    try:
        return pyautogui.size()
    except Exception:
        return (0, 0)


def capture_screen():
    """Capture the primary monitor. Returns a PIL Image (RGB) or None.

    Never raises — callers decide how to handle a failed capture.
    """
    try:
        img = pyautogui.screenshot()
        if img is None:
            return None
        if img.mode != "RGB":
            img = img.convert("RGB")
        return img
    except Exception:
        return None


def downsample_gray(img, size=(96, 54)):
    """Small grayscale copy for cheap change detection (NOT for vision)."""
    from PIL import Image
    return img.resize(size, Image.Resampling.BILINEAR).convert("L")


def to_vision_png(img, max_width: int = 1440) -> bytes:
    """Downscale for the vision model and encode as PNG bytes (lossless text)."""
    from PIL import Image
    w, h = img.size
    if w > max_width:
        img = img.resize((max_width, max(1, int(h * max_width / w))),
                         Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ──────────────────────────────────────────────
# Desktop resolution (dynamic — never hard-coded)
# ──────────────────────────────────────────────
def get_desktop_path() -> str:
    """Return the current user's real Windows Desktop directory.

    Uses SHGetKnownFolderPath(FOLDERID_Desktop) so OneDrive-redirected and
    roaming Desktops resolve correctly. Falls back to %USERPROFILE%\\Desktop.
    """
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            class _GUID(ctypes.Structure):
                _fields_ = [
                    ("Data1", ctypes.c_ulong),
                    ("Data2", ctypes.c_ushort),
                    ("Data3", ctypes.c_ushort),
                    ("Data4", ctypes.c_ubyte * 8),
                ]

            # FOLDERID_Desktop = {B4BFCC3A-DB2C-424C-B029-7FE99A87C641}
            desktop_guid = _GUID(
                0xB4BFCC3A, 0xDB2C, 0x424C,
                (ctypes.c_ubyte * 8)(0xB0, 0x29, 0x7F, 0xE9, 0x9A, 0x87, 0xC6, 0x41),
            )
            ptr = ctypes.c_wchar_p()
            shell32 = ctypes.windll.shell32
            shell32.SHGetKnownFolderPath.argtypes = [
                ctypes.POINTER(_GUID),
                wintypes.DWORD,
                wintypes.HANDLE,
                ctypes.POINTER(ctypes.c_wchar_p),
            ]
            if shell32.SHGetKnownFolderPath(
                ctypes.byref(desktop_guid), 0, None, ctypes.byref(ptr)
            ) == 0 and ptr.value:
                path = ptr.value
                ctypes.windll.ole32.CoTaskMemFree(ptr)
                if os.path.isdir(path):
                    return path
        except Exception:
            pass
    return os.path.join(os.path.expanduser("~"), "Desktop")


def build_screenshot_path(directory: str = "") -> str:
    """Unique Desktop path: Jarvis_Screenshot_YYYY-MM-DD_HH-MM-SS.png.

    Never overwrites — on collision appends _2, _3, …
    """
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    directory = directory or get_desktop_path()
    base = os.path.join(directory, f"Jarvis_Screenshot_{ts}.png")
    if not os.path.exists(base):
        return base
    stem, ext = os.path.splitext(base)
    for i in range(2, 10000):
        cand = f"{stem}_{i}{ext}"
        if not os.path.exists(cand):
            return cand
    return base


def save_screenshot_to_desktop(directory: str = "") -> str:
    """Capture the current screen and save it as a PNG on the Desktop.

    Returns the absolute path on success. Raises on capture/write failure
    (callers report the failure instead of crashing).
    """
    img = capture_screen()
    if img is None:
        raise RuntimeError("Screen capture failed.")
    path = build_screenshot_path(directory)
    try:
        img.save(path, format="PNG")
    except OSError as e:
        raise RuntimeError(f"Could not write screenshot file: {e}") from e
    return path