#!/usr/bin/env bash
# Installe GPT-SoVITS dans vendor/GPT-SoVITS (commit épinglé), avec son propre venv.
#   ./scripts/install_gptsovits.sh              # moteur seul
#   ./scripts/install_gptsovits.sh --models     # + poids pré-entraînés (plusieurs Go, Hugging Face)
#   GPT_SOVITS_DIR=/autre/chemin ./scripts/install_gptsovits.sh
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
DIR=${GPT_SOVITS_DIR:-"$ROOT/vendor/GPT-SoVITS"}
COMMIT=b4570b876f5c
MODELS=0
for arg in "$@"; do
  case "$arg" in
    --models) MODELS=1 ;;
    -h|--help) sed -n '2,7p' "$0"; exit 0 ;;
    *) echo "Option inconnue : $arg" >&2; exit 2 ;;
  esac
done

need() { command -v "$1" >/dev/null 2>&1 || { echo "Manquant : $1" >&2; exit 1; }; }
need git
need python3

if [ ! -d "$DIR/.git" ]; then
  echo "▸ clonage de GPT-SoVITS dans $DIR"
  mkdir -p "$(dirname -- "$DIR")"
  git clone https://github.com/RVC-Boss/GPT-SoVITS "$DIR"
fi
echo "▸ commit $COMMIT"
git -C "$DIR" fetch --quiet origin
git -C "$DIR" checkout --quiet "$COMMIT"

if [ ! -x "$DIR/.venv/bin/python" ]; then
  echo "▸ environnement Python de GPT-SoVITS"
  python3 -m venv "$DIR/.venv"
fi
"$DIR/.venv/bin/python" -m pip install --upgrade pip
echo "▸ dépendances (peut être long : PyTorch et les roues audio)"
"$DIR/.venv/bin/pip" install -r "$DIR/requirements.txt"

if [ "$MODELS" = "1" ]; then
  echo "▸ poids pré-entraînés (Hugging Face)"
  "$DIR/.venv/bin/pip" install --upgrade huggingface_hub
  "$DIR/.venv/bin/huggingface-cli" download lj1995/GPT-SoVITS \
    --local-dir "$DIR/pretrained_models"
fi

cat <<EOF

✓ GPT-SoVITS est prêt dans $DIR
  Démarrage seul : cd "$DIR" && .venv/bin/python api_v2.py -a 127.0.0.1 -p 9880
  Avec Monika    : ./lancer-monika.sh   (utilise vendor/GPT-SoVITS par défaut)
  Pack de voix   : place references/emotions.json et les WAV dans ~/.config/monika/voice/<voix>/
EOF
