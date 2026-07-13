# AGENTS.md

General development guidance lives in `CLAUDE.md` (architecture, invariants,
conventions) and `README.md` (feature overview + quick start). Standard
commands (`uv sync`, `uv run pytest`, `uv run ruff check .`, `uv run friday …`)
are documented there — use those as the source of truth.

## Cursor Cloud specific instructions

- **Package manager / Python:** This project requires Python 3.13+, but the VM's
  system `python3` is 3.12. Do **not** use system Python — always go through
  `uv` (`uv run …`), which provisions and pins its own 3.13 interpreter from
  `.python-version` + `uv.lock`. `uv` is installed on `PATH` (at
  `/usr/local/bin/uv`); the startup update script runs `uv sync --extra server`.
- **Claude auth is the only blocker for live agent turns.** The Claude Code CLI
  is bundled inside the `claude-agent-sdk` package (`_bundled/claude`), so no
  separate CLI install is needed. However, an agent turn (interactive REPL,
  one-shot prompt, `--voice`, or a chat message in the `--serve` panel) needs an
  authenticated Anthropic account. Without it, turns print
  `Not logged in · Please run /login`. Provide credentials via the
  `ANTHROPIC_API_KEY` secret (or run the CLI login flow) before testing
  end-to-end agentic behavior.
- **What runs WITHOUT Claude auth** (safe to test in cloud): the whole test
  suite (`uv run pytest`), lint, long-term memory (`uv run friday --remember`
  / `--memories`), the `--serve` web daemon + panel (loads, WebSocket connects,
  `/api/status`, `/api/memories`, `/api/inbox` all work — only actual chat
  turns need auth), schedules/inbox/undo/history CLI commands, and config
  parsing. The test suite is designed to never need the CLI, network, audio, or
  ML models.
- **Web panel:** `uv run friday --serve` serves the chat panel at
  `http://127.0.0.1:4527` (override with `--port`). Needs a `friday.toml` with
  granted roots to be useful; without config it still runs but grants nothing.
- **`friday.toml` TOML gotcha:** top-level keys such as `data_dir` must appear
  **before** any `[section]` header. TOML assigns a bare key that follows a
  `[filesystem]` header to that table, so `data_dir` placed after a section is
  silently ignored (data lands in the default `~/.local/share/friday`).
- **Voice / hands-free is not testable in cloud.** `--voice` / `--handsfree`
  need PortAudio + a mic, and the Whisper/Piper/wake-word models download from
  `huggingface.co`, which is blocked in the sandbox. Don't attempt live audio
  here; `uv run friday --doctor` is the user-side check. The heavy `voice` /
  `handsfree` extras are intentionally NOT installed by the update script.
