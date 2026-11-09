#!/usr/bin/env bash
# Installe Monika dans le dossier du projet : venv, dépendances, .env et wizard.
#   ./scripts/install.sh              # installation + wizard
#   ./scripts/install.sh --no-setup   # installation seule
#   ./scripts/install.sh --voice      # + GPT-SoVITS dans vendor/GPT-SoVITS
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
PYTHON=${PYTHON:-python3}
SETUP=1
VOICE=0
for arg in "$@"; do
  case "$arg" in
    --no-setup) SETUP=0 ;;
    --voice) VOICE=1 ;;
    -h|--help) sed -n '2,7p' "$0"; exit 0 ;;
    *) echo "Option inconnue : $arg" >&2; exit 2 ;;
  esac
done

"$PYTHON" - <<'PY' || { echo "Python 3.12+ est requis." >&2; exit 1; }
import sys
raise SystemExit(0 if sys.version_info >= (3, 12) else 1)
PY

cd "$ROOT"
if [ ! -x .venv/bin/python ]; then
  echo "▸ création de .venv"
  "$PYTHON" -m venv .venv
fi
.venv/bin/python -m pip install --upgrade pip
echo "▸ installation des dépendances"
.venv/bin/pip install -r requirements.txt

if [ ! -f .env ]; then
  echo "▸ .env créé depuis .env.example"
  cp .env.example .env
  chmod 600 .env
fi
mkdir -p config
if [ ! -f config/settings.json ]; then
  cp config/settings.example.json config/settings.json
fi

if [ "$VOICE" = "1" ]; then
  ./scripts/install_gptsovits.sh
fi
if [ "$SETUP" = "1" ]; then
  .venv/bin/python -m monika --setup
fi
echo "✓ Installation terminée. Lance : ./lancer-monika.sh"
