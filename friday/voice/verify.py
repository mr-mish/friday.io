"""Speaker verification: FRIDAY only obeys the voice it was enrolled to.

Backed by resemblyzer (a d-vector speaker encoder; optional heavy extra).
Enrollment stores the owner's average voice embedding in the data dir;
each utterance is compared by cosine similarity. Below the threshold, the
utterance is ignored for commands and REJECTED for confirmations.

This gate is advisory for convenience commands and mandatory for spoken
confirmation of dangerous actions (Phase 7). The deny list remains absolute
no matter who is speaking.
"""

from __future__ import annotations

from pathlib import Path

VERIFY_INSTALL_HINT = (
    "Speaker verification needs the resemblyzer dependency.\n"
    "Install it with:  uv sync --extra handsfree\n"
    "Then enroll your voice:  friday --enroll-voice"
)

PROFILE_FILENAME = "voice_profile.npy"


def verifier_available() -> bool:
    try:
        import resemblyzer  # noqa: F401
    except ImportError:
        return False
    return True


class SpeakerVerifier:
    def __init__(self, data_dir: Path, threshold: float = 0.75):
        from resemblyzer import VoiceEncoder

        self._encoder = VoiceEncoder()
        self.profile_path = data_dir / PROFILE_FILENAME
        self.threshold = threshold

    @property
    def enrolled(self) -> bool:
        return self.profile_path.exists()

    def enroll(self, utterances: list) -> None:
        """Store the mean embedding of several samples of the owner's voice."""
        import numpy as np
        from resemblyzer import preprocess_wav

        embeddings = [
            self._encoder.embed_utterance(preprocess_wav(u.astype(np.float32)))
            for u in utterances
        ]
        profile = np.mean(embeddings, axis=0)
        profile /= np.linalg.norm(profile)
        self.profile_path.parent.mkdir(parents=True, exist_ok=True)
        np.save(self.profile_path, profile)

    def similarity(self, utterance) -> float:
        import numpy as np
        from resemblyzer import preprocess_wav

        if not self.enrolled:
            return 0.0
        profile = np.load(self.profile_path)
        embedding = self._encoder.embed_utterance(preprocess_wav(utterance.astype(np.float32)))
        embedding = embedding / np.linalg.norm(embedding)
        return float(np.dot(profile, embedding))

    def verify(self, utterance) -> bool:
        return self.similarity(utterance) >= self.threshold
