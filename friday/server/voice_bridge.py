"""Local browser-audio bridge using FRIDAY's Whisper and Piper stack."""

from __future__ import annotations

import asyncio
import base64
import binascii
import tempfile
from pathlib import Path

from friday.config import FridayConfig
from friday.voice.chunker import SentenceStream, strip_markdown

MAX_AUDIO_BYTES = 15 * 1024 * 1024
ALLOWED_FORMATS = {"webm", "wav", "ogg", "mp3", "m4a"}


class VoiceBridge:
    """Lazily load local voice models and exchange audio with the web panel."""

    def __init__(self, config: FridayConfig, transcriber=None, speaker=None):
        self.config = config
        self._transcriber = transcriber
        self._speaker = speaker
        self._load_lock = asyncio.Lock()

    def sentence_stream(self) -> SentenceStream:
        return SentenceStream()

    async def _ensure_models(self) -> None:
        if self._transcriber is not None and self._speaker is not None:
            return
        async with self._load_lock:
            if self._transcriber is None:
                from friday.voice.stt import Transcriber

                self._transcriber = await asyncio.to_thread(
                    Transcriber, self.config.stt_model, self.config.language
                )
            if self._speaker is None:
                from friday.voice.tts import Speaker, ensure_voice

                voice_path = await asyncio.to_thread(
                    ensure_voice, self.config.tts_voice, self.config.voices_dir
                )
                self._speaker = await asyncio.to_thread(Speaker, voice_path)

    async def transcribe(self, audio_b64: str, audio_format: str = "webm") -> str:
        await self._ensure_models()
        audio_format = audio_format.lower().lstrip(".")
        if audio_format not in ALLOWED_FORMATS:
            raise ValueError(f"Unsupported audio format: {audio_format}")
        try:
            audio = base64.b64decode(audio_b64, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("Invalid audio payload") from exc
        if not audio or len(audio) > MAX_AUDIO_BYTES:
            raise ValueError("Audio payload is empty or too large")
        path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=f".{audio_format}", delete=False) as temp:
                temp.write(audio)
                path = Path(temp.name)
            return await asyncio.to_thread(self._transcriber.transcribe, path)
        finally:
            if path is not None:
                path.unlink(missing_ok=True)

    async def synthesize(self, text: str) -> list[dict]:
        await self._ensure_models()

        def render() -> list[bytes]:
            return [
                chunk.tobytes() if hasattr(chunk, "tobytes") else bytes(chunk)
                for chunk in self._speaker.synthesize(strip_markdown(text))
            ]

        chunks = await asyncio.to_thread(render)
        return [
            {
                "type": "audio",
                "pcm_b64": base64.b64encode(chunk).decode("ascii"),
                "sample_rate": self._speaker.sample_rate,
            }
            for chunk in chunks
            if chunk
        ]
