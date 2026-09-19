# Jarvis 4.0 — Voice AI Assistant with Screen Awareness

## v5 — Intelligent Open System (new)

Saying **"Open GTA 5" / "Open VS Code" / "Open Netflix" / "open that racing game"**
now works through **real system discovery + reasoning**, not lookup tables.

- `jarvis_launcher.py` scans Start Menu, Program Files (×2), LocalAppData,
  Desktop, **Steam libraries, Epic manifests, Xbox (GamingRoots), UWP/Store apps**
  and caches results in `jarvis_cache/app_index.json`
  (TTL: `JARVIS_APP_CACHE_TTL` seconds, default 1h; auto-refreshed at startup).
- **`smart_open`** is the single entry point for ANY target (app / game /
  website / file / folder). It decides the type itself, launches Steam games
  via `steam://rungameid`, Epic/Xbox games via their real exes, and asks the
  user only when a match is truly ambiguous.
- **No hardcoded site list**: names are resolved via a learned-sites cache →
  DNS+HTTP-validated URL construction → official-site web search
  (`resolve_website_tool` resolves without opening).
- **Launch memory**: every launch is recorded, so *"the game I played
  yesterday"* / *"آخری گیم کھولو"* reopens the last game.
- Helper tools: `refresh_app_index_tool` (after installing something new),
  `list_discovered_apps_tool` (what's installed).
- `open`, `discover_apps`, `browser_open` and browser URL normalization now
  delegate to this engine — the old `APP_MAPPINGS`, `KNOWN_SITES` and
  `_SITE_ALIASES` dictionaries are gone (only a tiny Windows-protocol /
  offline fallback remains).

Validate without launching anything:

```bash
venv\Scripts\python.exe test_launcher.py       # discovery + matching + sites
venv\Scripts\python.exe test_launcher.py --full  # force a fresh system rescan
```

Voice AI assistant (LiveKit + Gemini realtime) for Windows with
**automatic screen awareness**, explicit screenshots, browser/system/terminal
control, reminders and more.
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
