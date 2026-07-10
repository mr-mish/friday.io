# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

FRIDAY is a local-first personal AI assistant (voice + text) with safe
filesystem powers, built on the Claude Agent SDK. The architecture and phased
roadmap live in `docs/PLAN.md` — read it before making structural changes.
Currently at Phase 1: a text-mode CLI agent with permission-gated file access.

## Commands

```bash
uv sync                  # install (creates .venv)
uv run pytest            # run tests
uv run pytest tests/test_permissions.py -k deny   # single test
uv run ruff check .      # lint
uv run friday            # run the assistant (needs Claude Code CLI + auth)
uv run friday --config path/to/friday.toml "one-shot prompt"
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

- Python 3.11+, `uv` for everything; ruff line length 100.
- Tests cover the safety-critical modules (permissions, config, audit) and
  must not require the Claude CLI or network.
