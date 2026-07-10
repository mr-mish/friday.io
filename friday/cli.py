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
    if not config.granted_roots:
        print(
            f"{YELLOW}No granted folders configured — every file action will ask first.\n"
            f"Create friday.toml (see friday.example.toml) to grant access.{RESET}\n"
        )

    async with FridayAgent(config, confirm=_confirm) as agent:
        if args.prompt:
            await _run_turn(agent, " ".join(args.prompt))
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


def main() -> None:
    parser = argparse.ArgumentParser(prog="friday", description="FRIDAY personal assistant")
    parser.add_argument("prompt", nargs="*", help="one-shot prompt (omit for interactive mode)")
    parser.add_argument("--config", help="path to friday.toml")
    parser.add_argument("--model", help="override the model for this session")
    parser.add_argument("--version", action="version", version=f"friday {__version__}")
    args = parser.parse_args()
    try:
        sys.exit(asyncio.run(_main(args)))
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
