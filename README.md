# friday.io

FRIDAY — a local-first personal AI assistant with voice and text, safe
filesystem powers, and an agentic Claude core. Think J.A.R.V.I.S., built
responsibly: full capability inside the folders you grant, confirmation and
audit for everything else.

See [docs/PLAN.md](docs/PLAN.md) for the architecture and roadmap.

## Status: Phase 2 (voice + text)

Working today:

- `friday` CLI — interactive REPL or one-shot prompts, streaming responses
- **Voice mode** (`friday --voice`) — push-to-talk: local Whisper
  speech-to-text, local Piper text-to-speech, sentence-streamed so FRIDAY
  starts speaking while it's still thinking; press Enter to barge in
- File powers via the Claude Agent SDK: search, read, write, edit, shell
- Permission gate: reads/writes inside granted roots are automatic;
  shell commands and out-of-root access require confirmation; credential
  paths (`~/.ssh`, `~/.aws`, …) are always denied
- Append-only audit log of every tool call and verdict

## Quick start

```bash
uv sync
cp friday.example.toml friday.toml   # edit granted_roots
uv run friday                        # interactive
uv run friday "summarize ~/Documents/taxes"
```

Requires Python 3.11+, [uv](https://docs.astral.sh/uv/), and the Claude Code
CLI (the Agent SDK's runtime) with an authenticated Anthropic account.

### Voice

```bash
uv sync --extra voice     # adds faster-whisper, piper, sounddevice
uv run friday --doctor    # self-test: downloads models, TTS→STT roundtrip
uv run friday --voice     # push-to-talk session
```

Speech recognition and synthesis run entirely on your machine; raw audio
never leaves it. First run downloads the models (~200 MB) from Hugging Face.
Linux needs PortAudio (`apt install libportaudio2`).

## Development

```bash
uv run pytest        # tests
uv run ruff check .  # lint
```
