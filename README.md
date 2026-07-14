# friday.io

FRIDAY — a local-first personal AI assistant with voice and text, safe
filesystem powers, and an agentic Claude core. Think J.A.R.V.I.S., built
responsibly: full capability inside the folders you grant, confirmation and
audit for everything else.

See [docs/PLAN.md](docs/PLAN.md) for the architecture and roadmap.

## Status: Unified app (Phase 9 foundation)

Working today:

- **One seamless app** — `friday --serve` owns one long-lived agent session
  shared by the browser, terminal REPL, push-to-talk, and hands-free modes.
  Start a thought by voice and continue it by typing without losing context.
  Multiple local clients can observe the same turn and answer permission
  prompts; agent turns are serialized to keep the SDK session safe.
- **Persistent conversation memory** — user and assistant turns are stored in
  the local SQLite database, restored after reconnect/restart, and shown in
  the browser. This transcript is separate from FRIDAY's curated facts and
  preferences.
- **Voice in the web app** — click the microphone for local push-to-talk.
  Browser audio travels only over localhost, is transcribed by faster-whisper,
  and FRIDAY's Piper response streams back to the browser. Starting a new
  recording barges in and interrupts the current response.
- **Autonomy** — tell FRIDAY "every Friday at five, summarize my week" and it
  schedules itself; file triggers react to changes in watched folders. Runs
  happen unattended in the `--serve` daemon (or via `friday --run-due` from
  cron) under a hard rule: anything needing confirmation is declined and
  reported to your inbox (`friday --inbox`) — autonomous FRIDAY never
  self-approves. Failing schedules disable themselves after 3 strikes.
- **Hands-free voice** (`friday --handsfree`) — say the wake word, speak,
  FRIDAY answers; no keyboard. VAD detects when you've finished talking, an
  echo guard keeps FRIDAY from hearing itself, and the wake word barges in.
- **Voice trust** — optional speaker verification (`friday --enroll-voice`)
  so only your voice is obeyed; dangerous actions require echoing a one-time
  challenge phrase ("confirm tango"), and "undo that" reverts the last change.
- **Self-maintenance** — background index refresh and weekly memory
  consolidation run as built-in schedules; `friday --doctor` reports the
  health of the whole stack.

- **Web app** (`friday --serve`) — a localhost daemon (FastAPI + WebSocket)
  serving streaming text and voice. Permission prompts appear as native
  dialogs; a memories drawer shows what FRIDAY remembers.

- **Skills** — plug any MCP server into FRIDAY with a `[skills.NAME]` config
  entry (calendar, email, weather, Notion, …). Untrusted skills confirm every
  call; `trust = "allow"` auto-approves servers you vouch for
- **Named tasks** — `[tasks.NAME]` prompts run via `friday --run-task NAME`;
  schedule them with cron/launchd for proactive routines ("every Friday,
  summarize my week")
- **Web access** — WebSearch/WebFetch available to the agent, confirmed
  per-call unless `allow_web = true`

- `friday` CLI — interactive REPL or one-shot prompts, streaming responses
- **Voice mode** (`friday --voice`) — push-to-talk: local Whisper
  speech-to-text, local Piper text-to-speech, sentence-streamed so FRIDAY
  starts speaking while it's still thinking; press Enter to barge in
- **Long-term memory** — FRIDAY stores lasting facts and preferences on its
  own (or via `friday --remember`), recalls them in every future session,
  and forgets on request
- **Content search** — an incremental full-text index over your granted
  folders, including PDF and Word documents; ask for files by what's in
  them, not what they're called
- **Undo** — every file FRIDAY writes or edits is snapshotted first;
  `friday --undo` reverts the last change, `friday --history` lists them
- File powers via the Claude Agent SDK: search, read, write, edit, shell
- Permission gate: reads/writes inside granted roots are automatic;
  shell commands and out-of-root access require confirmation; credential
  paths (`~/.ssh`, `~/.aws`, …) are always denied — including in the index
- Append-only audit log of every tool call and verdict

## Quick start

```bash
uv sync
cp friday.example.toml friday.toml   # edit granted_roots
uv run friday                        # interactive
uv run friday "summarize ~/Documents/taxes"
```

Requires Python 3.13+, [uv](https://docs.astral.sh/uv/), and the Claude Code
CLI (the Agent SDK's runtime) with an authenticated Anthropic account.

### Desktop panel

```bash
uv sync --extra server --extra voice
uv run friday --serve     # unified text + voice app at http://127.0.0.1:4527
uv run friday             # another terminal joins that same session
```

If the daemon is not running, `friday`, `friday --voice`, and
`friday --handsfree` retain their embedded/offline session fallback.

### Voice

```bash
uv sync --extra voice     # adds faster-whisper, piper, sounddevice
uv run friday --doctor    # self-test: downloads models, TTS→STT roundtrip
uv run friday --voice     # push-to-talk session

uv sync --extra handsfree # + wake word (openwakeword) and speaker
                          #   verification (speechbrain; pulls torch)
uv run friday --enroll-voice   # optional: teach FRIDAY your voice
uv run friday --handsfree      # "Hey Jarvis…" — no keyboard
```

Speech recognition and synthesis run entirely on your machine; raw audio
never leaves it. First run downloads the models (~200 MB) from Hugging Face.
Linux needs PortAudio (`apt install libportaudio2`).

## Development

```bash
uv run pytest        # tests
uv run ruff check .  # lint
```
