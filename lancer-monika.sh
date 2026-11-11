#!/usr/bin/env bash
# Lance Monika avec sa voix : démarre api_v2.py (GPT-SoVITS) en arrière-plan, attend
# qu'il réponde vraiment — une phrase de synthèse passée par le code de Monika, pas
# juste un port ouvert — puis ouvre Monika. Le serveur n'est arrêté en sortant que si
# c'est ce script qui l'a démarré ; un serveur déjà lancé est réutilisé tel quel.
#
#   ./lancer-monika.sh                  # Monika démarre, conversation dans la page web
#   ./lancer-monika.sh --mode voix      # conversation à voix haute
#   ./lancer-monika.sh --config         # même chose, mais Monika affiche sa config et sort
#
# Réglages facultatifs :
#   GPT_SOVITS_DIR     dossier de GPT-SoVITS        (défaut vendor/GPT-SoVITS, puis ~/Applications/GPT-SoVITS)
#   GPT_SOVITS_WAIT_S  attente maximale au démarrage (défaut 90 secondes)
#   GPT_SOVITS_LOG     journal du serveur de voix    (défaut ~/.config/monika/logs/gptsovits.log)
set -u

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PY=$ROOT/.venv/bin/python
if [ -n "${GPT_SOVITS_DIR:-}" ]; then
    TTS_DIR=$GPT_SOVITS_DIR
elif [ -f "$ROOT/vendor/GPT-SoVITS/api_v2.py" ]; then
    TTS_DIR=$ROOT/vendor/GPT-SoVITS
else
    TTS_DIR=$HOME/Applications/GPT-SoVITS
fi
WAIT_S=${GPT_SOVITS_WAIT_S:-90}
LOG=${GPT_SOVITS_LOG:-$HOME/.config/monika/logs/gptsovits.log}

die() { printf '\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }
say() { printf '\033[36m▸ %s\033[0m\n' "$*"; }
warn() { printf '\033[33m⚠ %s\033[0m\n' "$*"; }

[ -x "$PY" ] || die "Pas d'environnement ici : ./scripts/install.sh"

# --- Ce que la configuration dit de la voix -----------------------------------------
read -r ENABLED TTS_URL < <(cd "$ROOT" && "$PY" -c '
from monika.config import get_settings
s = get_settings()
print(int(s.tts_enabled), s.tts_url)' 2>/dev/null) || die "Configuration illisible : python -m monika --config pour voir l'erreur."
[ -n "${TTS_URL:-}" ] || die "Configuration illisible : python -m monika --config pour voir l'erreur."

read -r HOST PORT < <("$PY" - "$TTS_URL" <<'PY'
import sys
from urllib.parse import urlparse
url = urlparse(sys.argv[1])
host = url.hostname or "127.0.0.1"
print("127.0.0.1" if host == "localhost" else host, url.port or (443 if url.scheme == "https" else 80))
PY
)

listening() { (exec 3<>"/dev/tcp/$HOST/$PORT") 2>/dev/null; }

SRV_PID=""
cleanup() {
    if [ -n "$SRV_PID" ] && kill -0 "$SRV_PID" 2>/dev/null; then
        say "arrêt du serveur de voix (pid $SRV_PID)"
        kill -TERM "$SRV_PID" 2>/dev/null
        wait "$SRV_PID" 2>/dev/null
    fi
}
trap cleanup EXIT
trap 'exit 130' INT TERM

# --- Démarrage du serveur ------------------------------------------------------------
if [ "$ENABLED" != "1" ]; then
    say "voix désactivée (TTS_ENABLED=false) : Monika démarre sans serveur de synthèse"
elif listening; then
    say "serveur de voix déjà en écoute sur $HOST:$PORT, je le réutilise"
elif [ "$HOST" = "127.0.0.1" ] || [ "$HOST" = "::1" ]; then
    [ -f "$TTS_DIR/api_v2.py" ] || die "Serveur introuvable : $TTS_DIR/api_v2.py. Lance ./scripts/install_gptsovits.sh ou indique GPT_SOVITS_DIR."
    [ -x "$TTS_DIR/.venv/bin/python" ] || die "Pas d'environnement dans $TTS_DIR : relance ./scripts/install_gptsovits.sh."
    mkdir -p "$(dirname -- "$LOG")"
    say "démarrage de api_v2.py depuis $TTS_DIR (journal : $LOG)"
    ( cd "$TTS_DIR" && exec .venv/bin/python api_v2.py -a "$HOST" -p "$PORT" ) >>"$LOG" 2>&1 &
    SRV_PID=$!
    for _ in $(seq 1 "$WAIT_S"); do
        kill -0 "$SRV_PID" 2>/dev/null || { tail -15 "$LOG" >&2; die "api_v2.py s'est arrêté au démarrage (voir $LOG)."; }
        listening && break
        sleep 1
    done
    listening || { tail -15 "$LOG" >&2; die "rien sur $HOST:$PORT après ${WAIT_S}s (voir $LOG)."; }
    say "en écoute sur $HOST:$PORT"
else
    warn "serveur distant $HOST:$PORT : je ne le démarre pas, Monika l'utilisera s'il répond"
fi

# --- Contrôle : une vraie synthèse, avec le pack de voix de Monika --------------------
CONTROL=""
if [ "$ENABLED" = "1" ]; then
    say "contrôle : une phrase de synthèse avec le pack de voix…"
    CONTROL=$(cd "$ROOT" && timeout 180 "$PY" - 2>&1 <<'PY'
import asyncio

from monika.config import get_settings
from monika.paths import Paths
from voice.errors import VoiceError
from voice.gptsovits import GptSovitsEngine

settings = get_settings()
engine = GptSovitsEngine(settings, Paths.from_settings(settings))
try:
    warm = asyncio.run(engine.load())
    blocks = list(engine.synth("Bonjour, la voix est prête."))
except VoiceError as exc:
    print(f"ECHEC {exc}")
    raise SystemExit(1)
except Exception as exc:  # serveur muet, JSON inattendu, timeout…
    print(f"ECHEC {type(exc).__name__} : {exc}")
    raise SystemExit(1)
seconds = sum(len(block) for block in blocks) / engine.sample_rate
tags = ", ".join(sorted(engine.available_tags or ())) or "aucun"
print(f"OK {engine.character_name} · {engine.sample_rate} Hz · tags : {tags} · {seconds:.1f}s de voix en {warm:.1f}s")
PY
)
    case "$CONTROL" in
        OK\ *) say "voix prête : ${CONTROL#OK }" ;;
        ECHEC\ *) warn "la voix ne répond pas comme prévu : ${CONTROL#ECHEC }" ;;
        *) warn "contrôle impossible : ${CONTROL:-pas de réponse}" ;;
    esac
    case "$CONTROL" in
        ECHEC*|"") warn "Monika démarre quand même : elle parlera d'une voix neutre, ou se taira si le pack est inutilisable." ;;
    esac
fi

# --- Monika -------------------------------------------------------------------------
cd "$ROOT" || die "dossier du projet inaccessible : $ROOT"
say "ouverture de Monika"
"$PY" -m monika "$@"
