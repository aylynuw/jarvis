# Jarvis

A voice assistant for the Mac. Say "Hey Jarvis" and a HUD panel drops down from the top of the screen; ask out loud and Claude Code answers.

- School timetable and lunch menu (Korea's NEIS open API)
- Gmail: read and write drafts (it can't send or delete)
- Google Calendar: read and add events
- Reminders, timers, opening apps, volume and music control
- Long-term memory, and step-by-step help with homework files
- A dashboard window and a 3D hologram core (three.js)

The app's screens are in Korean. Jarvis replies in whatever language you speak to it.

## Requirements

- macOS (Apple Silicon recommended), Python 3.12, [uv](https://docs.astral.sh/uv/)
- The [Claude Code](https://claude.com/claude-code) CLI, signed in
- For Gmail, Calendar and Drive: connect those connectors on claude.ai

## Run

```bash
cp .env.example .env     # your school codes, grade, class and city
./run.sh                 # menu bar icon, listening for "Hey Jarvis"
./run.sh --dashboard     # also open the dashboard window
./run.sh --browser       # dashboard only, in your web browser
```

The first run creates `.venv` and installs the dependencies. Allow microphone access when macOS asks.

## Layout

| File | What it does |
| --- | --- |
| `app.py` | Menu bar icon, drop-down HUD window, dashboard window (pywebview) |
| `server.py` | Local server (127.0.0.1:8765) and WebSocket |
| `jarvis.py` | Wake word, speech recognition (faster-whisper), calls Claude Code, speaks the reply |
| `tools_server.py` | Jarvis's own MCP tools (timetable, lunch, Mac control, memory) |
| `ui/` | Dashboard and HUD pages, 3D core |

Personal settings (`.env`), memories (`data/`) and uploaded files are never committed.

three.js is included in `ui/vendor/three` under the MIT license.
