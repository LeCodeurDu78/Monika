import os
import signal
import subprocess
from pathlib import Path

from core.settings import settings

_MAX_OUTPUT_CHARS = 20000


def _clip(text: str) -> str:
    """Keep the start and the end of a long output (errors are often at the end)."""
    if len(text) <= _MAX_OUTPUT_CHARS:
        return text
    half = _MAX_OUTPUT_CHARS // 2
    return f"{text[:half]}\n... [{len(text) - _MAX_OUTPUT_CHARS} caractères omis] ...\n{text[-half:]}"


def run_script(command: str, workdir: str = None, timeout: int = None) -> str:
    """Run a command in the local terminal."""
    try:
        target_dir = Path(workdir).expanduser() if workdir else Path.home()
        if not target_dir.exists():
            return f"Erreur : Le répertoire de travail '{target_dir}' n'existe pas."

        limit = settings.SCRIPT_TIMEOUT_SECONDS if timeout in (None, 0) else int(timeout)
        limit = max(1, min(limit, settings.SCRIPT_MAX_TIMEOUT_SECONDS))

        # New process group: on timeout the shell's children are killed too
        # (otherwise a `sleep` or a server started by the command would survive).
        proc = subprocess.Popen(
            command,
            shell=True,
            cwd=str(target_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            text=True,
            errors="replace",
            start_new_session=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=limit)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            stdout, stderr = proc.communicate()
            partial = []
            if (stdout or "").strip():
                partial.append(f"--- STDOUT (partiel) ---\n{_clip(stdout.strip())}")
            if (stderr or "").strip():
                partial.append(f"--- STDERR (partiel) ---\n{_clip(stderr.strip())}")
            return (
                f"⚠️ Commande interrompue : temps limite de {limit} s dépassé. "
                f"Relance avec un paramètre timeout plus grand (max {settings.SCRIPT_MAX_TIMEOUT_SECONDS} s) "
                "ou lance-la en arrière-plan (nohup ... &).\n" + "\n".join(partial)
            ).strip()

        output = []
        if stdout.strip():
            output.append(f"--- STDOUT ---\n{_clip(stdout.strip())}")
        if stderr.strip():
            output.append(f"--- STDERR ---\n{_clip(stderr.strip())}")

        if not output:
            return f"Commande exécutée avec succès (Code de sortie : {proc.returncode}, aucune sortie texte)."

        return f"Code de sortie : {proc.returncode}\n" + "\n".join(output)

    except Exception as e:
        return f"Erreur lors de l'exécution du script : {str(e)}"
