#!/bin/bash
# Costruisce "MatteoEye per Mac.zip" nella cartella del progetto: l'app per chip Apple e,
# se c'e' Rosetta, anche quella per Mac Intel.
# Requisiti: Python 3.13 "universal2" da python.org (PY per cambiarlo).
set -e
cd "$(dirname "$0")/.."
ROOT=$PWD
OUT="$ROOT/MatteoEye per Mac"
WORK="${TMPDIR:-/tmp}/matteoeye_build_mac"
PY=${PY:-/Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13}
[ -x "$PY" ] || { echo "Python non trovato in $PY (installa Python 3.13 da python.org)"; exit 1; }
rm -rf "$WORK" "$OUT"
mkdir -p "$WORK" "$OUT"

build() {  # $1 = architettura (arm64 | x86_64), $2 = nome dell'app
  local arch=$1 name=$2 venv="$WORK/venv_$1" pre=""
  [ "$arch" = x86_64 ] && pre="arch -x86_64"
  echo "== $name ($arch)"
  $pre "$PY" -m venv "$venv"
  $pre "$venv/bin/pip" install -q --upgrade pip
  $pre "$venv/bin/pip" install -q -r requirements.txt pywebview pyinstaller
  $pre "$venv/bin/pyinstaller" --noconfirm --clean --windowed --name "MatteoEye" \
    --icon "$ROOT/strumenti/MatteoEye.icns" --osx-bundle-identifier it.matteoeye.app \
    --add-data "$ROOT/static:static" \
    --workpath "$WORK/build_$arch" --distpath "$WORK/dist_$arch" --specpath "$WORK/build_$arch" \
    app.py > "$WORK/pyinstaller_$arch.log" 2>&1 || { tail -20 "$WORK/pyinstaller_$arch.log"; exit 1; }
  ditto "$WORK/dist_$arch/MatteoEye.app" "$OUT/$name.app"
}

build arm64 "MatteoEye"
if arch -x86_64 /usr/bin/true 2>/dev/null; then
  build x86_64 "MatteoEye (Mac Intel)"
else
  echo "Rosetta assente: salto la versione per Mac Intel"
fi
cp "strumenti/LEGGIMI - Mac.txt" "$OUT/"
rm -f "$ROOT/MatteoEye per Mac.zip"
ditto -c -k --keepParent "$OUT" "$ROOT/MatteoEye per Mac.zip"
echo "Pronto: $ROOT/MatteoEye per Mac.zip"
