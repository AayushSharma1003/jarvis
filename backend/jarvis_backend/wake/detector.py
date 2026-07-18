"""Wake-word detection: the openWakeWord inference chain, vendored.

Three ONNX models run in sequence on 80 ms hops of 16 kHz audio:

    melspectrogram (with 480 samples of left context, spec/10 + 2 transform)
      → speech embedding (sliding 76-frame window → one 96-dim vector per hop)
        → wake classifier (last 16 embeddings → sigmoid score)

Why vendored instead of `pip install openwakeword`: the package drags scipy,
scikit-learn, requests, and tqdm into the bundled sidecar to do what is, at
inference time, three onnxruntime sessions and a ring buffer — and its model
downloader isn't hash-pinned (ours is, see assets.py). The streaming buffer
logic below mirrors openwakeword.utils.AudioFeatures exactly; scores match the
reference implementation once the internal buffers wash out (~2 s). The
openwakeword package remains the right tool for *training* custom wake words
(scripts/train_wake_word.py, dev-only, Phase 5).

Measured on the 8 GB M2: ~2 ms CPU per 80 ms hop ≈ 2.5% of one core.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

SAMPLE_RATE = 16_000
FRAME_SAMPLES = 1280  # 80 ms — the chain's native hop
_MEL_CONTEXT = 480  # samples of left context so frames stack contiguously
_MEL_FRAMES_PER_HOP = 8  # FRAME_SAMPLES / 160-sample melspec hop
_EMB_WINDOW = 76  # melspec frames per embedding
_EMB_DIM = 96
_WAKE_WINDOW = 16  # embeddings per classifier input


class WakeError(Exception):
    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


def _session(path: Path, ort):
    if not path.is_file():
        raise WakeError("WAKE_MODEL_MISSING", str(path))
    opts = ort.SessionOptions()
    opts.inter_op_num_threads = 1
    opts.intra_op_num_threads = 1
    opts.log_severity_level = 3  # keep sidecar stderr readable
    return ort.InferenceSession(str(path), sess_options=opts, providers=["CPUExecutionProvider"])


class WakeDetector:
    """Stateful streaming detector. feed() float32 [-1, 1] mono 16 kHz audio
    in chunks of any size; returns the newest score (0–1) each time a full
    80 ms hop completes, else None. Call reset() when the stream (re)starts."""

    def __init__(self, melspec_path: Path, embedding_path: Path, wake_path: Path):
        try:
            import onnxruntime as ort
        except ImportError as e:
            raise WakeError("WAKE_RUNTIME_MISSING", str(e)) from e
        self._melspec = _session(melspec_path, ort)
        self._embedding = _session(embedding_path, ort)
        self._wake = _session(wake_path, ort)
        self._wake_input = self._wake.get_inputs()[0].name
        self.reset()

    def reset(self) -> None:
        self._pending = np.empty(0, dtype=np.float32)  # int16-scaled samples
        self._tail = np.empty(0, dtype=np.float32)  # last _MEL_CONTEXT samples
        # Reference parity: openwakeword seeds the melspec buffer with ones.
        self._mel = np.ones((_EMB_WINDOW, 32), dtype=np.float32)
        self._features = np.zeros((_WAKE_WINDOW, _EMB_DIM), dtype=np.float32)

    def feed(self, chunk: np.ndarray) -> float | None:
        """Accepts one float32 chunk; returns the latest wake score when at
        least one full 80 ms hop was processed, None while accumulating."""
        scaled = np.clip(chunk.astype(np.float32, copy=False) * 32767.0, -32768.0, 32767.0)
        self._pending = np.concatenate([self._pending, scaled])
        score: float | None = None
        while self._pending.size >= FRAME_SAMPLES:
            block, self._pending = (
                self._pending[:FRAME_SAMPLES],
                self._pending[FRAME_SAMPLES:],
            )
            score = self._process_hop(block)
        return score

    def _process_hop(self, block: np.ndarray) -> float:
        # Melspectrogram over the new hop plus left context: yields exactly
        # 8 new frames (or fewer on the very first hop), stacked contiguously.
        ctx = np.concatenate([self._tail, block])
        self._tail = ctx[-_MEL_CONTEXT:]
        spec = self._melspec.run(None, {"input": ctx[None, :]})[0]
        spec = np.squeeze(spec) / 10.0 + 2.0  # openwakeword's fixed transform
        self._mel = np.vstack([self._mel, spec.astype(np.float32)])[-_EMB_WINDOW:]

        window = self._mel[None, :, :, None]  # [1, 76, 32, 1]
        emb = self._embedding.run(None, {"input_1": window})[0].reshape(1, _EMB_DIM)
        self._features = np.vstack([self._features, emb])[-_WAKE_WINDOW:]

        out = self._wake.run(None, {self._wake_input: self._features[None, :, :]})[0]
        return float(np.squeeze(out))
