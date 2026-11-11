#!/usr/bin/env bash
# Installe Monika dans le dossier du projet : venv, dépendances, .env et wizard.
#   ./scripts/install.sh              # installation + wizard
#   ./scripts/install.sh --no-setup   # installation seule
#   ./scripts/install.sh --voice      # + GPT-SoVITS dans vendor/GPT-SoVITS
#   ./scripts/install.sh --demo       # montre les étapes + wizard en démonstration, sans rien installer
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
PYTHON=${PYTHON:-python3}
SETUP=1
VOICE=0
DEMO=0
for arg in "$@"; do
  case "$arg" in
    --no-setup) SETUP=0 ;;
    --voice) VOICE=1 ;;
    --demo) DEMO=1 ;;
    -h|--help) sed -n '2,7p' "$0"; exit 0 ;;
    *) echo "Option inconnue : $arg" >&2; exit 2 ;;
  esac
done

if [ "$DEMO" = "1" ]; then
  echo "Mode démonstration : rien n'est créé, rien n'est installé."
  echo "▸ $PYTHON -m venv .venv"
  echo "▸ .venv/bin/python -m pip install --upgrade pip"
  echo "▸ .venv/bin/pip install -r requirements.txt"
  echo "▸ cp .env.example .env ; chmod 600 .env"
  echo "▸ cp config/settings.example.json config/settings.json"
  if [ "$VOICE" = "1" ]; then echo "▸ ./scripts/install_gptsovits.sh"; fi
  if [ "$SETUP" = "1" ]; then echo "▸ .venv/bin/python -m monika --setup"; else echo "▸ wizard sauté (--no-setup)"; fi
  echo "▸ ./lancer-monika.sh"
  echo
  if [ "$SETUP" = "1" ] && [ -x .venv/bin/python ]; then
    .venv/bin/python -m monika --setup --demo
  elif [ "$SETUP" = "1" ]; then
    echo "▸ wizard de démonstration sauté : pas de .venv (lance ./scripts/install.sh une fois)."
  fi
  exit 0
fi

"$PYTHON" - <<'PY' || { echo "Python 3.12+ requis." >&2; exit 1; }
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
