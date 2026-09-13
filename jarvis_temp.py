# ============================================================================
# OPTIONAL runtime temp-files manager.
# All disposable artifacts (screenshots, control logs, research dumps, etc.)
# are written here and the folder is auto-cleaned every hour.
#
# >>> IF THIS CAUSES PROBLEMS LATER: just delete this file and remove the
# >>> temp wiring from agent.py / keyboard_mouse_CTRL.py / jarvis_browser.py.
# ============================================================================
import os
import asyncio
import time

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
TEMP_DIR = os.path.join(PROJECT_DIR, "jarvis_temp")
_cleanup_task = None


def ensure_temp():
    os.makedirs(TEMP_DIR, exist_ok=True)
    return TEMP_DIR


def temp_path(name: str):
    """Return an absolute path inside the managed temp folder."""
    ensure_temp()
    return os.path.join(TEMP_DIR, name)


async def _cleanup_once():
    ensure_temp()
    for f in os.listdir(TEMP_DIR):
        p = os.path.join(TEMP_DIR, f)
        try:
            if os.path.isfile(p):
                os.remove(p)
        except Exception:
            pass  # file in use — skip, cleaned next cycle


async def cleanup_loop(interval_seconds: int = 3600):
    """Delete everything in the temp folder every `interval_seconds` (default 1h)."""
    while True:
        await asyncio.sleep(interval_seconds)
        await _cleanup_once()


def start_cleanup(interval_seconds: int = 3600):
    global _cleanup_task
    loop = asyncio.get_running_loop()
    if _cleanup_task is None or _cleanup_task.done():
        _cleanup_task = loop.create_task(cleanup_loop(interval_seconds))
    return _cleanup_task
