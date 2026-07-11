"""Wake-word detection ("Hey Jarvis" / "Hey Friday") via openWakeWord.

Optional heavy dependency (`uv sync --extra handsfree`); models download on
first use. The detector interface is one method — `detect(frame) -> bool` —
so tests and the hands-free session never care what's behind it.
"""

from __future__ import annotations

WAKEWORD_INSTALL_HINT = (
    "Hands-free mode needs the wake-word dependencies (missing or broken\n"
    "openwakeword install). Rebuild the environment with:\n"
    "  rm -rf .venv && uv sync --extra handsfree"
)


def wakeword_available() -> bool:
    # Check the exact modules the detector uses: a mangled install (e.g.
    # leftovers from a manual pip install later pruned by `uv sync`) can
    # have an importable `openwakeword` with missing submodules.
    try:
        import openwakeword.utils  # noqa: F401
        from openwakeword.model import Model  # noqa: F401
    except ImportError:
        return False
    return True


BLOCK_SAMPLES = 1280  # openWakeWord is designed for 80 ms @ 16 kHz blocks


class WakeWordDetector:
    """Feed 16 kHz audio frames of any size; detect() fires once per activation."""

    def __init__(self, model: str = "hey_jarvis", threshold: float = 0.6):
        # Explicit submodule import: some openwakeword versions don't bind
        # `utils` on the package from a bare `import openwakeword`.
        import numpy as np
        import openwakeword.utils
        from openwakeword.model import Model

        try:
            self._model = Model(wakeword_models=[model], inference_framework="onnx")
        except Exception:
            # The pip package ships without model files; fetch them once
            # (wake-word models + melspec/embedding feature models).
            print("Downloading wake-word models (first run)…")
            openwakeword.utils.download_models()
            self._model = Model(wakeword_models=[model], inference_framework="onnx")
        self.threshold = threshold
        self.last_score = 0.0
        self._buffer = np.zeros(0, dtype=np.int16)
        self._cooldown = 0

    def detect(self, frame) -> bool:
        import numpy as np

        if frame.dtype != np.int16:
            frame = (np.clip(frame, -1.0, 1.0) * 32767).astype(np.int16)
        self._buffer = np.concatenate([self._buffer, frame])
        fired = False
        # Feed the model exactly the block size it was trained for; smaller
        # chunks silently degrade its feature pipeline.
        while len(self._buffer) >= BLOCK_SAMPLES:
            block, self._buffer = self._buffer[:BLOCK_SAMPLES], self._buffer[BLOCK_SAMPLES:]
            scores = self._model.predict(block)
            # Keys are model *file* stems (e.g. "hey_jarvis_v0.1"), not the
            # requested name — with one model loaded, take the max.
            self.last_score = max(scores.values()) if scores else 0.0
            if self._cooldown > 0:  # don't re-trigger on the same phrase
                self._cooldown -= 1
                continue
            if self.last_score >= self.threshold:
                self._cooldown = 25  # ~2s of blocks
                fired = True
        return fired
