"""Text-to-speech on top of Piper. Fully local.

Voices are ONNX models fetched once into the FRIDAY data directory
(``python -m piper.download_voices`` under the hood).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

DEFAULT_VOICE = "en_US-lessac-medium"


def download_env() -> dict[str, str]:
    """Subprocess env with a CA bundle for stdlib urlopen.

    Piper's downloader uses plain urllib, which on some Python builds (the
    python.org macOS installer, notably) has no CA certificates configured
    and fails every HTTPS request with CERTIFICATE_VERIFY_FAILED. Pointing
    SSL_CERT_FILE at certifi's bundle fixes that without touching the system.
    """
    env = os.environ.copy()
    if "SSL_CERT_FILE" not in env:
        try:
            import certifi

            env["SSL_CERT_FILE"] = certifi.where()
        except ImportError:
            pass
    return env


def ensure_voice(name: str, voices_dir: Path) -> Path:
    """Return the local path of a Piper voice, downloading it if missing."""
    voices_dir.mkdir(parents=True, exist_ok=True)
    onnx = voices_dir / f"{name}.onnx"
    if not onnx.exists():
        subprocess.run(
            [sys.executable, "-m", "piper.download_voices", "--data-dir", str(voices_dir), name],
            check=True,
            env=download_env(),
        )
    return onnx


class Speaker:
    def __init__(self, voice_path: Path):
        from piper import PiperVoice

        self._voice = PiperVoice.load(str(voice_path))
        self.sample_rate: int = self._voice.config.sample_rate

    def synthesize(self, text: str):
        """Yield int16 PCM chunks for the given text."""
        import numpy as np

        for chunk in self._voice.synthesize(text):
            yield np.frombuffer(chunk.audio_int16_bytes, dtype=np.int16)

    def synthesize_wav(self, text: str, path: Path) -> None:
        import wave

        with wave.open(str(path), "wb") as wav:
            self._voice.synthesize_wav(text, wav)
