"""Streaming voice activity detection with the Silero model bundled in faster-whisper."""

from __future__ import annotations

import os
from typing import Protocol

import numpy as np

from voice.audio import FRAME_SAMPLES

CONTEXT = 64  # Samples of the previous window prepended to each window


class FrameVAD(Protocol):
    def reset(self) -> None: ...
    def probability(self, frame: np.ndarray) -> float: ...


class SileroStreamingVAD:
    """Speech probability for each 32 ms frame; the network state carries over between frames."""

    def __init__(self) -> None:
        import onnxruntime as ort
        from faster_whisper.vad import get_assets_path

        options = ort.SessionOptions()
        options.inter_op_num_threads = options.intra_op_num_threads = 1
        options.log_severity_level = 4
        self._session = ort.InferenceSession(
            os.path.join(get_assets_path(), "silero_vad_v6.onnx"),
            providers=["CPUExecutionProvider"],
            sess_options=options,
        )
        self.reset()

    def reset(self) -> None:
        self._h = np.zeros((1, 1, 128), dtype=np.float32)
        self._c = np.zeros((1, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, CONTEXT), dtype=np.float32)

    def probability(self, frame: np.ndarray) -> float:
        if frame.shape != (FRAME_SAMPLES,):
            raise ValueError(
                f"image audio attendue : {FRAME_SAMPLES} échantillons, reçu {frame.shape}"
            )
        samples = (
            frame.astype(np.float32) / 32768.0
            if frame.dtype == np.int16
            else frame.astype(np.float32)
        )
        window = samples.reshape(1, -1)
        out, self._h, self._c = self._session.run(
            None,
            {
                "input": np.concatenate([self._context, window], axis=1),
                "h": self._h,
                "c": self._c,
            },
        )
        self._context = window[:, -CONTEXT:]
        return float(np.asarray(out).reshape(-1)[0])
