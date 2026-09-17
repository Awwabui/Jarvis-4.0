# ============================================================================
# vision/screen_watcher.py — background screen awareness
#
# Runs in a daemon thread so the LiveKit voice loop is NEVER blocked:
#
#     capture → downsample (96x54 gray) → diff vs previous frame
#         ├─ change small → skip (no API cost)
#         └─ change >= threshold → vision analysis (async worker pool)
#                                 → in-memory screen context updated
#                                 → PNG bytes discarded (nothing saved)
#
# Stored state is ONLY: previous downsampled frame + latest context string.
# No screenshot files, no history, no repository writes — ever.
# ============================================================================
import os
import threading
import time

from vision import screen_capture, vision_agent

# ── Configuration (env-driven, single source of truth) ─────────────────
ENV_SCREEN_AWARENESS = "SCREEN_AWARENESS"
ENV_WATCH_INTERVAL = "SCREEN_WATCH_INTERVAL"
ENV_CHANGE_THRESHOLD = "SCREEN_CHANGE_THRESHOLD"

DEFAULT_INTERVAL = 2.0      # seconds between captures
DEFAULT_THRESHOLD = 8.0     # 0-255 mean-abs-diff on the downsampled frame
MIN_INTERVAL = 0.5          # guard rail against invalid .env values
RATE_LIMIT_GAP = 8.0        # min seconds between vision analyses
DEFAULT_VISION_QUESTION = (
    "Concise background awareness: what is currently happening on this screen?"
)


def _env_float(name: str, default: float) -> float:
    try:
        return float((os.getenv(name) or "").strip() or default)
    except ValueError:
        return default


def screen_awareness_enabled() -> bool:
    """SCREEN_AWARENESS=false/0/no/off disables background awareness.
    The explicit screenshot command works regardless (independent features)."""
    return (os.getenv(ENV_SCREEN_AWARENESS) or "true").strip().lower() not in (
        "0", "false", "no", "off"
    )


# spec-conventional alias
is_screen_awareness_enabled = screen_awareness_enabled


def log_status(reason: str, detail: str = "") -> None:
    """Central '[JARVIS] ...' status printer (matches startup log style).
    Never raises."""
    try:
        line = f"[JARVIS] {reason}".rstrip()
        if detail:
            line = f"{line} {detail}".rstrip()
        print(line, flush=True)
    except Exception:
        pass


def describe_change(prev, curr, threshold: float = None) -> tuple:
    """Lightweight change metrics between two downsampled grayscale frames.

    Returns (mean_diff, significant). Uses numpy (already a dependency).
    `significant` combines the mean diff with the fraction of clearly-changed
    pixels, so a blinking cursor / small text edits do NOT trigger the vision
    API, while app switches do. `threshold` defaults to the
    SCREEN_CHANGE_THRESHOLD env value (never hard-coded per call site).
    Never raises.
    """
    try:
        import numpy as np
        threshold = DEFAULT_THRESHOLD if threshold is None else threshold
        a = np.asarray(prev, dtype="int16")
        b = np.asarray(curr, dtype="int16")
        diff = abs(a - b)
        mean_diff = float(diff.mean())
        frac = float((diff > 16).mean())
        significant = mean_diff >= threshold or frac >= 0.02
        return mean_diff, significant
    except Exception:
        return 0.0, False


class ScreenWatcher:
    """Background screen-awareness worker (daemon thread, fail-safe)."""

    def __init__(self, interval: float = None, threshold: float = None):
        self.interval = max(MIN_INTERVAL, interval if interval else
                            _env_float(ENV_WATCH_INTERVAL, DEFAULT_INTERVAL))
        self.threshold = threshold if threshold is not None else \
            _env_float(ENV_CHANGE_THRESHOLD, DEFAULT_THRESHOLD)
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self._prev_frame = None
        self._context = ""          # latest vision analysis (in-memory ONLY)
        self._context_time = 0.0
        self._last_analysis = 0.0
        self._captures = 0
        self._changes = 0
        self._api_calls = 0
        self._errors = 0

    # ── lifecycle ──────────────────────────────────────────────────────
    def start(self):
        if self._thread and self._thread.is_alive():
            return self
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="jarvis-screen-watcher", daemon=True)
        self._thread.start()
        return self

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._thread = None

    # ── public context access (thread-safe) ────────────────────────────
    def get_context(self, max_age: float = 0.0) -> str:
        """Latest screen context text (never from disk). Empty if none yet."""
        with self._lock:
            if not self._context:
                return ""
            if max_age and (time.time() - self._context_time) > max_age:
                return ""
            return self._context

    def get_age(self) -> float:
        with self._lock:
            return (time.time() - self._context_time) if self._context_time else 0.0

    def is_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def stats(self) -> dict:
        with self._lock:
            return {
                "captures": self._captures,
                "changes_detected": self._changes,
                "vision_analyses": self._api_calls,
                "errors": self._errors,
                "interval": self.interval,
                "threshold": self.threshold,
                "alive": self.is_alive(),
            }

    # ── worker loop ────────────────────────────────────────────────────
    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self._tick()
            except Exception:
                self._errors += 1  # never let the watcher crash the agent

    def _tick(self) -> None:
        self._captures += 1
        img = screen_capture.capture_screen()
        if img is None:
            self._errors += 1
            return

        frame = screen_capture.downsample_gray(img)
        prev, self._prev_frame = self._prev_frame, frame
        if prev is None:
            return  # first frame — establish baseline, no analysis

        mean_diff, significant = describe_change(prev, frame, self.threshold)
        if not significant:
            return  # cheap path: NO vision API call

        now = time.time()
        if now - self._last_analysis < RATE_LIMIT_GAP:
            return  # rate limit — max ~4 vision calls/minute
        self._last_analysis = now
        self._changes += 1

        # Level-1 analysis runs on the vision worker pool; the watcher thread
        # stays free, so captures continue while the API call is in flight.
        png_bytes = screen_capture.to_vision_png(img)
        vision_agent.describe_screen_async(
            png_bytes, DEFAULT_VISION_QUESTION, self._on_analysis)
        self._api_calls += 1
        # png_bytes go out of scope here — frame is discarded, nothing saved

    def _on_analysis(self, result) -> None:
        """Callback from the vision worker thread (str result or Exception)."""
        if isinstance(result, Exception):
            self._errors += 1
            log_status("Vision analysis failed.",
                       f"({result}) Continuing normal operation.")
            return
        with self._lock:
            self._context = result.strip()
            self._context_time = time.time()


# ──────────────────────────────────────────────
# Module-level singleton (one watcher per process)
# ──────────────────────────────────────────────
_watcher = None


def start_watcher():
    """Start (or return the already-running) screen watcher. Idempotent."""
    global _watcher
    if _watcher and _watcher.is_alive():
        return _watcher
    _watcher = ScreenWatcher().start()
    return _watcher


def stop_watcher() -> None:
    global _watcher
    if _watcher:
        _watcher.stop()
        _watcher = None


def get_watcher():
    return _watcher if (_watcher and _watcher.is_alive()) else None


def get_screen_context():
    """(context_text, age_seconds) — ('', 0.0) when no watcher/context."""
    w = get_watcher()
    if not w:
        return "", 0.0
    return w.get_context(), w.get_age()


def build_context_message() -> str:
    """Formatted screen-context report for the voice agent (tool result)."""
    ctx, age = get_screen_context()
    if not ctx:
        return ("No screen context available yet (watcher off, or no significant "
                "screen change detected). Use analyze_screen_tool for a fresh look.")
    if age < 90:
        age_s = f"{age:.0f}s"
    else:
        age_s = f"{int(age // 60)}m {int(age % 60):02d}s"
    return f"Current screen context ({age_s} old):\n{ctx}"