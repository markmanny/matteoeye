#!/bin/bash
# Doppio clic: avvia Blink Live visibile dagli altri dispositivi
# (rete di casa e, se installato, Tailscale per l'accesso da fuori casa).
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

# Se Blink Live e' gia' acceso (es. con l'altro avviatore) lo spegniamo e lo riavviamo in rete
PID=$(lsof -tnP -iTCP:8787 -sTCP:LISTEN 2>/dev/null)
if [ -n "$PID" ]; then
  kill $PID 2>/dev/null
  for i in 1 2 3 4 5 6 7 8 9 10; do lsof -tnP -iTCP:8787 -sTCP:LISTEN >/dev/null 2>&1 || break; sleep 1; done
fi

LAN_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null)
TS_BIN=""; TS_IP=""; FUNNEL_URL=""; ENABLE_URL=""
for t in tailscale /Applications/Tailscale.app/Contents/MacOS/Tailscale; do
  if command -v "$t" >/dev/null 2>&1; then TS_BIN="$t"; break; fi
done
if [ -n "$TS_BIN" ]; then
  TS_IP=$("$TS_BIN" ip -4 2>/dev/null | head -1)
  if [ -n "$TS_IP" ]; then
    # Link pubblico (Tailscale Funnel): utilizzabile da qualsiasi PC o telefono, ovunque.
    # Viene spento quando si chiude questa finestra.
    FUNNEL_URL=$("$TS_BIN" funnel status 2>/dev/null | grep -oE 'https://[^ /]+' | head -1)
    if [ -z "$FUNNEL_URL" ]; then
      OUT=$("$TS_BIN" funnel --bg 8787 2>&1)
      FUNNEL_URL=$(echo "$OUT" | grep -oE 'https://[^ /]+\.ts\.net' | head -1)
      ENABLE_URL=$(echo "$OUT" | grep -oE 'https://login\.tailscale\.com[^ ]*' | head -1)
    fi
    [ -n "$FUNNEL_URL" ] && trap '"$TS_BIN" funnel reset >/dev/null 2>&1' EXIT
  fi
fi

MSG="Blink Live e' acceso. Indirizzi da aprire:"
[ -n "$LAN_IP" ] && MSG="$MSG\n\nIn casa (stesso Wi-Fi):\nhttp://$LAN_IP:8787"
if [ -n "$FUNNEL_URL" ]; then
  MSG="$MSG\n\nDa qualsiasi PC o telefono, ovunque (link pubblico, copiato negli appunti):\n$FUNNEL_URL"
  printf '%s' "$FUNNEL_URL" | pbcopy
elif [ -n "$ENABLE_URL" ]; then
  MSG="$MSG\n\nPer avere il link pubblico apri questo indirizzo, premi Enable e poi riavvia Blink Live:\n$ENABLE_URL"
  open "$ENABLE_URL"
elif [ -n "$TS_IP" ]; then
  MSG="$MSG\n\nDai tuoi dispositivi con Tailscale acceso:\nhttp://$TS_IP:8787"
else
  MSG="$MSG\n\nPer usarla da fuori casa installa Tailscale sul Mac e accedi (vedi LEGGIMI.txt)"
fi
MSG="$MSG\n\nDagli altri dispositivi serve email, password e codice Blink.\nChiudi la finestra del Terminale per spegnerlo."
printf "%b\n\n" "$MSG"
osascript -e "display dialog \"$MSG\" buttons {\"OK\"} with title \"Blink Live\"" >/dev/null 2>&1 &

# caffeinate evita che il Mac vada in stop mentre Blink Live e' acceso
caffeinate -i ./.venv/bin/python app.py --lan
