#!/bin/bash
# Sets up the Python environment on first run, then starts Jarvis.
#   ./run.sh              lives in the notch and menu bar, like Siri (say "Hey Jarvis")
#   ./run.sh --dashboard  same, and open the dashboard window right away
#   ./run.sh --browser    dashboard in your browser, no notch
#   ./run.sh --voice    old terminal-only voice mode
#   ./run.sh --text     terminal, type instead of speaking
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ] || ! .venv/bin/python -c "import webview, fastapi, websockets" 2>/dev/null; then
  echo "Installing dependencies (first run takes a minute or two)..."
  [ -x .venv/bin/python ] || uv venv --python 3.12 .venv || exit 1
  uv pip install --python .venv/bin/python -r requirements.txt || exit 1
fi
case "$1" in
  --voice) shift; exec .venv/bin/python jarvis.py "$@" ;;
  --text|--tools) exec .venv/bin/python jarvis.py "$@" ;;
  *) exec .venv/bin/python app.py "$@" ;;
esac
