# ============================================================================
# vision/ — Jarvis 4.0 Screen Awareness + Screenshot system
#
# Architecture (each stage independent, all in-memory by default):
#
#     Real screen  →  screen_capture.py   (pyautogui, primary monitor)
#            ↓
#     Change detection  →  screen_watcher.py  (downsampled diff — NO AI)
#            ↓ (only on significant change)
#     Vision analysis   →  vision_agent.py    (google-genai + VISION_MODEL)
#            ↓
#     Visual context    →  kept in memory, old frame discarded
#            ↓
#     Agent integration →  tools.py (LiveKit function_tools used by agent.py)
#
# The explicit "take a screenshot" command is separate:
#     take_screenshot_desktop_tool() → Desktop PNG (only when user asks)
# ============================================================================

from vision.screen_capture import (
    capture_screen,
    get_desktop_path,
    build_screenshot_path,
    save_screenshot_to_desktop,
)
from vision.screen_watcher import (
    ScreenWatcher,
    start_watcher,
    stop_watcher,
    get_watcher,
    get_screen_context,
    is_screen_awareness_enabled,
)
from vision import vision_agent
from vision.vision_agent import VisionError, VisionUnavailable

__all__ = [
    "capture_screen",
    "get_desktop_path",
    "build_screenshot_path",
    "save_screenshot_to_desktop",
    "ScreenWatcher",
    "start_watcher",
    "stop_watcher",
    "get_watcher",
    "get_screen_context",
    "is_screen_awareness_enabled",
    "vision_agent",
    "VisionError",
    "VisionUnavailable",
]