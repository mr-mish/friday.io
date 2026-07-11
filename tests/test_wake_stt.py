"""STT wake engine tests with a fake transcriber."""

import pytest

from friday.voice.wakeword import SttWakeDetector

np = pytest.importorskip("numpy")

SR = 16_000
FRAME = 480  # 30ms


class FakeTranscriber:
    def __init__(self, text):
        self.text = text
        self.calls = 0

    def transcribe(self, _audio):
        self.calls += 1
        return self.text


def feed_seconds(detector, seconds, amplitude):
    fired = False
    for _ in range(int(seconds * SR / FRAME)):
        frame = np.full(FRAME, amplitude, dtype=np.float32)
        fired = detector.detect(frame) or fired
    return fired


def test_fires_on_wake_phrase():
    stt = FakeTranscriber("Hey, Jarvis! What's the weather?")
    detector = SttWakeDetector(stt, phrase="hey jarvis")
    assert feed_seconds(detector, 1.5, amplitude=0.1) is True
    assert detector.last_score == 1.0


def test_matches_distinctive_last_word_alone():
    stt = FakeTranscriber("jarvis?")
    detector = SttWakeDetector(stt, phrase="hey jarvis")
    assert feed_seconds(detector, 1.5, amplitude=0.1) is True


def test_no_fire_on_other_speech():
    stt = FakeTranscriber("what a lovely day outside")
    detector = SttWakeDetector(stt, phrase="hey jarvis")
    assert feed_seconds(detector, 3, amplitude=0.1) is False
    assert stt.calls >= 2  # it did check, repeatedly


def test_silence_skips_transcription():
    stt = FakeTranscriber("hey jarvis")  # would fire if it were ever consulted
    detector = SttWakeDetector(stt, phrase="hey jarvis")
    assert feed_seconds(detector, 3, amplitude=0.0001) is False
    assert stt.calls == 0


def test_cooldown_prevents_double_fire():
    stt = FakeTranscriber("hey jarvis")
    detector = SttWakeDetector(stt, phrase="hey jarvis")
    fires = 0
    for _ in range(int(4 * SR / FRAME)):  # 4s of continuous "hey jarvis"
        if detector.detect(np.full(FRAME, 0.1, dtype=np.float32)):
            fires += 1
    assert fires == 1  # cooldown swallows the echoes of the same activation
