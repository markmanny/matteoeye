#!/bin/bash
# Prepara "MatteoEye per Windows.zip" lavorando dal Mac: Python "embeddable" di python.org
# piu' i pacchetti nella versione per Windows (scaricati con pip, senza bisogno di un PC Windows).
# Requisiti: pip del progetto (./.venv/bin/pip, oppure PIP=...), curl, zip.
set -e
cd "$(dirname "$0")/.."
ROOT=$PWD
PYVER=${PYVER:-3.13.17}          # versione di Python da python.org (serie 3.13)
OUT="$ROOT/MatteoEye per Windows"
WORK="${TMPDIR:-/tmp}/matteoeye_build_win"
PIP=${PIP:-./.venv/bin/pip}
[ -x "$PIP" ] || PIP=pip3
rm -rf "$WORK" "$OUT"
mkdir -p "$WORK" "$OUT/python/Lib" "$OUT/static"

echo "== Python $PYVER embeddable"
curl -sSL -o "$WORK/python-embed.zip" "https://www.python.org/ftp/python/$PYVER/python-$PYVER-embed-amd64.zip"
unzip -q "$WORK/python-embed.zip" -d "$OUT/python"
# percorsi di ricerca dei moduli (file ._pth, righe con fine riga Windows)
printf 'python313.zip\r\n.\r\nLib\\site-packages\r\n' > "$OUT/python/python313._pth"

echo "== pacchetti per Windows"
SP="$OUT/python/Lib/site-packages"
pulisci() { rm -rf "$SP/bin"; find "$SP" -name "__pycache__" -type d -prune -exec rm -rf {} +; }
"$PIP" install -q --target "$SP" --platform win_amd64 --python-version 3.13 --implementation cp --only-binary=:all: -r requirements.txt
pulisci
"$PIP" install -q --target "$SP" --no-deps proxy_tools
pulisci
"$PIP" install -q --target "$SP" --no-deps --platform win_amd64 --python-version 3.13 --implementation cp --only-binary=:all: \
  pywebview pythonnet clr-loader cffi pycparser bottle
pulisci

echo "== file del programma"
cp app.py "$OUT/"
cp static/index.html static/mpegts.js "$OUT/static/"
cp "strumenti/LEGGIMI - Windows.txt" "$OUT/"
printf '@echo off\r\ncd /d "%%~dp0"\r\nstart "MatteoEye" /min "%%~dp0python\\python.exe" "%%~dp0app.py" --app\r\n' > "$OUT/Avvia MatteoEye.bat"
find "$OUT" -name ".DS_Store" -delete

rm -f "$ROOT/MatteoEye per Windows.zip"
(cd "$ROOT" && zip -q -r -X "MatteoEye per Windows.zip" "MatteoEye per Windows" -x "*.DS_Store")
echo "Pronto: $ROOT/MatteoEye per Windows.zip"
