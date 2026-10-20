"""Streaming text-to-speech: speak sentence by sentence while the model is still writing.

    text deltas -> SentenceChunker -> [synthesis thread] -> [playback thread]

Synthesis of sentence N+1 overlaps the playback of sentence N, so Monika starts talking after the first
sentence instead of after the whole reply. The synthesis and playback functions are injected: the local
speakers (voice/voice_tts.py) and a Discord call use the same pipeline with different outputs.
"""

import queue
import re
import threading
from typing import Callable, Optional

from core.settings import settings

CODE_NOTICE = " J'ai affiché du code dans le terminal. "

_BOUNDARY = re.compile(r"(?<=[.!?…])\s+|\n+")
_TRAILING_TICKS = re.compile(r"`{1,2}$")


def clean_for_speech(text: str) -> str:
    """Strip Markdown and URLs that would be read out loud."""
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"https?://\S+", "un lien", text)
    text = re.sub(r"[*_`#>]+", "", text)
    return re.sub(r"\s+", " ", text).strip()


class _FenceFilter:
    """Replace fenced code blocks by a short notice; copes with fences split across deltas."""

    def __init__(self) -> None:
        self._in_code = False
        self._hold = ""  # 1-2 trailing backticks that may be the start of a fence

    def feed(self, delta: str) -> str:
        data, self._hold, out = self._hold + delta, "", []
        while data:
            fence = data.find("```")
            if self._in_code:
                if fence < 0:
                    tick = _TRAILING_TICKS.search(data)
                    self._hold = tick.group() if tick else ""
                    break
                data, self._in_code = data[fence + 3 :], False
                out.append(CODE_NOTICE)
            else:
                if fence < 0:
                    tick = _TRAILING_TICKS.search(data)
                    if tick:
                        self._hold, data = tick.group(), data[: tick.start()]
                    out.append(data)
                    break
                out.append(data[:fence])
                data, self._in_code = data[fence + 3 :], True
        return "".join(out)

    def flush(self) -> str:
        self._hold = ""
        return ""


class SentenceChunker:
    """Turn a stream of text deltas into speakable chunks (whole sentences, never tiny fragments)."""

    def __init__(self, min_chars: Optional[int] = None, max_chars: Optional[int] = None) -> None:
        self._min = settings.VOICE_STREAM_MIN_CHARS if min_chars is None else min_chars
        self._max = settings.VOICE_STREAM_MAX_CHARS if max_chars is None else max_chars
        self._fence = _FenceFilter()
        self._buffer = ""
        self._acc = ""  # short sentences waiting to be merged into one chunk of at least _min chars

    def feed(self, delta: str) -> list[str]:
        self._buffer += self._fence.feed(delta)
        return self._drain(final=False)

    def flush(self) -> list[str]:
        self._buffer += self._fence.flush()
        return self._drain(final=True)

    def _drain(self, final: bool) -> list[str]:
        pieces, last_end = [], 0
        for match in _BOUNDARY.finditer(self._buffer):
            piece = self._buffer[last_end : match.start()].strip()
            last_end = match.end()
            if piece:
                pieces.append(piece)
        self._buffer = self._buffer[last_end:]

        while len(self._buffer) > self._max:  # long run without punctuation: cut at a comma or a space
            window = self._buffer[: self._max]
            cut = max(window.rfind(", "), window.rfind("; "), window.rfind(" "))
            if cut < self._max // 2:
                cut = self._max
            pieces.append(self._buffer[:cut].strip().rstrip(",;"))
            self._buffer = self._buffer[cut:].lstrip(" ,;")

        if final and self._buffer.strip():
            pieces.append(self._buffer.strip())
            self._buffer = ""

        chunks = []
        for piece in pieces:
            self._acc = f"{self._acc} {piece}".strip()
            if len(self._acc) >= self._min:
                chunks.append(self._acc)
                self._acc = ""
        if final and self._acc:
            chunks.append(self._acc)
            self._acc = ""
        return chunks


_END = object()


class SpeechStream:
    """One spoken reply. Feed it text deltas, then call finish() to wait for the end of the speech."""

    def __init__(
        self,
        synthesize: Callable[[str], Optional[tuple]],
        play: Callable[[object, int, str], None],
    ) -> None:
        self._synthesize = synthesize
        self._play = play
        self._chunker = SentenceChunker()
        self._lock = threading.Lock()
        self._text_q: "queue.Queue" = queue.Queue()
        self._audio_q: "queue.Queue" = queue.Queue(maxsize=2)  # small: do not synthesize far ahead of playback
        self._threads: list[threading.Thread] = []
        self.spoke_anything = False

    # --- input --------------------------------------------------------------------------
    def feed(self, delta: str) -> None:
        if not delta:
            return
        with self._lock:
            chunks = self._chunker.feed(delta)
        for chunk in chunks:
            self._enqueue(chunk)

    def say(self, text: str) -> None:
        """Queue a complete phrase (e.g. a waiting phrase) in order with the rest of the speech."""
        self._enqueue(text)

    def _enqueue(self, text: str) -> None:
        cleaned = clean_for_speech(text)
        if not cleaned:
            return
        self._ensure_started()
        self._text_q.put(cleaned)

    def _ensure_started(self) -> None:
        with self._lock:
            if self._threads:
                return
            for target, name in ((self._synth_loop, "tts-synth"), (self._play_loop, "tts-play")):
                thread = threading.Thread(target=target, daemon=True, name=name)
                thread.start()
                self._threads.append(thread)

    # --- workers ------------------------------------------------------------------------
    def _synth_loop(self) -> None:
        while True:
            text = self._text_q.get()
            if text is _END:
                self._audio_q.put(_END)
                return
            try:
                result = self._synthesize(text)
            except Exception as exc:
                print(f"⚠️ [TTS] Synthesis failed ({type(exc).__name__}: {exc}).")
                continue
            if result is not None:
                audio, sample_rate = result
                self._audio_q.put((text, audio, sample_rate))

    def _play_loop(self) -> None:
        while True:
            item = self._audio_q.get()
            if item is _END:
                return
            text, audio, sample_rate = item
            self.spoke_anything = True
            try:
                self._play(audio, sample_rate, text)
            except Exception as exc:
                print(f"⚠️ [TTS] Playback failed ({type(exc).__name__}: {exc}).")

    # --- end ----------------------------------------------------------------------------
    def finish(self) -> None:
        """Flush the last partial sentence and wait until everything has been spoken."""
        with self._lock:
            tail = self._chunker.flush()
        for chunk in tail:
            self._enqueue(chunk)
        if self._threads:
            self._text_q.put(_END)
            for thread in self._threads:
                thread.join()
