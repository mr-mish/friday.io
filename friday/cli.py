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


def main() -> None:
    parser = argparse.ArgumentParser(prog="friday", description="FRIDAY personal assistant")
    parser.add_argument("prompt", nargs="*", help="one-shot prompt (omit for interactive mode)")
    parser.add_argument("--config", help="path to friday.toml")
    parser.add_argument("--model", help="override the model for this session")
    parser.add_argument("--voice", action="store_true", help="voice mode (push-to-talk)")
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
    parser.add_argument("--version", action="version", version=f"friday {__version__}")
    args = parser.parse_args()
    try:
        sys.exit(asyncio.run(_main(args)))
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
