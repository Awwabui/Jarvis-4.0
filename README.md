# Jarvis 4.0 — Voice AI Assistant with Screen Awareness

Voice-based AI assistant (LiveKit + Gemini realtime) for Windows with
**automatic screen awareness**, explicit screenshots, browser/system/terminal
control, reminders and more.

## Quick start

```bash
python -m venv venv
venv\Scripts\pip install -r requirements.txt
copy .env.example .env        # then fill in your keys (never commit .env)
python agent.py               # boots dev worker + starts everything
```

Required keys in `.env`: `LIVEKIT_URL` / `LIVEKIT_API_KEY` /
`LIVEKIT_API_SECRET` (https://cloud.livekit.io) and `GOOGLE_API_KEY`
(https://aistudio.google.com/apikey). See `.env.example` for every option.

Optional: `python setup_jarvis_chrome.py` (one-time) lets Jarvis control your
real Chrome with `--remote-debugging-port=9222`.

## Screen awareness & screenshots

Run `python agent.py` and screen awareness starts **automatically**:

```
[JARVIS] Initializing...
[JARVIS] AI system ready
[JARVIS] Voice system ready
[JARVIS] Screen awareness enabled (every 2s, model: gemini-3.6-flash)
[JARVIS] Jarvis is ready.
```

How it works (module `vision/`):

```
Screen capture (pyautogui, primary monitor)
  → lightweight change detection (96x54 downsampled diff — no AI)
  → significant change only → vision model analysis (VISION_MODEL)
  → concise context kept in MEMORY (previous frame discarded)
```

- **Level 1 — background awareness**: the agent answers questions like
  *"What's wrong?"*, *"What am I looking at?"*, *"What should I click?"*
  using `get_screen_context_tool` — no "look at my screen" needed.
- **Level 2 — detailed analysis**: *"Explain this error in detail"* triggers
  `analyze_screen_tool` — a fresh screenshot + detailed vision analysis.
- **Explicit screenshot**: *"Take a screenshot"* / *"Capture my screen"* →
  `take_screenshot_desktop_tool` saves
  `Desktop\Jarvis_Screenshot_YYYY-MM-DD_HH-MM-SS.png` (never overwrites;
  the Desktop path is resolved dynamically, OneDrive-redirected Desktops work).
  This works **even when** `SCREEN_AWARENESS=false`.

### Privacy

- Background monitoring **never saves screenshots** — frames are processed in
  memory and discarded; nothing is written to disk or the repository.
- Images are sent only to the configured vision provider (Google) for
  analysis.
- Screenshots are saved to the Desktop **only** when you explicitly ask.

### Configuration (.env)

| Variable | Default | Meaning |
|---|---|---|
| `SCREEN_AWARENESS` | `true` | `false` disables background monitoring |
| `SCREEN_WATCH_INTERVAL` | `2` | seconds between captures (local check, not an AI call) |
| `SCREEN_CHANGE_THRESHOLD` | `8` | change sensitivity (higher = fewer vision calls) |
| `VISION_MODEL` | `gemini-3.6-flash` | vision-capable model (reuses `GOOGLE_API_KEY`) |

## Tests

```bash
venv\Scripts\python.exe test_vision.py     # vision system (real capture + live API)
venv\Scripts\python.exe test_v4_tools.py   # agent config regression
```

## Shutdown

`Ctrl+C` (or stopping the worker) stops the screen watcher cleanly — no
background threads or continued capture after exit.
