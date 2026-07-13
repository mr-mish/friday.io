"""Wake-word detection: "Hey Jarvis" → FRIDAY starts listening.

Two engines behind the same one-method interface (`detect(frame) -> bool`):

- "stt" (default): transcribe short rolling windows with a small Whisper
  model and match the wake phrase in text. Heavier per activation but built
  on the same STT stack the rest of voice mode already proves works — a
  silence gate keeps it idle-cheap.
- "openwakeword": the classic dedicated detector. Lighter on CPU but the
  library is unmaintained and its models misbehave on some setups; opt in
  with voice.wake_engine = "openwakeword".
"""

from __future__ import annotations

import re

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


class SttWakeDetector:
    """Wake detection by transcribing rolling audio windows.

    Feed 16 kHz float32 frames; roughly once per second (and only when the
    window isn't silence) the last ~2.5 s are transcribed and matched
    against the wake phrase.
    """

    WINDOW_S = 2.5
    CHECK_INTERVAL_S = 1.0
    COOLDOWN_S = 3.0
    SILENCE_RMS = 0.004  # below this, skip transcription entirely

    def __init__(self, transcriber, phrase: str = "hey jarvis", sample_rate: int = 16_000):
        self.transcriber = transcriber
        self.phrase = re.sub(r"[^a-z ]", "", phrase.lower()).strip()
        self.sample_rate = sample_rate
        self.last_score = 0.0
        self._frames: list = []
        self._buffered = 0
        self._since_check = 0
        self._cooldown = 0

    def detect(self, frame) -> bool:
        import numpy as np

        if frame.dtype == np.int16:
            frame = frame.astype(np.float32) / 32767.0
        self._frames.append(frame)
        self._buffered += len(frame)
        while self._buffered > self.WINDOW_S * self.sample_rate and len(self._frames) > 1:
            self._buffered -= len(self._frames.pop(0))

        if self._cooldown > 0:
            self._cooldown -= len(frame)
            return False
        self._since_check += len(frame)
        if self._since_check < self.CHECK_INTERVAL_S * self.sample_rate:
            return False
        self._since_check = 0

        audio = np.concatenate(self._frames)
        if float(np.sqrt(np.mean(np.square(audio)))) < self.SILENCE_RMS:
            self.last_score = 0.0
            return False
        text = re.sub(r"[^a-z ]", "", self.transcriber.transcribe(audio).lower())
        # full phrase, or its distinctive last word ("jarvis") on its own
        hit = self.phrase in text or self.phrase.split()[-1] in text
        self.last_score = 1.0 if hit else 0.0
        if hit:
            self._cooldown = int(self.COOLDOWN_S * self.sample_rate)
            self._frames = []
            self._buffered = 0
            return True
        return False


BLOCK_SAMPLES = 1280  # openWakeWord is designed for 80 ms @ 16 kHz blocks


class WakeWordDetector:
    """Feed 16 kHz audio frames of any size; detect() fires once per activation."""

    def __init__(self, model: str = "hey_jarvis", threshold: float = 0.5):
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
        self._buffer = np.zeros(0, dtype=np.float32)
        self._cooldown = 0

    # Quiet laptop mics deliver speech far below the level the models were
    # trained on; boost soft blocks toward this RMS (bounded gain).
    AGC_TARGET_RMS = 0.08
    AGC_MAX_GAIN = 25.0

    def detect(self, frame) -> bool:
        import numpy as np

        if frame.dtype == np.int16:
            frame = frame.astype(np.float32) / 32767.0
        self._buffer = np.concatenate([self._buffer, frame])
        fired = False
        # Feed the model exactly the block size it was trained for; smaller
        # chunks silently degrade its feature pipeline.
        while len(self._buffer) >= BLOCK_SAMPLES:
            block, self._buffer = self._buffer[:BLOCK_SAMPLES], self._buffer[BLOCK_SAMPLES:]
            rms = float(np.sqrt(np.mean(np.square(block))))
            if 0.001 < rms < self.AGC_TARGET_RMS:
                block = block * min(self.AGC_TARGET_RMS / rms, self.AGC_MAX_GAIN)
            ints = (np.clip(block, -1.0, 1.0) * 32767).astype(np.int16)
            scores = self._model.predict(ints)
            # Keys are model *file* stems (e.g. "hey_jarvis_v0.1"), not the
            # requested name — with one model loaded, take the max.
            self.last_score = float(max(scores.values())) if scores else 0.0
            if self._cooldown > 0:  # don't re-trigger on the same phrase
                self._cooldown -= 1
                continue
            if self.last_score >= self.threshold:
                self._cooldown = 25  # ~2s of blocks
                fired = True
        return fired
