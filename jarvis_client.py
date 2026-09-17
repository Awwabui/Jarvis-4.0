# ============================================================================
# jarvis_client.py — Jarvis 4.0 PRO desktop ENGINE (GUI-agnostic)
#
# This is the "muscle" behind jarvis_gui.py: it runs your existing voice agent
# (agent.py) and drives a LiveKit room plus your local microphone/speaker, so
# the GUI can stay a thin, safe UI layer.
#
#   ┌────────────────  spawns   ┌───────────────────────────────┐
#   │  jarvis_gui.py │ ────────► │  agent.py dev  (Jarvis brain) │
#   │   (Tkinter)    │           └───────────────────────────────┘
#   │        │       │  events              ▲ job dispatch (LiveKit Cloud)
#   │        ▼       │  (queue)             │
#   │  JarvisEngine  │ ─────────► rtc.Room ─┘
#   └────────────────┘      mic ↑   speaker ↓
#
# Everything runs in ONE background thread with its OWN asyncio loop, so the
# GUI thread never blocks. Only public LiveKit APIs are used and agent.py is
# NOT modified — the app simply starts it and talks to it over a LiveKit room.
#
# Event protocol (dicts handed to on_event):
#   {"type": "status",      "state": str, "detail": str}
#       states: starting, worker_starting, worker_ready, worker_skipped,
#               connecting, connected, stopping, stopped, error
#   {"type": "log",         "level": "info|warn|error", "text": str,
#                            "source": "worker|app"}
#   {"type": "room",        "name": str, "sid": str, "identity": str, "url": str}
#   {"type": "transcript",  "role": "user|jarvis", "text": str,
#                            "final": bool, "ts": float}
#   {"type": "agent_state", "state": "initializing|listening|thinking|speaking|away"}
#   {"type": "mic_level",   "level": 0.0 … 1.0}
#
# Public API (all thread-safe):
#   list_audio_devices()      → {"input": [(idx, label)], "output": [...], ...}
#   JarvisEngine(on_event)    → engine
#       .start(...) .stop() .send_text(t) .set_mic_muted(b) .set_speaker_muted(b)
#       .state .running .room_name
#
# Requirements: LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET and
# GOOGLE_API_KEY in .env (see .env.example). Audio I/O uses sounddevice.
# ============================================================================
from __future__ import annotations

import asyncio
import datetime
import logging
import math
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any, Optional

import numpy as np
from dotenv import load_dotenv
from livekit import rtc

logger = logging.getLogger(__name__)

try:  # console-safe output (cp1252 consoles choke on Urdu / emoji)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(PROJECT_DIR, ".env")
_IS_WINDOWS = os.name == "nt"

# ── LiveKit topics (mirrors livekit-agents constants) ──
TOPIC_CHAT = "lk.chat"
ATTRIBUTE_AGENT_STATE = "lk.agent.state"

# ── Audio format ──
MIC_RATE = 48000          # what we publish to LiveKit
SPEAKER_RATE = 48000      # preferred local playback rate
FRAME_MS = 20             # mic chunk size

# ── Worker process ──
DEFAULT_IDENTITY = "M. Awwab"

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def load_env() -> None:
    """Load .env from the project folder (idempotent)."""
    try:
        load_dotenv(ENV_FILE, override=False)
    except Exception:
        pass


load_env()


# ──────────────────────────────────────────────
# Environment / process helpers
# ──────────────────────────────────────────────
def _no_window_flags() -> int:
    """Keep child processes invisible when the GUI runs under pythonw.exe."""
    if not _IS_WINDOWS:
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def _default_python() -> str:
    """Prefer this interpreter (the venv python) for the worker child."""
    exe = sys.executable or ""
    if exe and os.path.exists(exe):
        return exe
    return "python"


def _kill_tree(pid: int) -> None:
    """Force-kill a process AND its children (agent jobs run in children)."""
    if _IS_WINDOWS:
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=_no_window_flags(),
                timeout=15,
            )
            return
        except Exception:
            pass
    try:
        subprocess.run(
            ["pkill", "-TERM", "-P", str(pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except Exception:
        pass


def _guess_level(line: str) -> str:
    low = line.lower()
    if "traceback" in low or "error" in low or "exception" in low or "failed" in low:
        return "error"
    if "warn" in low:
        return "warn"
    return "info"


# ──────────────────────────────────────────────
# Audio device helpers
# ──────────────────────────────────────────────
def _hostapi_name(index: int) -> str:
    try:
        import sounddevice as sd

        return str(sd.query_hostapis(index).get("name", "")).replace("Windows ", "")
    except Exception:
        return ""


def list_audio_devices() -> dict[str, Any]:
    """Available input/output devices + defaults. Never raises."""
    data: dict[str, Any] = {
        "input": [], "output": [], "default_input": None,
        "default_output": None, "error": None,
    }
    try:
        import sounddevice as sd
    except Exception as e:  # pragma: no cover - missing optional dep
        data["error"] = f"sounddevice install nahi hai: {e}"
        return data

    try:
        for idx, dev in enumerate(sd.query_devices()):
            name = str(dev.get("name") or f"Device {idx}").strip()
            api = _hostapi_name(int(dev.get("hostapi", -1)))
            label = f"{name}  [{api}]" if api else name
            if int(dev.get("max_input_channels", 0)) > 0:
                data["input"].append((idx, label))
            if int(dev.get("max_output_channels", 0)) > 0:
                data["output"].append((idx, label))
        default_in, default_out = sd.default.device
        if default_in is not None and int(default_in) >= 0:
            data["default_input"] = int(default_in)
        if default_out is not None and int(default_out) >= 0:
            data["default_output"] = int(default_out)
    except Exception as e:
        data["error"] = str(e)
    return data


def _pick_samplerate(device: Optional[int], kind: str, preferred: int) -> int:
    """Use `preferred` when the device supports it, else its own default rate."""
    try:
        import sounddevice as sd

        check = sd.check_input_settings if kind == "input" else sd.check_output_settings
        check(device=device, samplerate=preferred, channels=1, dtype="int16")
        return preferred
    except Exception:
        pass
    try:
        import sounddevice as sd

        info = sd.query_devices(device, kind=kind)
        rate = int(round(float(info.get("default_samplerate") or 0)))
        if rate > 0:
            return rate
    except Exception:
        pass
    return preferred


# ──────────────────────────────────────────────
# LiveKit room + token
# ──────────────────────────────────────────────
async def _create_room_token(
    room_name: Optional[str],
    identity: str,
    emit: Optional[Callable[[dict], None]] = None,
) -> tuple[str, str, str]:
    """Create the room (best effort) and mint a join token.

    Returns (room_name, jwt_token, ws_url).
    """
    from livekit import api

    load_env()
    ws_url = (os.getenv("LIVEKIT_URL") or "").strip()
    key = (os.getenv("LIVEKIT_API_KEY") or "").strip()
    secret = (os.getenv("LIVEKIT_API_SECRET") or "").strip()

    if not (ws_url and key and secret):
        raise RuntimeError(
            "LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET .env mein set nahi hain "
            "— .env.example dekhein."
        )

    room = (room_name or "").strip() or f"jarvis-desktop-{uuid.uuid4().hex[:8]}"
    http_url = ws_url.replace("wss://", "https://").replace("ws://", "http://")

    try:
        async with api.LiveKitAPI(http_url, key, secret) as lk:
            await lk.room.create_room(
                api.CreateRoomRequest(
                    name=room, empty_timeout=600, departure_timeout=20, max_participants=4
                )
            )
    except Exception as e:
        if emit is not None:
            emit({
                "type": "log", "level": "warn", "source": "app",
                "text": f"room pre-create warn: {e} (join par auto-create try hoga)",
            })

    token = (
        api.AccessToken(key, secret)
        .with_identity(identity)
        .with_name(identity)
        .with_grants(
            api.VideoGrants(room_join=True, room=room, can_publish=True, can_subscribe=True)
        )
        .with_ttl(datetime.timedelta(hours=6))
        .to_jwt()
    )
    return room, token, ws_url


# ──────────────────────────────────────────────
# Worker process (`python agent.py dev`)
# ──────────────────────────────────────────────
class WorkerProcess:
    """Jarvis brain (agent.py) as a managed child process.

    The GUI never needs a second terminal: this starts the LiveKit worker,
    streams its log lines back, and stops it (with children) at app close.
    """

    READY_MARKERS = ("registered worker", "worker registered")

    def __init__(
        self,
        on_log: Optional[Callable[[str, str], None]] = None,
        mode: Optional[str] = None,
        python_exe: Optional[str] = None,
        project_dir: str = PROJECT_DIR,
    ) -> None:
        self._on_log = on_log or (lambda level, text: None)
        self._mode = (mode or os.getenv("JARVIS_WORKER_MODE") or "dev").strip()
        self._python = python_exe or _default_python()
        self._project_dir = project_dir
        self._proc: Optional[subprocess.Popen] = None
        self._reader: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self.cmd_text = ""

    # ── state ──
    @property
    def pid(self) -> Optional[int]:
        return self._proc.pid if self._proc is not None else None

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def ready(self) -> bool:
        return self._ready.is_set()

    # ── lifecycle ──
    def start(self) -> None:
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["JARVIS_DESKTOP_CHILD"] = "1"

        argv = [self._python, "-u", "agent.py", self._mode]
        self.cmd_text = " ".join(argv)
        self._proc = subprocess.Popen(
            argv,
            cwd=self._project_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            env=env,
            bufsize=0,
            creationflags=_no_window_flags(),
        )
        self._reader = threading.Thread(
            target=self._pump, name="jarvis-worker-log", daemon=True
        )
        self._reader.start()
        self._on_log("info", f"worker child start (pid {self._proc.pid}) — {self.cmd_text}")

    def wait_until_registered(self, timeout: float = 90.0) -> bool:
        """Block until the worker registers with LiveKit (or it dies)."""
        deadline = time.monotonic() + max(5.0, float(timeout))
        while time.monotonic() < deadline:
            if self._ready.wait(0.4):
                return True
            if self._proc is not None and self._proc.poll() is not None:
                self._on_log("error", f"worker exit ho gaya (code {self._proc.returncode})")
                return False
        return self._ready.is_set()

    def stop(self, timeout: float = 8.0) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        if proc.poll() is None:
            self._on_log("info", "worker band kiya ja raha hai…")
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                proc.wait(timeout=timeout)
            except Exception:
                if proc.pid:
                    _kill_tree(proc.pid)
        try:
            if proc.stdout is not None:
                proc.stdout.close()
        except Exception:
            pass

    # ── internals ──
    def _pump(self) -> None:
        stream = self._proc.stdout if self._proc is not None else None
        if stream is None:
            return
        try:
            for raw in iter(stream.readline, b""):
                line = raw.decode("utf-8", errors="replace").rstrip()
                if not line:
                    continue
                clean = _ANSI_RE.sub("", line).strip()
                if not clean:
                    continue
                if not self._ready.is_set():
                    low = clean.lower()
                    if any(m in low for m in self.READY_MARKERS):
                        self._ready.set()
                self._on_log(_guess_level(clean), clean)
        except Exception:
            pass


# ──────────────────────────────────────────────
# Local audio: speaker (agent voice → your ears)
# ──────────────────────────────────────────────
class SpeakerOutput:
    """sounddevice output stream fed by the agent's LiveKit audio track.

    Frames are buffered (max 2 s) and played by the audio callback, so the
    engine loop never blocks. `clear()` = instant barge-in.
    """

    def __init__(self, device: Optional[int] = None, muted: bool = False) -> None:
        self._device = device
        self._muted = bool(muted)
        self._stream: Any = None
        self._buf = np.zeros(0, dtype=np.int16)
        self._lock = threading.Lock()
        self._rate = SPEAKER_RATE
        self._playing = False
        self.error: Optional[str] = None

    @property
    def rate(self) -> int:
        return self._rate

    @property
    def playing(self) -> bool:
        return self._playing

    @property
    def muted(self) -> bool:
        return self._muted

    def open(self) -> None:
        import sounddevice as sd

        self._rate = _pick_samplerate(self._device, "output", SPEAKER_RATE)
        blocksize = max(240, int(self._rate * FRAME_MS / 1000))
        self._stream = sd.OutputStream(
            device=self._device,
            samplerate=self._rate,
            channels=1,
            dtype="int16",
            blocksize=blocksize,
            callback=self._callback,
        )
        self._stream.start()

    def push(self, frame: rtc.AudioFrame) -> None:
        if self._muted:
            return
        try:
            samples = np.frombuffer(frame.data, dtype=np.int16)
        except Exception:
            return
        if samples.size == 0:
            return
        with self._lock:
            self._buf = np.concatenate((self._buf, samples))
            limit = self._rate * 2  # never buffer more than 2 seconds
            if self._buf.size > limit:
                self._buf = self._buf[-limit:]
            self._playing = True

    def clear(self) -> None:
        with self._lock:
            if self._buf.size:
                self._buf = np.zeros(0, dtype=np.int16)
        self._playing = False

    def set_muted(self, muted: bool) -> None:
        self._muted = bool(muted)
        if self._muted:
            self.clear()

    def close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is None:
            return
        try:
            stream.stop()
        except Exception:
            pass
        try:
            stream.close()
        except Exception:
            pass
        self.clear()

    # ── audio-thread callback ──
    def _callback(self, outdata, frames, time_info, status) -> None:
        try:
            with self._lock:
                take = min(frames, self._buf.size)
                if take:
                    outdata[:take, 0] = self._buf[:take]
                    self._buf = self._buf[take:].copy()
                if take < frames:
                    outdata[take:, 0] = 0
                self._playing = self._buf.size > 0
        except Exception:
            try:
                outdata.fill(0)
            except Exception:
                pass


# ──────────────────────────────────────────────
# Local audio: microphone (your voice → agent)
# ──────────────────────────────────────────────
class MicCapture:
    """sounddevice InputStream → rtc.AudioSource (48 kHz mono to LiveKit).

    The audio thread only queues raw bytes; a task on the engine loop turns
    them into `rtc.AudioFrame`s (resampling when the device can't do 48 kHz)
    and reports the level for the GUI meter.
    """

    def __init__(
        self,
        device: Optional[int],
        source: rtc.AudioSource,
        *,
        muted: bool = False,
        on_level: Optional[Callable[[float], None]] = None,
    ) -> None:
        self._device = device
        self._source = source
        self._muted = bool(muted)
        self._on_level = on_level
        self._stream: Any = None
        self._queue: Optional[asyncio.Queue] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._resampler: Any = None
        self._rate = MIC_RATE
        self._closed = False
        self._level_ts = 0.0
        self.error: Optional[str] = None

    @property
    def rate(self) -> int:
        return self._rate

    @property
    def muted(self) -> bool:
        return self._muted

    def open(self) -> None:
        import sounddevice as sd

        self._rate = _pick_samplerate(self._device, "input", MIC_RATE)
        if self._rate != MIC_RATE:
            self._resampler = rtc.AudioResampler(
                input_rate=self._rate, output_rate=MIC_RATE, num_channels=1
            )
        blocksize = max(160, int(self._rate * FRAME_MS / 1000))
        self._stream = sd.InputStream(
            device=self._device,
            samplerate=self._rate,
            channels=1,
            dtype="int16",
            blocksize=blocksize,
            callback=self._callback,
        )
        self._stream.start()

    def attach_loop(
        self, loop: asyncio.AbstractEventLoop, queue: asyncio.Queue
    ) -> None:
        self._loop = loop
        self._queue = queue

    def set_muted(self, muted: bool) -> None:
        self._muted = bool(muted)

    def close(self) -> None:
        stream, self._stream = self._stream, None
        self._closed = True
        if stream is not None:
            try:
                stream.stop()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass
        loop, queue = self._loop, self._queue
        if loop is not None and queue is not None:
            try:
                loop.call_soon_threadsafe(self._wake)
            except Exception:
                pass

    # ── async pump (engine loop) ──
    async def pump(self) -> None:
        """Turn queued mic bytes into LiveKit audio frames."""
        if self._queue is None:
            return
        while True:
            data = await self._queue.get()
            if data is None or self._closed:
                break
            frame = rtc.AudioFrame(
                data=data,
                sample_rate=self._rate,
                num_channels=1,
                samples_per_channel=len(data) // 2,
            )
            self._report_level(frame)
            if self._muted:
                continue
            frames = self._resampler.push(frame) if self._resampler is not None else [frame]
            for f in frames:
                try:
                    if self._source.queued_duration > 0.5:
                        continue  # network slow → drop instead of building latency
                    await self._source.capture_frame(f)
                except Exception:
                    pass

    def _wake(self) -> None:
        if self._queue is not None:
            try:
                self._queue.put_nowait(None)
            except Exception:
                pass

    # ── audio-thread callback ──
    def _callback(self, indata, frames, time_info, status) -> None:
        loop, queue = self._loop, self._queue
        if self._closed or loop is None or queue is None:
            return
        try:
            data = bytes(indata)
        except Exception:
            return
        try:
            loop.call_soon_threadsafe(self._offer, data)
        except Exception:
            pass

    def _offer(self, data: bytes) -> None:
        """Runs on the engine loop — keeps the backlog bounded (real-time)."""
        queue = self._queue
        if queue is None or self._closed:
            return
        if queue.qsize() > 60:  # ~1.2 s behind → drop oldest
            try:
                queue.get_nowait()
            except Exception:
                pass
        try:
            queue.put_nowait(data)
        except Exception:
            pass

    def _report_level(self, frame: rtc.AudioFrame) -> None:
        cb = self._on_level
        if cb is None:
            return
        now = time.monotonic()
        if now - self._level_ts < 0.05:  # ~20 updates/s
            return
        self._level_ts = now
        try:
            samples = np.frombuffer(frame.data, dtype=np.int16)
            if samples.size == 0:
                return
            rms = float(np.sqrt(np.mean(np.square(samples.astype(np.float32))))) / 32768.0
            db = 20.0 * math.log10(rms + 1e-9)
        except Exception:
            return
        cb(max(0.0, min(1.0, (db + 60.0) / 50.0)))


# ──────────────────────────────────────────────
# One live session: worker + room + mic + speaker
# ──────────────────────────────────────────────
class _LocalSession:
    """Internal: a single Start → Stop cycle of the desktop app."""

    def __init__(self, emit: Callable[[dict], None], cfg: dict[str, Any]) -> None:
        self._emit = emit
        self._cfg = cfg
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop_evt: Optional[asyncio.Event] = None
        self._stop_requested = False
        self._room: Optional[rtc.Room] = None
        self._worker: Optional[WorkerProcess] = None
        self._mic: Optional[MicCapture] = None
        self._speaker: Optional[SpeakerOutput] = None
        self._source: Optional[rtc.AudioSource] = None
        self._mic_track: Optional[rtc.LocalAudioTrack] = None
        self._pump_task: Optional[asyncio.Task] = None
        self._watch_task: Optional[asyncio.Task] = None
        self._play_tasks: dict[str, asyncio.Task] = {}
        self._identity = str(cfg.get("identity") or DEFAULT_IDENTITY)
        self._mic_muted = bool(cfg.get("mic_muted"))
        self._speaker_muted = bool(cfg.get("speaker_muted"))
        self._agent_seen = False
        self.room_name: Optional[str] = None
        self.worker_cmd = ""

    # ──────────────────────────────
    # Control (engine loop thread)
    # ──────────────────────────────
    def request_stop(self) -> None:
        """Ask the session to finish (called via call_soon_threadsafe)."""
        self._stop_requested = True
        if self._stop_evt is not None:
            self._stop_evt.set()

    async def send_text(self, text: str) -> None:
        room = self._room
        if room is None or not room.isconnected():
            raise RuntimeError("room connected nahi hai")
        await room.local_participant.send_text(text, topic=TOPIC_CHAT)
        self._emit({
            "type": "transcript", "role": "user", "text": text,
            "final": True, "ts": time.time(), "typed": True,
        })

    def set_mic_muted(self, muted: bool) -> None:
        self._mic_muted = bool(muted)
        if self._mic is not None:
            self._mic.set_muted(muted)
        track = self._mic_track
        if track is not None:
            try:
                track.mute() if muted else track.unmute()
            except Exception:
                pass
        if muted and self._source is not None:
            try:
                self._source.clear_queue()
            except Exception:
                pass

    def set_speaker_muted(self, muted: bool) -> None:
        self._speaker_muted = bool(muted)
        if self._speaker is not None:
            self._speaker.set_muted(muted)

    def force_stop(self) -> None:
        """Kill the worker child from ANY thread (last-resort cleanup)."""
        worker, self._worker = self._worker, None
        if worker is not None:
            try:
                worker.stop(timeout=3.0)
            except Exception:
                pass

    # ──────────────────────────────
    # Main flow
    # ──────────────────────────────
    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop_evt = asyncio.Event()
        if self._stop_requested:
            self._stop_evt.set()
        try:
            await self._start_worker()
            await self._join_room()
            await self._start_audio()
            if self._stop_requested:
                return
            self._emit_status("connected", f"Connected — room: {self.room_name}")
            self._watch_task = asyncio.create_task(self._watch_for_agent())
            await self._stop_evt.wait()
        finally:
            self._emit_status("stopping", "Jarvis band kiya ja raha hai…")
            await self._teardown()

    async def _start_worker(self) -> None:
        cfg = self._cfg
        if self._stop_requested:
            return
        if not cfg.get("start_worker", True):
            self._emit_status("worker_skipped", "Worker auto-start OFF — pehle se chalta worker use hoga")
            return

        self._emit_status("worker_starting", "Jarvis brain (agent.py) start ho raha hai…")
        self._worker = WorkerProcess(on_log=self._worker_log)
        try:
            self._worker.start()
        except Exception as e:
            self._worker = None
            raise RuntimeError(f"worker start nahi hua: {e}") from e
        self.worker_cmd = self._worker.cmd_text

        ok = await asyncio.to_thread(
            self._worker.wait_until_registered, float(cfg.get("agent_wait") or 90.0)
        )
        if self._stop_requested:
            return
        if not ok:
            raise RuntimeError(
                "agent.py worker LiveKit par register nahi hua (timeout ya exit). "
                "Worker log tab dekhein — .env keys, internet aur port 2883 check karein."
            )
        self._emit_status("worker_ready", "Agent worker registered ✓")

    def _worker_log(self, level: str, text: str) -> None:
        """Called from the worker reader thread → marshal onto the loop."""
        event = {"type": "log", "level": level, "source": "worker", "text": text}
        loop = self._loop
        if loop is None or loop.is_closed():
            self._emit(event)
            return
        try:
            loop.call_soon_threadsafe(self._emit, event)
        except Exception:
            pass

    async def _join_room(self) -> None:
        if self._stop_requested:
            return
        self._emit_status("connecting", "LiveKit room join kiya ja raha hai…")
        room_name, token, ws_url = await _create_room_token(
            self._cfg.get("room_name"), self._identity, self._emit
        )
        self.room_name = room_name
        self._room = rtc.Room()
        self._wire_room(self._room)
        try:
            await asyncio.wait_for(self._room.connect(ws_url, token), timeout=25)
        except Exception as e:
            self._room = None
            raise RuntimeError(f"LiveKit room connect fail: {e}") from e
        sid = ""
        try:
            sid = str(await self._room.sid or "")
        except Exception:
            pass
        self._emit({
            "type": "room", "name": room_name, "sid": sid,
            "identity": self._identity, "url": ws_url,
        })
        # The agent may already be inside (it joins as soon as the room exists),
        # so scan the initial participant list too — events alone would miss it.
        try:
            for participant in self._room.remote_participants.values():
                self._note_participant(participant)
        except Exception:
            pass

    # ──────────────────────────────
    # Room events (engine loop)
    # ──────────────────────────────
    def _wire_room(self, room: rtc.Room) -> None:
        room.on("track_subscribed", self._on_track_subscribed)
        room.on("track_unsubscribed", self._on_track_unsubscribed)
        room.on("transcription_received", self._on_transcription)
        room.on("participant_connected", self._on_participant_connected)
        room.on("participant_attributes_changed", self._on_attributes_changed)
        room.on("disconnected", self._on_disconnected)

    def _on_track_subscribed(self, track, publication, participant) -> None:
        if track.kind != rtc.TrackKind.KIND_AUDIO:
            return
        if participant is not None and participant.identity == self._identity:
            return
        if self._speaker is None:
            return
        self._play_tasks[track.sid] = asyncio.create_task(
            self._play_track(track), name=f"jarvis-play-{track.sid}"
        )
        who = getattr(participant, "identity", "?") or "?"
        self._emit({
            "type": "log", "level": "info", "source": "app",
            "text": f"agent audio track mila ({who})",
        })

    def _on_track_unsubscribed(self, track, publication, participant) -> None:
        task = self._play_tasks.pop(track.sid, None)
        if task is not None:
            task.cancel()

    def _on_transcription(self, segments, participant, publication) -> None:
        if not segments:
            return
        text = "".join(getattr(seg, "text", "") or "" for seg in segments)
        if not text.strip():
            return
        identity = getattr(participant, "identity", "") or ""
        self._emit({
            "type": "transcript",
            "role": "user" if identity == self._identity else "jarvis",
            "text": text,
            "final": all(bool(getattr(seg, "final", False)) for seg in segments),
            "ts": time.time(),
        })

    def _on_participant_connected(self, participant) -> None:
        self._note_participant(participant)

    def _note_participant(self, participant) -> None:
        if participant is None:
            return
        identity = getattr(participant, "identity", "") or "?"
        kind = getattr(participant, "kind", -1)
        attrs: dict = {}
        try:
            attrs = dict(participant.attributes or {})
        except Exception:
            attrs = {}
        try:
            is_agent = int(kind) == int(rtc.ParticipantKind.KIND_AGENT)
        except Exception:
            is_agent = False
        if not is_agent and (ATTRIBUTE_AGENT_STATE in attrs or identity.startswith("agent-")):
            is_agent = True  # fallback for servers/versions reporting another kind
        self._emit({
            "type": "log", "level": "info", "source": "app",
            "text": f"participant connected: {identity} (kind={kind})",
        })
        if not is_agent:
            return
        if not self._agent_seen:
            self._agent_seen = True
            self._emit({
                "type": "log", "level": "info", "source": "app",
                "text": f"Jarvis (agent) room mein aa gaya: {identity}",
            })
        self._apply_agent_state(participant)

    def _on_attributes_changed(self, changed_attributes, participant) -> None:
        if changed_attributes and ATTRIBUTE_AGENT_STATE in changed_attributes:
            self._apply_agent_state(participant)

    def _apply_agent_state(self, participant) -> None:
        try:
            state = str((participant.attributes or {}).get(ATTRIBUTE_AGENT_STATE, "") or "")
        except Exception:
            state = ""
        if not state:
            return
        self._agent_seen = True
        self._emit({"type": "agent_state", "state": state})

    def _on_disconnected(self, *args) -> None:
        code = ""
        if args:
            code = getattr(args[0], "name", "") or str(args[0])
        if self._stop_requested:
            return
        self._emit({
            "type": "log", "level": "warn", "source": "app",
            "text": f"room disconnect ho gaya ({code or 'unknown'})",
        })
        self._emit_status("error", f"Connection toot gaya ({code or 'unknown'}) — Start dobara dabayein")
        if self._stop_evt is not None:
            self._stop_evt.set()

    # ──────────────────────────────
    # Local audio
    # ──────────────────────────────
    async def _start_audio(self) -> None:
        if self._stop_requested:
            return
        cfg = self._cfg
        loop = self._loop or asyncio.get_running_loop()

        try:
            self._speaker = SpeakerOutput(
                device=cfg.get("speaker_device"), muted=self._speaker_muted
            )
            self._speaker.open()
        except Exception as e:
            self._speaker = None
            self._emit({
                "type": "log", "level": "warn", "source": "app",
                "text": f"speaker open nahi hua ({e}) — text mode mein chalega",
            })

        try:
            self._source = rtc.AudioSource(MIC_RATE, 1, queue_size_ms=200, loop=loop)
            self._mic = MicCapture(
                cfg.get("mic_device"), self._source,
                muted=self._mic_muted, on_level=self._on_mic_level,
            )
            self._mic.open()
            self._mic.attach_loop(loop, asyncio.Queue())
            self._pump_task = asyncio.create_task(self._mic.pump(), name="jarvis-mic-pump")
            self._mic_track = rtc.LocalAudioTrack.create_audio_track("jarvis-mic", self._source)
            if self._mic_muted:
                self._mic_track.mute()
            room = self._room
            if room is not None:
                await room.local_participant.publish_track(
                    self._mic_track,
                    rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE),
                )
        except Exception as e:
            self._emit({
                "type": "log", "level": "error", "source": "app",
                "text": f"microphone publish fail ({e}) — text chat phir bhi chalega",
            })
            self._mic = None

        if self._speaker is not None:
            self._emit({
                "type": "log", "level": "info", "source": "app",
                "text": f"speaker ready @ {self._speaker.rate} Hz",
            })
        if self._mic is not None:
            self._emit({
                "type": "log", "level": "info", "source": "app",
                "text": f"microphone ready @ {self._mic.rate} Hz",
            })

    async def _play_track(self, track) -> None:
        speaker = self._speaker
        if speaker is None:
            return
        stream = None
        try:
            stream = rtc.AudioStream(track, sample_rate=speaker.rate, num_channels=1)
            async for ev in stream:
                speaker.push(ev.frame)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._emit({
                "type": "log", "level": "warn", "source": "app",
                "text": f"audio playback ruk gaya: {e}",
            })
        finally:
            if stream is not None:
                try:
                    await stream.aclose()
                except Exception:
                    pass

    def _on_mic_level(self, level: float) -> None:
        self._emit({"type": "mic_level", "level": round(float(level), 3)})
        speaker = self._speaker
        if speaker is not None and level > 0.12 and speaker.playing:
            speaker.clear()  # barge-in: aap bol rahe hain → agent audio foran band

    async def _watch_for_agent(self) -> None:
        try:
            await asyncio.sleep(25.0)
            if not self._agent_seen:
                self._emit({
                    "type": "log", "level": "warn", "source": "app",
                    "text": "25 s mein koi agent join nahi hua — worker log dekhein ya "
                            "`python agent.py dev` manually chalayein",
                })
        except asyncio.CancelledError:
            pass

    # ──────────────────────────────
    # Teardown
    # ──────────────────────────────
    async def _teardown(self) -> None:
        for task in list(self._play_tasks.values()):
            task.cancel()
        self._play_tasks.clear()

        pending = [t for t in (self._watch_task, self._pump_task) if t is not None]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._watch_task = self._pump_task = None

        mic, self._mic = self._mic, None
        if mic is not None:
            mic.close()

        source, self._source = self._source, None
        if source is not None:
            try:
                source.clear_queue()
            except Exception:
                pass
            try:
                await source.aclose()
            except Exception:
                pass
        self._mic_track = None

        speaker, self._speaker = self._speaker, None
        if speaker is not None:
            speaker.close()

        room, self._room = self._room, None
        if room is not None:
            try:
                await room.disconnect()
            except Exception:
                pass

        worker, self._worker = self._worker, None
        if worker is not None:
            try:
                await asyncio.to_thread(worker.stop, 6.0)
            except Exception:
                pass
        self._emit({"type": "mic_level", "level": 0.0})

    def _emit_status(self, state: str, detail: str = "") -> None:
        self._emit({"type": "status", "state": state, "detail": detail})
        if detail:
            level = "error" if state == "error" else "info"
            self._emit({
                "type": "log", "level": level, "source": "app", "text": detail,
            })


# ──────────────────────────────────────────────
# Public engine API (GUI-facing, thread-safe)
# ──────────────────────────────────────────────
class JarvisEngine:
    """Runs the whole Jarvis desktop stack inside one background thread.

    Every method below may be called from the GUI thread: commands are
    marshalled onto the engine loop, events come back through `on_event`.
    """

    def __init__(self, on_event: Optional[Callable[[dict], None]] = None) -> None:
        self._on_event = on_event or (lambda event: None)
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._session: Optional[_LocalSession] = None
        self._lock = threading.Lock()
        self._state = "stopped"
        self.room_name: Optional[str] = None

    # ── state ──
    @property
    def state(self) -> str:
        return self._state

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ── lifecycle ──
    def start(
        self,
        *,
        start_worker: bool = True,
        room_name: Optional[str] = None,
        mic_device: Optional[int] = None,
        speaker_device: Optional[int] = None,
        mic_muted: bool = False,
        speaker_muted: bool = False,
        identity: Optional[str] = None,
        agent_wait: float = 90.0,
    ) -> tuple[bool, str]:
        """Start the desktop stack (worker + room + audio). Non-blocking."""
        with self._lock:
            if self.running:
                return False, "Jarvis pehle se chal raha hai"
            cfg = {
                "start_worker": bool(start_worker),
                "room_name": room_name or None,
                "mic_device": mic_device,
                "speaker_device": speaker_device,
                "mic_muted": bool(mic_muted),
                "speaker_muted": bool(speaker_muted),
                "identity": str(identity or DEFAULT_IDENTITY),
                "agent_wait": float(agent_wait or 90.0),
            }
            self._state = "starting"
            self.room_name = cfg["room_name"]
            self._thread = threading.Thread(
                target=self._run, args=(cfg,), name="jarvis-engine", daemon=True
            )
            self._thread.start()
        self._emit({
            "type": "status", "state": "starting",
            "detail": "Jarvis desktop start ho raha hai…",
        })
        return True, "ok"

    def stop(self, timeout: float = 25.0) -> None:
        """Stop everything: audio, room and the worker child process."""
        thread, session, loop = self._thread, self._session, self._loop
        if thread is None or not thread.is_alive():
            self._thread = None
            self._state = "stopped"
            return
        self._state = "stopping"
        self._emit({"type": "status", "state": "stopping", "detail": "Band kiya ja raha hai…"})
        if session is not None and loop is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(session.request_stop)
            except Exception:
                pass
        thread.join(timeout=max(1.0, float(timeout)))
        if thread.is_alive():
            self._emit({
                "type": "log", "level": "warn", "source": "app",
                "text": "engine thread waqt par band nahi hua — worker force kill kiya",
            })
            if session is not None:
                session.force_stop()
            thread.join(timeout=5.0)
        self._thread = None
        self._state = "stopped"
        self._emit({"type": "status", "state": "stopped", "detail": "Jarvis band ho gaya"})

    # ── commands ──
    def send_text(self, text: str) -> bool:
        """Type a command instead of speaking it."""
        text = (text or "").strip()
        if not text:
            return False
        session, loop = self._session, self._loop
        if session is None or loop is None or loop.is_closed() or not self.running:
            self._emit({
                "type": "log", "level": "warn", "source": "app",
                "text": "Pehle Jarvis start karein (Start button)",
            })
            return False
        fut = asyncio.run_coroutine_threadsafe(session.send_text(text), loop)
        fut.add_done_callback(lambda f: self._future_error(f, "text send"))
        return True

    def set_mic_muted(self, muted: bool) -> None:
        session, loop = self._session, self._loop
        if session is None or loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(session.set_mic_muted, bool(muted))
        except Exception:
            pass

    def set_speaker_muted(self, muted: bool) -> None:
        session, loop = self._session, self._loop
        if session is None or loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(session.set_speaker_muted, bool(muted))
        except Exception:
            pass

    # ── internals ──
    def _emit(self, event: dict) -> None:
        try:
            self._on_event(event)
        except Exception:
            pass

    def _future_error(self, fut, what: str) -> None:
        if fut.cancelled():
            return
        try:
            err = fut.exception()
        except Exception:
            return
        if err is not None:
            self._emit({
                "type": "log", "level": "error", "source": "app",
                "text": f"{what} fail: {err}",
            })

    def _run(self, cfg: dict) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        session = _LocalSession(self._emit, cfg)
        self._session = session
        failure: Optional[str] = None
        try:
            loop.run_until_complete(session.run())
        except Exception as e:
            failure = f"{type(e).__name__}: {e}"
            logger.exception("jarvis engine failed")
        finally:
            self.room_name = session.room_name or self.room_name
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            try:
                loop.close()
            except Exception:
                pass
            self._loop = None
            self._session = None
            self._state = "error" if failure else "stopped"
            if failure:
                self._emit({"type": "status", "state": "error", "detail": failure})
                self._emit({
                    "type": "log", "level": "error", "source": "app",
                    "text": f"engine error: {failure}",
                })
