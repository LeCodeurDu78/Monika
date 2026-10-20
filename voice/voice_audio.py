"""Microphone capture with end-of-sentence detection (VAD) and audio playback."""

import sys

import numpy as np
import sounddevice as sd
import webrtcvad

from web import state as avatar_state
from core.settings import settings

SAMPLE_RATE = settings.SAMPLE_RATE
FRAME_MS = settings.FRAME_MS
VAD_AGGRESSIVENESS = settings.VOICE_VAD_AGGRESSIVENESS
SILENCE_MS = settings.VOICE_SILENCE_MS
MAX_RECORD_SECONDS = settings.VOICE_MAX_RECORD_SECONDS

FRAME_SAMPLES = int(SAMPLE_RATE * FRAME_MS / 1000)


def record_until_silence() -> np.ndarray:
    """Record from the default microphone until SILENCE_MS of silence."""
    vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)
    silence_frames_needed = max(1, SILENCE_MS // FRAME_MS)
    max_frames = int(MAX_RECORD_SECONDS * 1000 / FRAME_MS)

    frames = []
    silent_run = 0
    speech_started = False

    print("Je t'écoute... (parle, je m'arrête tout seul après le silence)")
    avatar_state.set_state("listening")

    try:
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=FRAME_SAMPLES) as stream:
            for _ in range(max_frames):
                block, overflowed = stream.read(FRAME_SAMPLES)
                if overflowed:
                    print(
                        "Buffer audio saturé, un fragment a peut-être été perdu.",
                        file=sys.stderr,
                    )

                is_speech = vad.is_speech(block.tobytes(), SAMPLE_RATE)
                frames.append(block.copy())

                if is_speech:
                    speech_started = True
                    silent_run = 0
                elif speech_started:
                    silent_run += 1
                    if silent_run >= silence_frames_needed:
                        break
    finally:
        # The mic is closed again: either we go on to transcribe ("thinking"), or nothing was said
        # and we go straight back to rest.
        avatar_state.set_state("thinking" if (frames and speech_started) else "idle")

    if not frames or not speech_started:
        return np.array([], dtype=np.float32)

    audio_int16 = np.concatenate(frames, axis=0).flatten()
    return audio_int16.astype(np.float32) / 32768.0


def play_audio(audio_float32: np.ndarray, sample_rate: int) -> None:
    """Play a float32 [-1, 1] audio array on the default output."""
    if audio_float32.size == 0:
        return
    sd.play(audio_float32, samplerate=sample_rate, blocking=True)
