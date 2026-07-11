"""Wake-word detection ("Hey Jarvis" / "Hey Friday") via openWakeWord.

Optional heavy dependency (`uv sync --extra handsfree`); models download on
first use. The detector interface is one method — `detect(frame) -> bool` —
so tests and the hands-free session never care what's behind it.
"""

from __future__ import annotations

WAKEWORD_INSTALL_HINT = (
    "Hands-free mode needs the wake-word dependencies.\n"
    "Install them with:  uv sync --extra handsfree"
)


def wakeword_available() -> bool:
    try:
        import openwakeword  # noqa: F401
    except ImportError:
        return False
    return True


class WakeWordDetector:
    """Feed 16 kHz int16 frames; detect() fires once per activation."""

    def __init__(self, model: str = "hey_jarvis", threshold: float = 0.6):
        # Explicit submodule import: some openwakeword versions don't bind
        # `utils` on the package from a bare `import openwakeword`.
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
        self._name = model
        self.threshold = threshold
        self._cooldown = 0

    def detect(self, frame) -> bool:
        import numpy as np

        if frame.dtype != np.int16:
            frame = (frame * 32767).astype(np.int16)
        scores = self._model.predict(frame)
        score = scores.get(self._name, 0.0)
        if self._cooldown > 0:  # don't re-trigger on the tail of the same phrase
            self._cooldown -= 1
            return False
        if score >= self.threshold:
            self._cooldown = 25  # ~2s of frames
            return True
        return False
