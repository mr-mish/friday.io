"""Hands-free FRIDAY: wake word → speak → FRIDAY answers. No keyboard.

Architecture: a frame pump task runs the wake-word/VAD state machine over
the mic stream and emits complete utterances onto a queue; the main loop
consumes them. While FRIDAY is speaking, mic frames are only scanned for
the wake word (echo guard) — saying it barges in and stops playback.

Phase 7 safety in this mode:
- If speaker verification is enabled, unverified utterances are ignored.
- Dangerous actions use a spoken challenge: FRIDAY announces the action and
  a one-time word; approval requires saying "confirm <word>" (verified voice
  when verification is on). Anything else — including silence — declines.
- "undo that" reverts FRIDAY's last file change without a round-trip.
"""

from __future__ import annotations

import asyncio
import os
import re
import secrets

from friday.fs.permissions import Decision
from friday.voice.session import VoiceSession

CONFIRM_TIMEOUT_S = 30
CHALLENGE_WORDS = ["alpha", "bravo", "delta", "echo", "sierra", "tango", "victor", "zulu"]

_UNDO = re.compile(r"\bundo\b.*\b(that|it|last|change)\b|\b(that|it)\b.*\bundo\b")
_DISMISS = {"never mind", "nevermind", "stop", "cancel", "nothing"}


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


class HandsFreeSession(VoiceSession):
    def __init__(
        self,
        agent,
        transcriber,
        speaker,
        frames,  # object with .next() -> frame (blocking)
        player,
        wake,  # object with .detect(frame) -> bool
        collector,  # UtteranceCollector
        verifier=None,  # SpeakerVerifier or None
        undo=None,  # UndoJournal or None
    ):
        super().__init__(agent, transcriber, speaker, recorder=object(), player=player)
        self.frames = frames
        self.wake = wake
        self.collector = collector
        self.verifier = verifier
        self.undo = undo
        self._utterances: asyncio.Queue = asyncio.Queue()
        self._awaiting_confirm = False

    # ---------------------------------------------------------------- pump

    async def _pump(self) -> None:
        listening = False
        debug = bool(os.environ.get("FRIDAY_VOICE_DEBUG"))
        frame_count = 0
        peak_score = 0.0
        peak_rms = 0.0
        while True:
            frame = await asyncio.to_thread(self.frames.next)
            if frame is None:  # end of stream (only fakes/tests ever end)
                await self._utterances.put(None)
                return
            frame_count += 1
            if self.player.active:
                # echo guard: never transcribe FRIDAY's own voice; the wake
                # word (or an active confirm exchange) barges in.
                if self.wake.detect(frame):
                    self.player.interrupt()
                    print("⏺ (barge-in) listening…")
                    listening = True
                    self.collector.reset()
                continue
            if not listening and not self._awaiting_confirm:
                # keep the VAD's noise floor tracking ambient sound so the
                # utterance right after the wake word is classified correctly
                if hasattr(self.collector, "calibrate"):
                    self.collector.calibrate(frame)
                if self.wake.detect(frame):
                    print("⏺ listening…")
                    listening = True
                    self.collector.reset()
                if debug:
                    rms = float((frame.astype("float64") ** 2).mean() ** 0.5)
                    peak_rms = max(peak_rms, rms)
                    peak_score = max(peak_score, getattr(self.wake, "last_score", 0.0) or 0.0)
                    if frame_count % 33 == 0:  # report the last second's PEAKS
                        print(f"[voice] peak_rms={peak_rms:.4f} peak_wake_score={peak_score:.4f}")
                        peak_score = 0.0
                        peak_rms = 0.0
                continue
            utterance = self.collector.feed(frame)
            if utterance is not None:
                listening = False
                print("… got it, thinking")
                await self._utterances.put(utterance)

    # ------------------------------------------------------------ utterance

    async def _next_transcript(self, timeout: float | None = None) -> str | None:
        """Next verified utterance as text; None on timeout/end-of-stream."""
        while True:
            try:
                utterance = await asyncio.wait_for(self._utterances.get(), timeout)
            except TimeoutError:
                return None
            if utterance is None:
                return None
            if self.verifier is not None and not self.verifier.verify(utterance):
                print("  (ignored: unverified voice)")
                continue
            text = await asyncio.to_thread(self.transcriber.transcribe, utterance)
            if text.strip():
                return text

    async def spoken_confirm(self, tool_name: str, tool_input: dict, decision: Decision) -> bool:
        """The agent's ConfirmFn in hands-free mode (challenge phrase)."""
        word = secrets.choice(CHALLENGE_WORDS)
        detail = str(tool_input.get("command") or tool_input.get("file_path") or "")
        self._awaiting_confirm = True
        try:
            await asyncio.to_thread(
                self._speak,
                f"Permission needed: {tool_name}. {decision.reason}. {detail}. "
                f"Say: confirm {word} — or say deny.",
            )
            await asyncio.to_thread(self.player.wait)
            reply = await self._next_transcript(timeout=CONFIRM_TIMEOUT_S)
        finally:
            self._awaiting_confirm = False
        approved = reply is not None and f"confirm {word}" in _normalize(reply)
        await asyncio.to_thread(
            self._speak, "Confirmed." if approved else "Declined."
        )
        return approved

    async def _handle(self, transcript: str) -> bool:
        """Handle one spoken request; returns False to exit."""
        print(f"you › {transcript}")
        normalized = _normalize(transcript)
        if normalized in _DISMISS:
            return True
        if normalized.rstrip(".!") in ("exit", "quit", "goodbye"):
            await asyncio.to_thread(self._speak, "Goodbye, boss.")
            await asyncio.to_thread(self.player.wait)
            return False
        if self.undo is not None and _UNDO.search(normalized):
            result = self.undo.undo_last()
            print(result)
            await asyncio.to_thread(self._speak, result)
            return True
        await self.respond(transcript)
        return True

    # ----------------------------------------------------------------- run

    async def run(self) -> None:
        print("Hands-free mode — say the wake word, then speak. Say 'goodbye' to quit.\n")
        pump = asyncio.create_task(self._pump())
        try:
            while True:
                transcript = await self._next_transcript()
                if transcript is None:
                    break
                if not await self._handle(transcript):
                    break
        finally:
            pump.cancel()
