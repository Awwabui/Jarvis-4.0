# ============================================================================
# test_jarvis_desktop.py — headless end-to-end test of the desktop engine
#
# Runs the exact stack the GUI uses (worker + LiveKit room + mic/speaker),
# sends a typed command and waits for Jarvis's spoken reply to come back as a
# transcript. Use it to check the desktop plumbing without opening the window.
#
# Run:
#   venv\Scripts\python.exe test_jarvis_desktop.py                 (full stack)
#   venv\Scripts\python.exe test_jarvis_desktop.py --no-worker     (existing agent.py)
#   venv\Scripts\python.exe test_jarvis_desktop.py --seconds 60 --text "hello"
#   venv\Scripts\python.exe test_jarvis_desktop.py --quiet-mic     (mic muted)
#
# NOTE: this is a LIVE test — it uses your LiveKit/Gemini keys and plays the
# reply through your speakers.
# ============================================================================
import argparse
import queue
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import jarvis_client as jc


def main() -> int:
    parser = argparse.ArgumentParser(description="Jarvis desktop engine — live test")
    parser.add_argument("--seconds", type=float, default=120.0, help="max wait time")
    parser.add_argument("--no-worker", action="store_true", help="do NOT spawn agent.py")
    parser.add_argument("--text", default="Jarvis, ek line mein apna introduction do.",
                        help="typed command to send")
    parser.add_argument("--quiet-mic", action="store_true", help="start with mic muted")
    parser.add_argument("--room", default=None, help="room name (default: auto)")
    args = parser.parse_args()

    print("=== Jarvis desktop engine test ===")
    devices = jc.list_audio_devices()
    print(f"audio: {len(devices['input'])} inputs / {len(devices['output'])} outputs"
          f" (defaults {devices['default_input']}/{devices['default_output']})")
    if devices.get("error"):
        print("audio error:", devices["error"])

    events: queue.Queue = queue.Queue()
    engine = jc.JarvisEngine(on_event=events.put)
    started, message = engine.start(
        start_worker=not args.no_worker,
        room_name=args.room,
        mic_muted=args.quiet_mic,
    )
    print("engine.start:", started, message)
    if not started:
        return 1

    deadline = time.time() + max(20.0, args.seconds)
    connected_at = None
    text_sent_at = None
    reply = None
    failure = None

    while time.time() < deadline:
        try:
            event = events.get(timeout=0.5)
        except queue.Empty:
            continue

        etype = event.get("type")
        if etype == "mic_level":
            continue
        if etype == "log":
            print(f"[{event.get('source')}:{event.get('level')}] {event.get('text')}")
        elif etype == "status":
            print(f"[status] {event.get('state')} — {event.get('detail')}")
            if event.get("state") == "connected" and connected_at is None:
                connected_at = time.time()
            if event.get("state") == "error":
                failure = event.get("detail")
        elif etype == "room":
            print(f"[room] {event.get('name')} ({event.get('sid')}) as {event.get('identity')}")
        elif etype == "agent_state":
            print(f"[agent] {event.get('state')}")
        elif etype == "transcript":
            kind = "FINAL" if event.get("final") else "partial"
            print(f"[{event.get('role')}:{kind}] {event.get('text')}")
            if event.get("role") == "jarvis" and event.get("final"):
                if len((event.get("text") or "").strip()) > 3:
                    reply = event.get("text")

        if connected_at and text_sent_at is None and time.time() - connected_at > 6.0:
            text_sent_at = time.time()
            print(f"[test] typed command bheja: {args.text!r}")
            engine.send_text(args.text)
        if reply and text_sent_at and time.time() - text_sent_at > 4.0:
            break

    print("[test] stopping engine…")
    engine.stop(timeout=35.0)
    print("---")
    print("reply:", (reply or "(koi reply nahi)")[:300])
    if reply:
        print("RESULT: PASS")
        return 0
    if failure:
        print("RESULT: ERROR —", failure)
        return 1
    print("RESULT: FAIL — reply nahi mila (worker log / .env / internet check karein)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
