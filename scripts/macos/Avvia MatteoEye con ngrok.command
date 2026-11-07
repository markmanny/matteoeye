#!/bin/bash
# Doppio clic: avvia MatteoEye e lo pubblica su internet con ngrok.
# Serve un account ngrok gratuito con il token configurato (vedi LEGGIMI.txt).
cd "$(dirname "$0")/../.."  # cartella principale del progetto
NGROK_CFG="$HOME/Library/Application Support/ngrok/ngrok.yml"
if ! command -v ngrok >/dev/null 2>&1; then
  osascript -e 'display dialog "ngrok non trovato. Installalo da ngrok.com/download e riprova." buttons {"OK"} with title "MatteoEye"'
  exit 1
fi
if ! grep -q '^authtoken:' "$NGROK_CFG" 2>/dev/null; then
  osascript -e 'display dialog "Manca il token di ngrok.\n\n1. Crea un account gratuito su ngrok.com\n2. Nella dashboard apri Your Authtoken e copialo\n3. Apri il Terminale e incolla:\n    ngrok config add-authtoken IL_TUO_TOKEN\n\nPoi riapri questo avviatore." buttons {"OK"} with title "MatteoEye"'
  exit 1
fi
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

APP=""
if ! lsof -tnP -iTCP:8787 -sTCP:LISTEN >/dev/null 2>&1; then
  caffeinate -i ./.venv/bin/python app.py &
  APP=$!
  for i in $(seq 1 30); do curl -s -o /dev/null -m 1 http://127.0.0.1:8787/api/status && break; sleep 1; done
fi

# Dominio fisso (facoltativo): scrivi il dominio gratuito di ngrok nel file ngrok_dominio.txt
DOM=""
[ -f ngrok_dominio.txt ] && DOM=$(tr -d '[:space:]' < ngrok_dominio.txt)
if [ -n "$DOM" ]; then
  ngrok http 8787 --url "https://$DOM" --log=stdout --log-format=logfmt > /dev/null 2>&1 &
else
  ngrok http 8787 --log=stdout --log-format=logfmt > /dev/null 2>&1 &
fi
NG=$!
trap 'kill $NG 2>/dev/null; [ -n "$APP" ] && kill $APP 2>/dev/null' EXIT

URL=""
for i in $(seq 1 20); do
  URL=$(curl -s -m 2 http://127.0.0.1:4040/api/tunnels 2>/dev/null | python3 -I -c 'import sys,json; t=json.load(sys.stdin).get("tunnels",[]); print(t[0]["public_url"] if t else "")' 2>/dev/null)
  [ -n "$URL" ] && break
  sleep 1
done
if [ -z "$URL" ]; then
  osascript -e 'display dialog "ngrok non e partito. Controlla il token (ngrok config check) o riprova tra un minuto." buttons {"OK"} with title "MatteoEye"'
  exit 1
fi
printf '%s' "$URL" | pbcopy
printf "\nMatteoEye e' online su:  %s\n(indirizzo copiato negli appunti; chiudi questa finestra per spegnerlo)\n\n" "$URL"
osascript -e "display dialog \"MatteoEye e' online su:\n\n$URL\n\n(indirizzo copiato negli appunti)\n\nDa fuori serve sempre email, password e codice Blink.\nChiudi la finestra del Terminale per spegnerlo.\" buttons {\"OK\"} with title \"MatteoEye\"" >/dev/null 2>&1 &
wait $NG
