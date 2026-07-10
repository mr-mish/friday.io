"""Speech-to-text on top of faster-whisper. Fully local; no audio leaves
the machine."""

from __future__ import annotations

from pathlib import Path

SAMPLE_RATE = 16_000  # Whisper's native rate; the recorder captures at this.


class Transcriber:
    def __init__(self, model: str = "base", language: str | None = None):
        from faster_whisper import WhisperModel

        # int8 keeps CPU latency reasonable; GPU users can extend this later.
        self._model = WhisperModel(model, device="cpu", compute_type="int8")
        self.language = language

    def transcribe(self, audio: object | str | Path) -> str:
        """Transcribe a float32 mono 16 kHz numpy array, or an audio file path."""
        if isinstance(audio, Path):
            audio = str(audio)
        segments, _info = self._model.transcribe(
            audio, language=self.language, vad_filter=True
        )
        return " ".join(seg.text.strip() for seg in segments).strip()
