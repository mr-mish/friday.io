"""The ``friday`` command: an interactive REPL, or one-shot with a prompt.

Usage:
    friday                      # interactive session
    friday "what's in ~/docs"   # one question, then exit
    friday --config path.toml   # explicit config file
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from friday import __version__
from friday.agent.core import FridayAgent
from friday.config import load_config
from friday.fs.permissions import Decision

DIM = "\033[2m"
BOLD = "\033[1m"
CYAN = "\033[36m"
YELLOW = "\033[33m"
RESET = "\033[0m"


async def _confirm(tool_name: str, tool_input: dict, decision: Decision) -> bool:
    detail = tool_input.get("command") or tool_input.get("file_path") or ""
    print(f"\n{YELLOW}⚠ FRIDAY wants to run {BOLD}{tool_name}{RESET}{YELLOW} — {decision.reason}")
    if detail:
        print(f"  {detail}")
    try:
        answer = await asyncio.to_thread(input, f"  Allow? [y/N] {RESET}")
    except EOFError:  # non-interactive stdin: never assume consent
        print("(no interactive terminal — declined)")
        return False
    return answer.strip().lower() in ("y", "yes")


async def _run_turn(agent: FridayAgent, prompt: str) -> None:
    async for kind, payload in agent.ask(prompt):
        if kind == "text":
            print(payload)
        elif kind == "tool":
            print(f"{DIM}  · {payload}{RESET}")
        elif kind == "done" and payload:
            print(f"{DIM}  ({payload}){RESET}")


async def _main(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config) if args.config else None)
    if args.model:
        config.model = args.model
    if args.doctor:
        from friday.voice import doctor

        return doctor.run(config)

    if args.remember or args.memories or args.forget:
        from friday.memory.store import MemoryStore

        store = MemoryStore(config.db_path)
        if args.remember:
            memory_id = store.remember(args.remember)
            print(f"Remembered (id {memory_id}).")
        if args.forget:
            print("Forgotten." if store.forget(args.forget) else "No memory with that id.")
        if args.memories:
            for m in store.recent():
                print(f"[{m.id}] {m.fact}  {DIM}{m.created[:10]}{RESET}")
        return 0
    if not config.granted_roots:
        print(
            f"{YELLOW}No granted folders configured — every file action will ask first.\n"
            f"Create friday.toml (see friday.example.toml) to grant access.{RESET}\n"
        )

    if args.serve:
        from friday.server import SERVER_INSTALL_HINT, server_available

        if not server_available():
            print(f"{YELLOW}{SERVER_INSTALL_HINT}{RESET}")
            return 1
        import uvicorn

        from friday.server.app import create_app

        print(f"{CYAN}FRIDAY daemon{RESET} — chat panel at http://127.0.0.1:{args.port}")
        uv_config = uvicorn.Config(
            create_app(config), host="127.0.0.1", port=args.port, log_level="warning"
        )
        await uvicorn.Server(uv_config).serve()
        return 0

    if args.inbox or args.schedules:
        from friday.autonomy.notify import Inbox
        from friday.autonomy.schedule import ScheduleStore

        if args.inbox:
            inbox = Inbox(config.db_path)
            notices = inbox.unread()
            if not notices:
                print("Inbox empty.")
            for n in notices:
                print(f"{BOLD}[{n.ts[:16]}] {n.source}{RESET}\n{n.message}\n")
            inbox.mark_read([n.id for n in notices])
        if args.schedules:
            schedules = ScheduleStore(config.db_path).all()
            if not schedules:
                print("No schedules.")
            for s in schedules:
                state = "" if s.enabled else f"  {YELLOW}DISABLED ({s.failures} failures){RESET}"
                print(f"{BOLD}{s.name}{RESET}  {s.spec}  next {s.next_run[:16]}{state}")
                print(f"  {DIM}{s.prompt[:90]}{RESET}")
        return 0

    if args.run_due:
        from friday.autonomy.loop import AutonomyLoop, register_maintenance
        from friday.autonomy.notify import Inbox
        from friday.autonomy.schedule import ScheduleStore
        from friday.autonomy.watcher import FileWatcher
        from friday.memory.index import FileIndex

        store = ScheduleStore(config.db_path)
        register_maintenance(store)
        loop = AutonomyLoop(
            config,
            store,
            Inbox(config.db_path),
            FileWatcher(config.granted_roots, config.denied_paths, []),
            FileIndex(config.db_path, config.granted_roots, config.denied_paths),
        )
        due = store.due()
        await loop.tick()
        print(f"Ran {len(due)} due schedule(s). See friday --inbox for results.")
        return 0

    if args.undo or args.history:
        from friday.fs.undo import UndoJournal

        journal = UndoJournal(config.data_dir)
        if args.undo:
            print(journal.undo_last())
        if args.history:
            changes = journal.history()
            if not changes:
                print("No changes journaled yet.")
            for change in changes:
                flag = " (undone)" if change.undone else ""
                print(f"[{change.id}] {change.ts[:19]} {change.action} {change.path}{flag}")
        return 0

    if args.tasks:
        if not config.tasks:
            print("No tasks defined. Add [tasks.NAME] sections to friday.toml.")
            return 0
        for name, task in config.tasks.items():
            note = f" — {task.description}" if task.description else ""
            print(f"{BOLD}{name}{RESET}{note}")
            print(f"  {DIM}{task.prompt}{RESET}")
        return 0

    prompt = " ".join(args.prompt) if args.prompt else ""
    if args.run_task:
        task = config.tasks.get(args.run_task)
        if task is None:
            known = ", ".join(config.tasks) or "none defined"
            print(f"Unknown task '{args.run_task}' (known: {known})")
            return 2
        prompt = task.prompt

    if args.enroll_voice:
        return _enroll_voice(config)

    if args.listen_test:
        return await _listen_test(config)

    if args.handsfree:
        return await _run_handsfree(config)

    async with FridayAgent(config, confirm=_confirm) as agent:
        if args.voice:
            return await _run_voice(agent, config)

        if prompt:
            await _run_turn(agent, prompt)
            return 0

        print(f"{CYAN}FRIDAY v{__version__}{RESET} — type your request, or 'exit' to quit.\n")
        while True:
            try:
                line = await asyncio.to_thread(input, f"{BOLD}you ›{RESET} ")
            except (EOFError, KeyboardInterrupt):
                print()
                break
            line = line.strip()
            if not line:
                continue
            if line.lower() in ("exit", "quit"):
                break
            try:
                await _run_turn(agent, line)
            except KeyboardInterrupt:
                await agent.interrupt()
                print(f"\n{DIM}(interrupted){RESET}")
            print()
    return 0


async def _run_voice(agent: FridayAgent, config) -> int:
    from friday.voice import VOICE_INSTALL_HINT, voice_available

    if not voice_available():
        print(f"{YELLOW}{VOICE_INSTALL_HINT}{RESET}")
        return 1

    from friday.voice.session import VoiceSession
    from friday.voice.stt import Transcriber
    from friday.voice.tts import Speaker, ensure_voice

    print(f"{DIM}Loading voice models (first run downloads them)…{RESET}")
    voice_path = ensure_voice(config.tts_voice, config.voices_dir)
    speaker = Speaker(voice_path)
    transcriber = Transcriber(config.stt_model, language=config.language)
    session = VoiceSession(agent, transcriber, speaker)
    await session.run()
    return 0


def _build_wake(config):
    """The wake detector the hands-free session (and --listen-test) will use."""
    from friday.voice.stt import Transcriber
    from friday.voice.wakeword import SttWakeDetector, WakeWordDetector

    if config.wake_engine == "openwakeword":
        return WakeWordDetector(config.wake_word, threshold=config.wake_threshold)
    return SttWakeDetector(
        Transcriber(config.wake_stt_model, language=config.language or "en"),
        phrase=config.wake_phrase,
    )


async def _listen_test(config) -> int:
    """Record 5 s from the hands-free mic pipeline, then report what the
    STT hears vs. what the wake model scores — splits audio-path problems
    from wake-model problems in one run."""
    import asyncio as aio
    import wave

    import numpy as np

    from friday.voice.audio import FrameStream
    from friday.voice.stt import SAMPLE_RATE, Transcriber

    print("Recording 5 seconds — say: 'Hey Jarvis, what's the weather?'")
    frames = FrameStream()
    frames.start()
    collected = []
    for _ in range(167):  # ~5s of 30ms frames
        collected.append(await aio.to_thread(frames.next))
    frames.stop()
    audio = np.concatenate(collected)

    wav_path = config.data_dir / "listen_test.wav"
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(wav_path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())

    rms = float(np.sqrt(np.mean(np.square(audio))))
    print(f"rms      : {rms:.4f}  (saved to {wav_path})")

    print("transcribing what was heard…")
    transcript = Transcriber(config.stt_model, language=config.language).transcribe(audio)
    print(f"stt heard: {transcript!r}")

    print(f"scoring with the wake engine ({config.wake_engine})…")
    detector = _build_wake(config)
    peak = 0.0
    fired = False
    for frame in collected:
        fired = detector.detect(frame) or fired
        peak = max(peak, detector.last_score)
    print(f"wake     : peak_score={peak:.6f} fired={fired}")
    if transcript and peak < 0.01:
        print("=> STT hears you but the wake model does not: wake-model-side problem.")
    elif not transcript:
        print("=> STT heard nothing either: audio-path problem (device/levels).")
    return 0


async def _run_handsfree(config) -> int:
    from friday.voice import VOICE_INSTALL_HINT, voice_available
    from friday.voice.wakeword import WAKEWORD_INSTALL_HINT, wakeword_available

    if not voice_available():
        print(f"{YELLOW}{VOICE_INSTALL_HINT}{RESET}")
        return 1
    if config.wake_engine == "openwakeword" and not wakeword_available():
        print(f"{YELLOW}{WAKEWORD_INSTALL_HINT}{RESET}")
        return 1

    from friday.fs.undo import UndoJournal
    from friday.voice.audio import FrameStream, Player
    from friday.voice.handsfree import HandsFreeSession
    from friday.voice.stt import Transcriber
    from friday.voice.tts import Speaker, ensure_voice
    from friday.voice.vad import EnergyVAD, UtteranceCollector
    from friday.voice.verify import SpeakerVerifier, verifier_available

    verifier = None
    if config.verify_speaker:
        if not verifier_available():
            print(
                f"{YELLOW}verify_speaker is on but speechbrain is missing — "
                f"install it with: uv sync --extra handsfree. Aborting.{RESET}"
            )
            return 1
        verifier = SpeakerVerifier(config.data_dir, threshold=config.verify_threshold)
        if not verifier.enrolled:
            print(f"{YELLOW}No enrolled voice. Run: friday --enroll-voice{RESET}")
            return 1

    print(f"{DIM}Loading models…{RESET}")
    speaker = Speaker(ensure_voice(config.tts_voice, config.voices_dir))
    transcriber = Transcriber(config.stt_model, language=config.language)
    wake = _build_wake(config)
    if config.wake_engine == "stt":
        engine = f'whisper-{config.wake_stt_model}, phrase "{config.wake_phrase}"'
        print(f"{DIM}Wake engine: {engine}{RESET}")

    holder: dict = {}

    async def confirm(tool_name, tool_input, decision):
        session = holder.get("session")
        return False if session is None else await session.spoken_confirm(
            tool_name, tool_input, decision
        )

    frames = FrameStream()
    session_obj = None
    async with FridayAgent(config, confirm=confirm) as agent:
        session_obj = HandsFreeSession(
            agent=agent,
            transcriber=transcriber,
            speaker=speaker,
            frames=frames,
            player=Player(speaker.sample_rate),
            wake=wake,
            collector=UtteranceCollector(EnergyVAD().is_speech),
            verifier=verifier,
            undo=UndoJournal(config.data_dir),
        )
        holder["session"] = session_obj
        frames.start()
        try:
            await session_obj.run()
        finally:
            frames.stop()
    return 0


def _enroll_voice(config) -> int:
    from friday.voice.verify import VERIFY_INSTALL_HINT, SpeakerVerifier, verifier_available

    if not verifier_available():
        print(f"{YELLOW}{VERIFY_INSTALL_HINT}{RESET}")
        return 1
    from friday.voice.audio import Recorder

    verifier = SpeakerVerifier(config.data_dir, threshold=config.verify_threshold)
    recorder = Recorder()
    samples = []
    print("Enrolling your voice — three samples, a full sentence each.")
    for i in range(3):
        input(f"[{i + 1}/3] Press Enter, speak a sentence, Enter again… ")
        recorder.start()
        input("  (recording — Enter to finish) ")
        samples.append(recorder.stop())
    verifier.enroll(samples)
    print(f"Enrolled. Profile stored at {verifier.profile_path}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="friday", description="FRIDAY personal assistant")
    parser.add_argument("prompt", nargs="*", help="one-shot prompt (omit for interactive mode)")
    parser.add_argument("--config", help="path to friday.toml")
    parser.add_argument("--model", help="override the model for this session")
    parser.add_argument("--voice", action="store_true", help="voice mode (push-to-talk)")
    parser.add_argument("--handsfree", action="store_true", help="always-on voice (wake word)")
    parser.add_argument("--enroll-voice", action="store_true", help="enroll your voice profile")
    parser.add_argument(
        "--listen-test", action="store_true", help="5s mic test: STT vs wake-model diagnosis"
    )
    parser.add_argument("--doctor", action="store_true", help="self-test the voice stack")
    parser.add_argument("--remember", metavar="FACT", help="store a long-term memory and exit")
    parser.add_argument("--memories", action="store_true", help="list stored memories and exit")
    parser.add_argument("--forget", type=int, metavar="ID", help="delete a memory by id and exit")
    parser.add_argument("--tasks", action="store_true", help="list named tasks and exit")
    parser.add_argument(
        "--run-task",
        metavar="NAME",
        help="run a named task from friday.toml (schedule via cron/launchd)",
    )
    parser.add_argument("--undo", action="store_true", help="revert FRIDAY's last file change")
    parser.add_argument("--history", action="store_true", help="list journaled file changes")
    parser.add_argument("--serve", action="store_true", help="run the daemon + web chat panel")
    parser.add_argument(
        "--inbox", action="store_true", help="read notifications from autonomous runs"
    )
    parser.add_argument("--schedules", action="store_true", help="list recurring autonomous tasks")
    parser.add_argument("--run-due", action="store_true", help="run due schedules once (for cron)")
    parser.add_argument("--port", type=int, default=4527, help="daemon port (default 4527)")
    parser.add_argument("--version", action="version", version=f"friday {__version__}")
    args = parser.parse_args()
    try:
        sys.exit(asyncio.run(_main(args)))
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
