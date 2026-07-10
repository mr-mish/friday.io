# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

FRIDAY is a local-first personal AI assistant (voice + text) with safe
filesystem powers, built on the Claude Agent SDK. The architecture and phased
roadmap live in `docs/PLAN.md` — read it before making structural changes.
Currently at Phase 5: text REPL, push-to-talk voice (local Whisper STT,
local Piper TTS, sentence-streamed playback), long-term memory, a full-text
index over granted folders, config-driven MCP skills, named tasks
(`--run-task`, scheduled externally via cron/launchd), and a localhost
daemon + web chat panel (`--serve`).

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
uv run friday --remember "fact"   # store a memory; --memories lists, --forget ID deletes
uv run friday --tasks             # list named tasks; --run-task NAME runs one
uv run friday --undo              # revert FRIDAY's last file change; --history lists
uv sync --extra server            # daemon deps (fastapi, uvicorn, websockets)
uv run friday --serve             # web chat panel at http://127.0.0.1:4527
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
- `friday/fs/undo.py` — pre-write snapshots + change journal in
  `data_dir/undo/`. The agent snapshots in `_snapshot_for_undo` at both
  approval points (hook ALLOW, and can_use_tool after a confirmed ask);
  undoing a "create" moves the file to trash rather than deleting it.
  Bash-driven writes are not tracked — that's why Bash always confirms.
- `friday/config.py` — `friday.toml` loader ($FRIDAY_CONFIG, ./friday.toml,
  ~/.config/friday/friday.toml). No config = no granted roots = everything
  confirms. User `denied_paths` extend the built-in deny list, never shrink it.
  Also parses `[skills.NAME]` (external MCP servers; `trust = "allow"` makes
  the gate auto-approve that server's tools, default is confirm-per-call) and
  `[tasks.NAME]` (named prompts for `--run-task`; cron/launchd does the
  scheduling). The skill name "memory" is reserved for the built-in server.
- `friday/cli.py` — REPL / one-shot entry point (`friday` script).
- `friday/memory/` — long-term memory + file content index, both SQLite FTS5
  (BM25) in `data_dir/friday.db`; `search()` is the interface a future vector
  backend replaces. `tools.py` exposes remember/recall/forget/search_files to
  the agent as in-process MCP tools (server name "memory"); recent memories
  are injected into the system prompt at session start. The index walks
  granted roots incrementally (mtime/size), extracts text from plain files
  plus PDF (pypdf) and DOCX (python-docx), and enforces the deny list at
  index time.
- `friday/server/` — optional extra (`server`); localhost FastAPI daemon
  (`app.py`) wrapping one `FridayAgent`. A single WebSocket client streams
  agent events and answers permission asks over the socket (fail-closed:
  disconnect or 120 s timeout = declined); `agent_factory` is injectable so
  tests drive it with a fake agent. `static/index.html` is the self-contained
  chat panel. Browser E2E lives in tests/test_panel_e2e.py (skipped unless
  `FRIDAY_E2E=1` — it needs a live agent).
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
- Confirmation prompts must fail closed everywhere: no interactive terminal
  (CLI) or no/lost WebSocket client within 120 s (daemon) → declined.
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
