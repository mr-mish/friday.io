"""Speaker verification: FRIDAY only obeys the voice it was enrolled to.

Backed by SpeechBrain's ECAPA-TDNN speaker encoder (optional heavy extra —
it pulls PyTorch). Enrollment stores the owner's average voice embedding in
the data dir; each utterance is compared by cosine similarity. Below the
threshold, the utterance is ignored for commands and REJECTED for
confirmations.

This gate is advisory for convenience commands and mandatory for spoken
confirmation of dangerous actions (Phase 7). The deny list remains absolute
no matter who is speaking.

Typical ECAPA cosine scores: same speaker 0.5-0.8, different speaker < 0.3 —
hence the 0.5 default threshold (config: voice.verify_threshold).
"""

from __future__ import annotations

from pathlib import Path

VERIFY_INSTALL_HINT = (
    "Speaker verification needs the speechbrain dependency.\n"
    "Install it with:  uv sync --extra handsfree   (pulls PyTorch)\n"
    "Then enroll your voice:  friday --enroll-voice"
)

PROFILE_FILENAME = "voice_profile.npy"
ENCODER_SOURCE = "speechbrain/spkrec-ecapa-voxceleb"
DEFAULT_THRESHOLD = 0.5


def verifier_available() -> bool:
    try:
        import speechbrain  # noqa: F401
    except ImportError:
        return False
    return True


class SpeakerVerifier:
    def __init__(self, data_dir: Path, threshold: float = DEFAULT_THRESHOLD):
        from speechbrain.inference.speaker import EncoderClassifier

        self._encoder = EncoderClassifier.from_hparams(
            source=ENCODER_SOURCE, savedir=str(data_dir / "models" / "spkrec")
        )
        self.profile_path = data_dir / PROFILE_FILENAME
        self.threshold = threshold

    @property
    def enrolled(self) -> bool:
        return self.profile_path.exists()

    def _embed(self, utterance):
        """L2-normalized embedding of a float32 mono 16 kHz waveform."""
        import numpy as np
        import torch

        wav = torch.from_numpy(np.asarray(utterance, dtype=np.float32)).unsqueeze(0)
        with torch.no_grad():
            embedding = self._encoder.encode_batch(wav).squeeze().cpu().numpy()
        return embedding / np.linalg.norm(embedding)

    def enroll(self, utterances: list) -> None:
        """Store the mean embedding of several samples of the owner's voice."""
        import numpy as np

        profile = np.mean([self._embed(u) for u in utterances], axis=0)
        profile /= np.linalg.norm(profile)
        self.profile_path.parent.mkdir(parents=True, exist_ok=True)
        np.save(self.profile_path, profile)

    def similarity(self, utterance) -> float:
        import numpy as np

        if not self.enrolled:
            return 0.0
        profile = np.load(self.profile_path)
        return float(np.dot(profile, self._embed(utterance)))

    def verify(self, utterance) -> bool:
        return self.similarity(utterance) >= self.threshold
