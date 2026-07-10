"""The push-to-talk voice loop.

Enter starts recording, Enter again stops it; the transcript goes through the
same ``FridayAgent`` the text REPL uses. Response text is split into sentences
as it streams and spoken immediately, so FRIDAY talks while it thinks.
Pressing Enter while FRIDAY is speaking interrupts it (barge-in).

Confirmation prompts stay on the keyboard in Phase 2: saying "yes" to a
dangerous action should be a deliberate act, not something a TTS echo can
trigger. Spoken confirmation is a Phase 4 problem.
"""

from __future__ import annotations

import asyncio

from friday.agent.core import FridayAgent
from friday.voice.chunker import SentenceStream, strip_markdown

EXIT_WORDS = ("exit", "quit", "goodbye")


class VoiceSession:
    def __init__(
        self,
        agent: FridayAgent,
        transcriber,
        speaker,
        recorder=None,
        player=None,
    ):
        self.agent = agent
        self.transcriber = transcriber
        self.speaker = speaker
        if recorder is None or player is None:
            from friday.voice.audio import Player, Recorder

            recorder = recorder or Recorder()
            player = player or Player(speaker.sample_rate)
        self.recorder = recorder
        self.player = player

    async def _capture_utterance(self) -> str:
        await asyncio.to_thread(input, "⏺ Press Enter, then speak…")
        self.player.interrupt()  # talking over FRIDAY = barge-in
        self.recorder.start()
        await asyncio.to_thread(input, "  (recording — Enter to finish) ")
        audio = self.recorder.stop()
        if len(audio) < 1600:  # < 0.1 s: an accidental double-Enter
            return ""
        return await asyncio.to_thread(self.transcriber.transcribe, audio)

    def _speak(self, sentence: str) -> None:
        for chunk in self.speaker.synthesize(strip_markdown(sentence)):
            self.player.play(chunk)

    async def respond(self, transcript: str) -> None:
        """Stream one agent turn, speaking sentences as they complete."""
        stream = SentenceStream()
        async for kind, payload in self.agent.ask(transcript):
            if kind == "text":
                print(payload)
                for sentence in stream.feed(payload):
                    await asyncio.to_thread(self._speak, sentence)
        for sentence in stream.flush():
            await asyncio.to_thread(self._speak, sentence)
        await asyncio.to_thread(self.player.wait)

    async def run_turn(self) -> bool:
        """One PTT round-trip. Returns False when the user wants to exit."""
        transcript = await self._capture_utterance()
        if not transcript:
            print("  (heard nothing)")
            return True
        print(f"you › {transcript}")
        if transcript.strip().lower().rstrip(".!") in EXIT_WORDS:
            return False
        await self.respond(transcript)
        return True

    async def run(self) -> None:
        print("Voice mode — push-to-talk. Say or type 'exit' to quit.\n")
        while await self.run_turn():
            pass
