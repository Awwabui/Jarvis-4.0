# 🤖 Jarvis v4.0 PRO — Windows AI Voice Assistant

A professional, low-latency voice assistant for Windows built on
[LiveKit Agents](https://docs.livekit.io/agents) + **Gemini Realtime**.
Controls your PC: apps, windows, files, terminal, **your real browser
(logged-in native account)**, media, system power — all by voice.

> Designed & built by **Muhammad Awwab**

---

## ⚡ What's new in v4.0 PRO

| Area | Upgrade |
|---|---|
| **Speed** | Realtime thinking disabled (`JARVIS_THINKING_BUDGET=0`), tighter endpointing (0.25s), `preemptive_generation`, HIGH end-of-speech sensitivity, context-window compression → replies start in well under a second |
| **Native browser** | Jarvis **attaches to your real Chrome/Edge via CDP** — your logged-in Gmail/YouTube/anything works. No more blank automation profile |
| **Run commands** | `run_command_tool` — Win+R style (`msconfig`, `services.msc`, `ms-settings:*`, `shell:*`, URLs) |
| **System control** | Lock / sleep / restart / shutdown (confirm-guarded), brightness, recycle bin, DNS flush |
| **Media & volume** | Play/pause/next/prev, exact volume % via pycaw |
| **Processes** | List top processes by RAM, kill any app by name (confirm-guarded) |
| **Windows** | Snap windows: `window_snap_tool` (left/right/top/bottom/center/maximize) |
| **Reminders** | `set_reminder_tool` — Jarvis **speaks** the reminder out loud later |
| **Notes & clipboard** | Timestamped notes to Documents, read/write clipboard |
| **Browser power** | `browser_run_js` (arbitrary JS), `browser_restart_native` |
| **Terminal** | PowerShell runs via EncodedCommand (quotes never break), more blocked destructive patterns |
| **Safer deletes** | Files/folders go to the **Recycle Bin** (send2trash) instead of being destroyed |

---

## 🚀 Setup

```powershell
# 1) Virtual env + deps
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium

# 2) Keys
Copy .env.example → .env  and fill in:
  LIVEKIT_API_KEY / LIVEKIT_API_SECRET / LIVEKIT_URL   (https://cloud.livekit.io)
  GOOGLE_API_KEY                                       (https://aistudio.google.com/apikey)
  GOOGLE_SEARCH_API_KEY + SEARCH_ENGINE_ID             (optional, free DDG fallback exists)
  OPENWEATHER_API_KEY                                  (optional)

# 3) Run
python agent.py dev
```

## 🌐 One-time: native-account browser control

Your normal Chrome can't be automated while it runs without a debug port.
Two options (pick one):

**Automatic (recommended):** just ask Jarvis — *"apna Chrome dobara kholo"*
→ it calls `browser_restart_native`, relaunches Chrome with your real
profile + debug port, and attaches.

**Persistent (best):** run once:

```powershell
python setup_jarvis_chrome.py
```

This creates a desktop shortcut **"Jarvis Chrome"** that always launches
Chrome with `--remote-debugging-port=9222`. Start Chrome from that shortcut
and Jarvis controls your real browser instantly (zero launch delay).

## 🎙️ What you can say

- *"YouTube pe lofi beats chalao"* — plays in your real account
- *"Gmail kholo, mere emails ka khulasa do"*
- *"msconfig kholo"* / *"device manager kholo"*
- *"Chrome left snap karo, VS Code right"*
- *"PC ki health batao"* / *"volume 30% kar do"*
- *"Spotify pause karo"* / *"next track"*
- *"15 minute ka reminder do — meeting ki tayyari"*
- *"Chrome band kar do"* (confirm-guarded kill)
- *"PowerShell mein batayye kitni RAM free hai"*
- *"Is note ko save karo: kal 9 baje doctor"*

## 🔧 Tuning (in `.env`)

| Variable | Default | Meaning |
|---|---|---|
| `JARVIS_THINKING_BUDGET` | `0` | 0 = fastest replies; -1 = model default |
| `JARVIS_LLM_MODEL` | (empty) | e.g. `gemini-3.1-flash-live-preview` |
| `JARVIS_VOICE` | `Charon` | Any Gemini Live voice |
| `JARVIS_CDP_PORT` | `9222` | Debug port for native browser attach |
| `JARVIS_CDP` | `1` | `0` disables CDP attach |
| `JARVIS_BROWSER_HEADLESS` | (empty) | `1` = hidden browser (not recommended) |

## 🗂️ Project map

```
agent.py                 — entrypoint, tuned AgentSession + tool registry
Jarvis_prompts.py        — personality, tool routing, safety
jarvis_browser.py        — Playwright browser layer (CDP native + profiles)
jarvis_system.py         — system control / info / media / volume / processes / clipboard / run-commands / notes
jarvis_reminders.py      — spoken reminders
jarvis_terminal.py       — persistent-dir shell + EncodedCommand PowerShell
Jarvis_window_CTRL.py    — app launch, window focus/snap, file index & operations
keyboard_mouse_CTRL.py   — mouse/keyboard/screen automation
Jarvis_google_search.py  — fast API search (+ DuckDuckGo fallback)
jarvis_get_whether.py    — weather
jarvis_temp.py           — disposable temp files + hourly cleanup
setup_jarvis_chrome.py   — one-time native-Chrome shortcut creator
```

## 🛡️ Safety

Destructive actions (shutdown, process kill, recycle-bin empty, recursive
deletes, registry edits, disk ops) are **confirm-guarded or fully blocked**.
Jarvis asks you first — always.
