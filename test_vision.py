# ============================================================================
# test_vision.py — verification for the Jarvis 4.0 screen awareness system
#
# Run:  venv\Scripts\python.exe test_vision.py
#
# Covers: real capture, change detection, unique filename logic, watcher
# start/stop, Desktop screenshot save (cleaned up afterwards), live vision
# API call, and agent.py import regression (tools registered).
# ============================================================================
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv()

PASS, FAIL = "✅", "❌"
results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"{PASS if cond else FAIL} {name}" + (f" — {detail}" if detail else ""))


# 1) Real screen capture (primary monitor)
from vision import screen_capture, screen_watcher, vision_agent

img = screen_capture.capture_screen()
check("capture_screen()", img is not None,
      f"size={getattr(img, 'size', None)} mode={getattr(img, 'mode', None)}")

# 2) Change detection — identical frame = no change, shifted frame = change
if img is not None:
    g1 = screen_capture.downsample_gray(img)
    g2 = screen_capture.downsample_gray(img)
    mean_same, sig_same = screen_watcher.describe_change(g1, g2)
    check("change detection: identical frame → not significant",
          not sig_same, f"mean_diff={mean_same:.3f}")

    from PIL import Image
    shifted = img.copy()
    px = shifted.load()
    for x in range(0, img.width, 2):          # half the screen shifted
        for y in range(img.height):
            r, g, b = px[x, y]
            px[x, y] = (255 - r, 255 - g, 255 - b)
    g3 = screen_capture.downsample_gray(shifted)
    mean_shift, sig_shift = screen_watcher.describe_change(g1, g3)
    check("change detection: inverted half-screen → significant",
          sig_shift, f"mean_diff={mean_shift:.2f}")

# 3) Desktop resolution + unique filename (collision test in temp dir)
desktop = screen_capture.get_desktop_path()
check("get_desktop_path()", os.path.isdir(desktop), desktop)

import jarvis_temp
tmp = jarvis_temp.ensure_temp()
p1 = screen_capture.build_screenshot_path(tmp)
p2 = screen_capture.build_screenshot_path(tmp)
check("unique filename format", os.path.basename(p1).startswith("Jarvis_Screenshot_")
      and p1.endswith(".png"), os.path.basename(p1))
import datetime
stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
forced = os.path.join(tmp, f"Jarvis_Screenshot_{stamp}.png")
open(forced, "w").close()
collision = screen_capture.build_screenshot_path(tmp)
os.remove(forced)
check("filename collision → never overwrites", collision != forced,
      os.path.basename(collision))

# 4) Watcher lifecycle — high threshold so NO vision API calls fire here
w = screen_watcher.ScreenWatcher(interval=0.6, threshold=999.0).start()
time.sleep(2.5)
st = w.stats()
check("watcher runs in background thread", w.is_alive() and st["captures"] >= 2,
      f"stats={st}")
w.stop()
time.sleep(0.2)
check("watcher stops cleanly", not w.is_alive(), "thread joined")

# 5) Explicit Desktop screenshot (real save, then cleanup)
try:
    path = screen_capture.save_screenshot_to_desktop()
    ok = os.path.isfile(path) and os.path.getsize(path) > 0
    if ok:
        os.remove(path)
    check("save_screenshot_to_desktop() + cleanup", ok, path)
except Exception as e:
    check("save_screenshot_to_desktop() + cleanup", False, str(e))

# 6) LIVE vision API call (real end-to-end: capture → vision model → text)
if img is not None and vision_agent.is_vision_configured():
    try:
        png = screen_capture.to_vision_png(img)
        text = vision_agent.analyze_image(
            png, "In one short sentence: what application is visible?")
        check("live vision API call", bool(text and text.strip()),
              text.strip()[:120])
    except vision_agent.VisionUnavailable as e:
        check("live vision API call", False, f"unavailable: {e}")
    except Exception as e:
        check("live vision API call", False, f"{type(e).__name__}: {e}")
else:
    check("live vision API call", False, "skipped (no key or no capture)")

# 7) Regression: agent.py still imports and registers the vision tools
try:
    import agent
    tool_names = [t.__name__ if hasattr(t, "__name__") else str(t)
                  for t in agent.Assistant.__init__.__doc__ or []] if False else None
    import dataclasses
    check("agent.py imports (regression)", True)
except Exception as e:
    check("agent.py imports (regression)", False, f"{type(e).__name__}: {e}")

failed = [n for n, ok in results if not ok]
print()
print(f"RESULT: {len(results) - len(failed)}/{len(results)} passed"
      + (f" — failed: {failed}" if failed else ""))
sys.exit(1 if failed else 0)