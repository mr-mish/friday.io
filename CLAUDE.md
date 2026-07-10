# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

FRIDAY is a local-first personal AI assistant (voice + text) with safe
filesystem powers, built on the Claude Agent SDK. The architecture and phased
roadmap live in `docs/PLAN.md` — read it before making structural changes.
Currently at Phase 2: text REPL plus push-to-talk voice (local Whisper STT,
local Piper TTS, sentence-streamed playback).

## Commands

```bash
uv sync                  # install (creates .venv)
uv sync --extra voice    # + voice deps (faster-whisper, piper, sounddevice)
uv run pytest            # run tests
uv run pytest tests/test_permissions.py -k deny   # single test
uv run ruff check .      # lint
uv run friday            # run the assistant (needs Claude Code CLI + auth)
uv run friday --config path/to/friday.toml "one-shot prompt"
uv run friday --voice    # push-to-talk voice mode (needs mic + models)
uv run friday --doctor   # voice self-test (TTS→STT roundtrip, timings)
```

## Architecture

- `friday/agent/core.py` — `FridayAgent`: a Claude Agent SDK session.
  Policy is enforced in a `PreToolUse` hook (sees every tool call, maps the
  gate's verdict to allow/deny/ask); `can_use_tool` handles only the "ask"
  path by prompting the user via the interface-supplied `confirm` callback.
  `ask()` yields `(kind, payload)` events so different frontends (CLI now,
  voice later) can render the same stream.
- `friday/fs/permissions.py` — `PermissionGate`: three tiers (READ/WRITE
  auto-allowed inside granted roots, DANGEROUS always confirms). The deny
  list wins over everything, including user confirmation.
- `friday/fs/audit.py` — append-only JSONL journal of every tool call.
- `friday/config.py` — `friday.toml` loader ($FRIDAY_CONFIG, ./friday.toml,
  ~/.config/friday/friday.toml). No config = no granted roots = everything
  confirms. User `denied_paths` extend the built-in deny list, never shrink it.
- `friday/cli.py` — REPL / one-shot entry point (`friday` script).
- `friday/voice/` — optional extra (`voice`); every heavy import is lazy so
  text mode and tests never need it. `chunker.SentenceStream` turns the
  streaming response into speakable sentences (the latency lever);
  `session.VoiceSession` is the push-to-talk loop and takes injectable
  recorder/player/engines so tests run with fakes; `audio.py` is the only
  module that touches PortAudio. `doctor.py` = `friday --doctor` self-test.

## Critical invariants

- Never pass tool names to the SDK's `allowed_tools` option — it auto-approves
  them *before* `can_use_tool` and the permission gate is silently bypassed.
  Use `tools=` (availability) + the PreToolUse hook (policy) instead.
- The CLI runtime auto-approves some calls on its own (reads in cwd, "safe"
  shell commands), so enforcement must stay in the PreToolUse hook — it is
  the only chokepoint that sees every call.
- Confirmation prompts must fail closed: no interactive terminal → declined.
- `DEFAULT_DENIED` in config.py protects credential stores; additions welcome,
  removals are not.

## Conventions

- Python 3.13+ (CI also tests 3.14), `uv` for everything; ruff line length 100.
- Tests cover the safety-critical modules (permissions, config, audit) and
  must not require the Claude CLI, network, audio hardware, or ML models —
  voice logic is tested through fakes (see tests/test_voice_session.py).
- Model downloads (Piper voices, Whisper weights) come from huggingface.co,
  which is blocked in remote/sandboxed sessions — `friday --doctor` is the
  user-side check; don't try to live-test audio in a cloud session.
