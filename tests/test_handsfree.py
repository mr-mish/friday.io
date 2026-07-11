"""Hands-free session tests: fake mic, wake word, VAD, verifier, engines."""

import pytest

from friday.fs.permissions import Decision, Tier, Verdict
from friday.voice.handsfree import HandsFreeSession
from friday.voice.vad import EnergyVAD, UtteranceCollector

np = pytest.importorskip("numpy")  # ships with the voice extra

FRAME = 480  # 30ms @ 16kHz


def quiet(n):
    return [np.full(FRAME, 0.001, dtype=np.float32) for _ in range(n)]


def loud(n):
    return [np.full(FRAME, 0.5, dtype=np.float32) for _ in range(n)]


class FakeFrames:
    def __init__(self, frames):
        self._frames = list(frames)

    def next(self):
        return self._frames.pop(0) if self._frames else None


class FakeWake:
    """Fires on the first frame only."""

    def __init__(self):
        self.fired = False

    def detect(self, _frame):
        if self.fired:
            return False
        self.fired = True
        return True


class FakeTranscriber:
    def __init__(self, transcripts):
        self.transcripts = list(transcripts)

    def transcribe(self, _audio):
        return self.transcripts.pop(0) if self.transcripts else ""


class FakeSpeaker:
    sample_rate = 22050

    def __init__(self):
        self.spoken = []

    def synthesize(self, text):
        self.spoken.append(text)
        yield np.zeros(2, dtype=np.int16)


class FakePlayer:
    active = False

    def __init__(self):
        self.interrupts = 0

    def play(self, _s):
        pass

    def interrupt(self):
        self.interrupts += 1

    def wait(self):
        pass


class FakeAgent:
    def __init__(self):
        self.prompts = []

    async def ask(self, prompt):
        self.prompts.append(prompt)
        yield ("text", "As you wish.")
        yield ("done", "")


class FakeUndo:
    def __init__(self):
        self.calls = 0

    def undo_last(self):
        self.calls += 1
        return "Restored the file."


def make_session(frames, transcripts, verifier=None, undo=None, agent=None):
    vad = EnergyVAD()
    return HandsFreeSession(
        agent=agent or FakeAgent(),
        transcriber=FakeTranscriber(transcripts),
        speaker=FakeSpeaker(),
        frames=FakeFrames(frames),
        player=FakePlayer(),
        wake=FakeWake(),
        collector=UtteranceCollector(vad.is_speech, silence_ms=90, min_speech_ms=60),
        verifier=verifier,
        undo=undo,
    )


def utterance_frames():
    # calibration + wake frame + speech burst + trailing silence
    return quiet(3) + loud(6) + quiet(6)


async def test_wake_then_utterance_reaches_agent():
    session = make_session(utterance_frames(), ["what's the weather"])
    await session.run()
    assert session.agent.prompts == ["what's the weather"]
    assert "As you wish." in session.speaker.spoken


async def test_unverified_speaker_is_ignored():
    class RejectAll:
        def verify(self, _u):
            return False

    agent = FakeAgent()
    session = make_session(utterance_frames(), ["do something"], verifier=RejectAll(), agent=agent)
    await session.run()
    assert agent.prompts == []


async def test_undo_intent_bypasses_agent():
    undo = FakeUndo()
    agent = FakeAgent()
    session = make_session(utterance_frames(), ["undo that"], undo=undo, agent=agent)
    await session.run()
    assert undo.calls == 1
    assert agent.prompts == []
    assert "Restored the file." in session.speaker.spoken


async def test_goodbye_ends_session():
    session = make_session(utterance_frames(), ["goodbye"])
    await session.run()
    assert any("Goodbye" in s for s in session.speaker.spoken)


@pytest.mark.parametrize("echo_correctly,expected", [(True, True), (False, False)])
async def test_spoken_confirmation_challenge(echo_correctly, expected, monkeypatch):
    # two utterances: the request, then the confirmation reply
    frames = utterance_frames() + loud(6) + quiet(6)
    monkeypatch.setattr("friday.voice.handsfree.secrets.choice", lambda _seq: "tango")
    reply = "confirm tango" if echo_correctly else "confirm foxtrot"

    class ConfirmingAgent:
        def __init__(self):
            self.approved = None

        async def ask(self, prompt):
            decision = Decision(Verdict.CONFIRM, Tier.DANGEROUS, "needs confirmation")
            self.approved = await session.spoken_confirm("Bash", {"command": "rm x"}, decision)
            yield ("done", "")

    agent = ConfirmingAgent()
    session = make_session(frames, ["delete the temp file", reply], agent=agent)
    await session.run()
    assert agent.approved is expected
    assert any("tango" in s for s in session.speaker.spoken)  # challenge was spoken


async def test_vad_collector_end_to_end():
    vad = EnergyVAD()
    collector = UtteranceCollector(vad.is_speech, silence_ms=90, min_speech_ms=60)
    result = None
    for frame in quiet(3) + loud(6) + quiet(6):
        got = collector.feed(frame)
        result = got if got is not None else result
    assert result is not None
    assert len(result) >= 6 * FRAME  # captured the speech burst
