#!/bin/bash
# Doppio clic per avviare Blink Live sul Mac
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  osascript -e 'display dialog "Serve Python 3. Scaricalo da python.org e riprova." buttons {"OK"}'
  exit 1
fi
if [ ! -d .venv ]; then
  echo "Prima installazione, attendi un minuto..."
  python3 -m venv .venv || exit 1
  ./.venv/bin/pip install -q --upgrade pip
  ./.venv/bin/pip install -q -r requirements.txt || exit 1
fi
./.venv/bin/python app.py "$@"
