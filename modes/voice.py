"""Voice mode: speech in; answers on screen and, when the voice is loaded, out loud."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from voice.errors import VoiceError
from voice.listener import VoiceListener
from voice.stt import normalize_text

# Whole-sentence goodbyes only (normalized): a sentence merely containing "quitte" does not match
EXIT_PHRASES = frozenset(
    {
        "au revoir",
        "au revoir monika",
        "quitte",
        "quitte monika",
        "ferme toi",
        "arrete toi",
        "arrete toi monika",
        "on arrete",
        "a plus tard",
        "a plus tard monika",
    }
)

BANNER = (
    "Monika t'écoute. Dis « au revoir » pour quitter (Ctrl+C aussi). Ses réponses "
    "s'affichent à l'écran : elle ne parle pas (voir le message de chargement de la voix)."
)
BANNER_SPOKEN = (
    "Monika t'écoute et te répond à voix haute. Dis « au revoir » pour quitter. "
    "Ctrl+C coupe sa parole ; quand elle t'écoute, il quitte la session."
)


class VoiceReader:
    """Callable that replaces the keyboard prompt: returns the next thing the user says."""

    def __init__(
        self,
        listener: VoiceListener,
        write: Callable[[str], None],
        speaker=None,
        hub=None,
    ) -> None:
        self._hub = hub  # Live state for the web page (optional)
        self._listener = listener
        self._write = write
        self._speaker = speaker

    async def _wait_for_silence(self) -> None:
        """Half duplex: do not listen while Monika talks (the grace delay then covers the echo)."""
        try:
            await self._speaker.wait_idle()
        except asyncio.CancelledError:  # Ctrl+C cuts her voice; the session goes on
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                task.uncancel()
            await self._speaker.cancel()

    async def __call__(self) -> str:
        if self._speaker is not None and self._speaker.busy:
            await self._wait_for_silence()
        self._write("\n🎙  J'écoute…\n")
        if self._hub is not None:
            self._hub.set_state("listening")
        try:
            transcript = await self._listener.listen(on_state=self._on_state)
        except VoiceError as exc:
            self._write(f"[erreur micro] {exc}\n")
            raise EOFError from exc  # Ends the session cleanly
        finally:
            if self._hub is not None:
                self._hub.set_state("idle")
        text = transcript.text.strip() if transcript else ""
        self._write(f"Toi (voix) › {text}\n")
        if normalize_text(text) in EXIT_PHRASES:
            raise EOFError
        return text

    def _on_state(self, state: str) -> None:
        if state == "transcription":
            self._write("  · transcription…\n")
