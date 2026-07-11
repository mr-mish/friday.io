"""Voice-activity detection and end-of-utterance turn taking.

`EnergyVAD` is a dependency-free adaptive-threshold detector: it tracks the
noise floor and calls a frame "speech" when RMS rises well above it. It's
deliberately simple — good enough for a quiet room, and the `is_speech`
callable interface lets a model-based VAD (Silero) replace it without
touching the collector.

`UtteranceCollector` is the turn-taking brain: feed it frames; once speech
has started, `trailing silence > silence_ms` ends the utterance and the
collected audio is returned.
"""

from __future__ import annotations

FRAME_MS = 30  # collector frame size


class EnergyVAD:
    def __init__(self, ratio: float = 3.0, floor_decay: float = 0.05):
        self.ratio = ratio
        self.floor_decay = floor_decay
        self._noise_floor: float | None = None

    def is_speech(self, frame) -> bool:
        import numpy as np

        rms = float(np.sqrt(np.mean(np.square(frame, dtype=np.float64))))
        if self._noise_floor is None:
            self._noise_floor = max(rms, 1e-4)
            return False
        speech = rms > self._noise_floor * self.ratio
        if not speech:
            # only quiet frames update the floor, so speech can't raise it
            self._noise_floor += self.floor_decay * (rms - self._noise_floor)
            self._noise_floor = max(self._noise_floor, 1e-4)
        return speech


class UtteranceCollector:
    def __init__(
        self,
        is_speech,
        sample_rate: int = 16_000,
        silence_ms: int = 800,
        min_speech_ms: int = 200,
        max_utterance_s: int = 60,
    ):
        self._is_speech = is_speech
        self.sample_rate = sample_rate
        self._silence_frames_limit = max(1, silence_ms // FRAME_MS)
        self._min_speech_frames = max(1, min_speech_ms // FRAME_MS)
        self._max_frames = max_utterance_s * 1000 // FRAME_MS
        self.reset()

    def reset(self) -> None:
        self._frames: list = []
        self._speech_frames = 0
        self._trailing_silence = 0
        self._started = False

    def feed(self, frame):
        """Feed one frame; returns the full utterance (numpy array) when the
        speaker has finished, else None."""
        import numpy as np

        speech = self._is_speech(frame)
        if not self._started:
            if not speech:
                return None  # still waiting for the user to start talking
            self._started = True
        self._frames.append(frame)
        if speech:
            self._speech_frames += 1
            self._trailing_silence = 0
        else:
            self._trailing_silence += 1

        done = (
            self._trailing_silence >= self._silence_frames_limit
            or len(self._frames) >= self._max_frames
        )
        if not done:
            return None
        if self._speech_frames < self._min_speech_frames:
            self.reset()  # a click or cough, not speech
            return None
        utterance = np.concatenate(self._frames)
        self.reset()
        return utterance
