"""VoiceSession flow tests with fake engines — no audio hardware, no models,
no Claude CLI."""

import pytest

from friday.voice.session import VoiceSession


class FakeAgent:
    def __init__(self, fragments):
        self.fragments = fragments

    async def ask(self, _prompt):
        for fragment in self.fragments:
            yield ("text", fragment)
        yield ("done", "")


class FakeSpeaker:
    sample_rate = 22050

    def __init__(self):
        self.spoken: list[str] = []

    def synthesize(self, text):
        self.spoken.append(text)
        yield b"\x00\x00"


class FakePlayer:
    def __init__(self):
        self.chunks = 0
        self.interrupts = 0

    def play(self, _samples):
        self.chunks += 1

    def interrupt(self):
        self.interrupts += 1

    def wait(self):
        pass


class FakeRecorder:
    def start(self):
        pass

    def stop(self):
        return []


@pytest.fixture
def session_factory():
    def make(fragments):
        speaker = FakeSpeaker()
        player = FakePlayer()
        session = VoiceSession(
            agent=FakeAgent(fragments),
            transcriber=None,
            speaker=speaker,
            recorder=FakeRecorder(),
            player=player,
        )
        return session, speaker, player

    return make


async def test_sentences_spoken_as_stream_completes(session_factory):
    session, speaker, player = session_factory(
        ["The report is ready. It has three ", "sections. Enjoy your evening, boss."]
    )
    await session.respond("status?")
    assert speaker.spoken == [
        "The report is ready.",
        "It has three sections.",
        "Enjoy your evening, boss.",
    ]
    assert player.chunks == 3


async def test_markdown_stripped_before_speaking(session_factory):
    session, speaker, _ = session_factory(["**Done.** The `file` was renamed successfully."])
    await session.respond("rename it")
    assert speaker.spoken == ["Done. The file was renamed successfully."]


async def test_trailing_text_without_punctuation_is_flushed(session_factory):
    session, speaker, _ = session_factory(["All quiet on the western front"])
    await session.respond("anything new?")
    assert speaker.spoken == ["All quiet on the western front"]
