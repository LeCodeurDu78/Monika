"""Speaker: sentences in, spoken audio out; synthesis runs ahead of playback and can be cut at any time."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any, Protocol

import numpy as np

from voice.errors import PlaybackError, VoiceError
from voice.speech_text import group_blocks

log = logging.getLogger(__name__)

_SENTENCE_DONE = object()  # Marks the end of a sentence that was spoken
_SENTENCE_SKIPPED = object()  # Marks the end of a sentence that failed
MAX_FAILURES = 3  # Consecutive failures after which the voice is switched off for the session

# (text, emotion, intensity) -> text with emotion tags for the engine (see character/tts_emotion.py)
Tagger = Callable[[str, str, float], str]


class _Engine(Protocol):
    async def run(self, fn: Callable[..., Any], *args: Any) -> Any: ...
    def synth(self, text: str) -> Any: ...


class _Sink(Protocol):
    is_open: bool

    async def open(self) -> None: ...
    async def write(self, pcm: np.ndarray) -> None: ...
    async def close(self) -> None: ...
    async def stop(self) -> None: ...


class Speaker:
    """Turns queued sentences into sound, one burst at a time, without ever overlapping speech."""

    def __init__(self, engine: _Engine, sink: _Sink, on_error: Callable[[str], None] | None = None,
                 lookahead_chunks: int = 6, tagger: Tagger | None = None, block_chars: int = 0) -> None:
        self._engine = engine
        self._sink = sink
        self.on_error = on_error  # May be set later, e.g. by the chat session
        self.engine = engine
        self._tagger = tagger  # Emotion tags: None means the engine takes plain text, without tags
        self._mood: tuple[str, float] = ("neutre", 0.0)
        self._block_chars = max(0, block_chars)  # 0 = one sentence per request
        self._block: list[str] = []  # Sentences waiting to be spoken as one block
        self._blocking = False  # Only while a turn is being answered
        self._sentences: asyncio.Queue = asyncio.Queue()
        self._audio: asyncio.Queue = asyncio.Queue(maxsize=lookahead_chunks)
        self._pending = 0  # Sentences accepted but not completely played yet
        self._idle = asyncio.Event()
        self._idle.set()
        self._generation = 0  # Bumped by cancel(): older work is dropped wherever it is
        self._tasks: list[asyncio.Task] = []
        self._streak = 0
        self.disabled_reason: str | None = None
        self.last_error: str | None = None
        self.failures = 0
        self.spoken = 0

    # --- public API ---------------------------------------------------------------------------

    @property
    def busy(self) -> bool:
        """True while a sentence is queued, buffered or being heard."""
        return self._pending > 0 or self._sink.is_open or bool(self._block)

    def set_emotion(self, emotion: str, intensity: float = 0.5) -> None:
        """The mood the next phrases are spoken in; only used when a tagger was given."""
        self._mood = (str(emotion or ""), float(intensity))

    def say(self, text: str, filler: bool = False) -> bool:
        """Queue one sentence; False when it was not accepted (voice off, empty, or a filler while busy)."""
        text = text.strip()
        if not text or self.disabled_reason is not None:
            return False
        if filler and self.busy:
            return False  # A waiting phrase never talks over, or right after, real speech
        if self._blocking and self._block_chars > 0 and not filler:
            self._block.append(text)
            if sum(len(part) for part in self._block) + len(self._block) - 1 >= self._block_chars:
                self._flush_block()  # Reaches the limit: speak what we have, it is one request
            else:
                self._idle.clear()  # Buffered text still counts as speech waiting to be heard
            return True
        self._enqueue(text)
        return True

    def start_block(self) -> None:
        """From now on, sentences are grouped (up to `block_chars`) into a single voice request."""
        self._blocking = self._block_chars > 0

    def flush_block(self) -> None:
        """Speak the sentences buffered so far, without ending the block mode."""
        self._flush_block()

    def end_block(self) -> None:
        """Speak what is buffered and go back to one sentence per request."""
        self._flush_block()
        self._blocking = False

    async def wait_idle(self, timeout: float | None = None) -> bool:
        """Wait until everything queued has been played; False if the timeout came first."""
        self._flush_block()  # Buffered sentences would otherwise never be spoken
        try:
            await asyncio.wait_for(self._idle.wait(), timeout)
        except TimeoutError:
            return False
        return True

    async def cancel(self) -> None:
        """Drop everything queued and cut the sound at once."""
        self._generation += 1
        for queue in (self._sentences, self._audio):
            while not queue.empty():
                queue.get_nowait()
        self._pending = 0
        self._block.clear()
        self._blocking = False
        await self._sink.stop()
        self._idle.set()

    async def close(self) -> None:
        await self.cancel()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    # --- internals ----------------------------------------------------------------------------

    def _enqueue(self, text: str) -> None:
        self._ensure_started()
        self._idle.clear()
        self._pending += 1
        self._sentences.put_nowait((text, self._generation))

    def _flush_block(self) -> None:
        sentences, self._block = self._block, []
        for block in group_blocks(sentences, self._block_chars):
            self._enqueue(block)

    def _tag(self, text: str) -> str:
        """Text with emotion tags; a broken tagger must never cost the sentence."""
        if self._tagger is None:
            return text
        try:
            return self._tagger(text, *self._mood)
        except Exception:
            log.exception("emotion tagging failed, speaking the sentence untagged")
            return text

    def _ensure_started(self) -> None:
        if not self._tasks:
            loop = asyncio.get_running_loop()
            self._tasks = [loop.create_task(self._produce()), loop.create_task(self._consume())]

    def _report(self, message: str) -> None:
        self.failures += 1
        self._streak += 1
        self.last_error = message
        if self._streak >= MAX_FAILURES and self.disabled_reason is None:
            self.disabled_reason = message
            message += " Trop d'échecs de suite : je ne parle plus jusqu'au prochain lancement."
        if self.on_error is not None:
            try:
                self.on_error(message)
            except Exception:
                log.exception("on_error callback failed")

    async def _produce(self) -> None:
        """Synthesize sentences in order, a little ahead of what is being played."""
        end = object()
        while True:
            text, generation = await self._sentences.get()
            if generation != self._generation:
                continue
            stream = None
            outcome = _SENTENCE_SKIPPED
            try:
                stream = self._engine.synth(self._tag(text))
                while True:
                    chunk = await self._engine.run(next, stream, end)
                    if chunk is end:
                        break
                    if generation != self._generation:
                        break  # Cut while synthesizing: stop generating
                    await self._audio.put((generation, chunk))
                if generation == self._generation:
                    self._streak = 0
                    outcome = _SENTENCE_DONE
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if generation == self._generation:
                    self._report(str(exc) if isinstance(exc, VoiceError)
                                 else f"La synthèse vocale a échoué : {type(exc).__name__}: {exc}")
            finally:
                if stream is not None:
                    try:
                        await self._engine.run(stream.close)
                    except Exception:
                        log.debug("could not close the synthesis stream", exc_info=True)
            await self._audio.put((generation, outcome))

    async def _consume(self) -> None:
        """Play the audio; the stream opens with the first chunk and closes when nothing is left."""
        while True:
            generation, item = await self._audio.get()
            if generation != self._generation:
                continue
            try:
                if item is _SENTENCE_DONE or item is _SENTENCE_SKIPPED:
                    self._pending = max(0, self._pending - 1)
                    self.spoken += item is _SENTENCE_DONE
                    if self._pending == 0:
                        await self._sink.close()  # Returns when the last sound has been heard
                        if self._pending == 0 and generation == self._generation:
                            self._idle.set()
                    continue
                if not self._sink.is_open:
                    await self._sink.open()
                    if generation != self._generation:
                        await self._sink.stop()
                        continue
                await self._sink.write(item)
                if generation != self._generation:
                    await self._sink.stop()
            except asyncio.CancelledError:
                raise
            except PlaybackError as exc:
                if generation == self._generation:
                    self._report(str(exc))
                    await self._abort()
            except Exception as exc:
                if generation == self._generation:
                    self._report(f"La lecture audio a échoué : {type(exc).__name__}: {exc}")
                    await self._abort()

    async def _abort(self) -> None:
        """The audio output failed: drop what is queued, like cancel() but keeping the failure count."""
        await self.cancel()
