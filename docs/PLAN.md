# FRIDAY — Personal AI Assistant: Architecture & Implementation Plan

A JARVIS-style personal assistant that runs on your own computer, with full
(user-granted) access to local folders and files, and both voice and text
interaction. This document is the working plan for building it in this
repository.

## 1. Vision

FRIDAY is a local-first assistant daemon that you can talk to or type to. It
can find, read, summarize, organize, and edit your files; run tasks on your
machine; remember context across sessions; and speak its answers back to you.
The intelligence comes from a frontier LLM (Claude) driving an agentic
tool-use loop; the ears, mouth, and hands are local components.

Guiding principles:

- **Local-first**: audio capture, wake word, file indexing, and memory live on
  the user's machine. Only the reasoning calls go to the LLM API (with an
  option to redact/deny paths).
- **Capable but consented**: full filesystem power, gated by an explicit
  permission model with an audit trail — JARVIS, not a rootkit.
- **Incremental**: every phase ends with something usable end-to-end.

## 2. Capability Targets

| Capability | Description |
|---|---|
| Conversational Q&A | Natural text and voice conversation with context |
| File awareness | Search, read, summarize any file/folder the user grants |
| File actions | Create, edit, move, rename, organize files (confirm destructive ops) |
| Semantic search | "Find that PDF about the lease from last spring" |
| System actions | Open apps, run shell commands (tiered permissions) |
| Long-term memory | Remembers preferences, people, projects across sessions |
| Voice I/O | Wake word → STT → agent → streaming TTS, with barge-in |
| Proactivity | Scheduled tasks, reminders, "watch this folder" triggers |
| Extensibility | Skills/plugins via MCP (calendar, email, home automation, web) |

## 3. High-Level Architecture

```
                        ┌────────────────────────────────────────┐
                        │              FRIDAY Daemon             │
                        │                                        │
 ┌─────────────┐        │  ┌──────────┐      ┌────────────────┐  │
 │ Mic / Audio ├──VAD──▶│  │  Voice   │      │   Agent Core   │  │      ┌────────────┐
 │   Output    │◀──TTS──┤  │ Pipeline ├─text▶│ (Claude Agent  │◀─┼─────▶│ Claude API │
 └─────────────┘        │  └──────────┘      │  SDK loop)     │  │      └────────────┘
                        │                    └───┬────────┬───┘  │
 ┌─────────────┐        │  ┌──────────┐          │        │      │
 │ CLI / Text  │◀──────▶│  │ Session  │      ┌───▼───┐ ┌──▼───┐  │
 │ UI / Tray   │        │  │  Router  │      │ Tools │ │Memory│  │
 └─────────────┘        │  └──────────┘      └───┬───┘ └──┬───┘  │
                        │                        │        │      │
                        │  ┌─────────────────────▼──┐ ┌───▼───┐  │
                        │  │ Permission Gate + Audit│ │Vector │  │
                        │  └─────────────────────┬──┘ │ Index │  │
                        └────────────────────────┼────┴───────┴──┘
                                                 ▼
                                     Filesystem / Shell / Apps / MCP
```

Components:

1. **Agent Core** — the brain. An agentic loop built on the **Claude Agent
   SDK**, which ships with production-grade file tools (Read, Write, Edit,
   Glob, Grep, Bash), sub-agents, hooks, and MCP client support out of the
   box. This saves us from reimplementing a tool-use loop and gets FRIDAY
   most of its "hands" for free.
2. **Voice Pipeline** — wake word + voice activity detection + streaming
   speech-to-text on the way in; streaming text-to-speech on the way out.
3. **Session Router** — one conversation state shared across modalities: you
   can start by voice and continue by keyboard.
4. **Permission Gate + Audit Log** — every tool call passes through policy
   (allow / confirm / deny by path and action class); everything is journaled.
5. **Memory** — short-term (conversation), long-term (facts/preferences), and
   a semantic index over user-designated folders.
6. **Interfaces** — CLI first, then system tray + desktop panel (Tauri).
7. **Skills via MCP** — calendar, email, browser, smart-home, etc. plug in as
   MCP servers rather than bespoke integrations.

## 4. Tech Stack (recommendation)

| Layer | Choice | Rationale |
|---|---|---|
| Language | **Python 3.12+** | Best ecosystem for the audio/ML stack; Claude Agent SDK available |
| Agent loop | **Claude Agent SDK (Python)** | Built-in file tools, permissions hooks, MCP, sub-agents |
| LLM | Claude (Sonnet for routine turns, Opus-tier for hard tasks) | Latency/cost/quality routing |
| Wake word | **openWakeWord** (or Picovoice Porcupine for accuracy) | Local, low CPU |
| VAD | **Silero VAD** | Local, robust |
| STT | **faster-whisper** (local) with cloud fallback option | Private by default, near-realtime on modern CPUs/GPUs |
| TTS | **Piper** (local) with **ElevenLabs** as premium option | Piper is fast + offline; ElevenLabs for JARVIS-grade voice |
| Vector index | **SQLite + sqlite-vec** (upgrade path: LanceDB) | Zero-ops, single file, good enough for personal corpus |
| Embeddings | Local (e.g. `bge-small`) or Voyage API | Privacy vs. quality toggle |
| Daemon/IPC | FastAPI over localhost + WebSocket | Simple, debuggable, UI-agnostic |
| Desktop UI | **Tauri** (Phase 5) | Lightweight tray + chat/voice panel |
| Packaging | `uv` for env, PyInstaller/briefcase later | Reproducible dev now, installer later |

Alternative considered: full TypeScript/Electron stack — better UI story, but
materially worse local audio/ML ecosystem. Python wins for the core; Tauri
covers the UI later without dictating the backend language.

## 5. Voice Pipeline Detail

```
mic → ring buffer → openWakeWord ("Hey Friday")
    → Silero VAD segments utterance → faster-whisper streaming transcript
    → Agent Core (streaming response tokens)
    → sentence-chunked TTS (Piper) → audio out
    → barge-in: VAD during playback pauses TTS and starts a new capture
```

- Target: **< 1.5 s** from end-of-utterance to first spoken audio (stream
  tokens to TTS sentence-by-sentence; don't wait for the full response).
- Push-to-talk hotkey ships **before** wake word (simpler, private, reliable);
  wake word is an upgrade, not the MVP.
- All audio processing local; raw audio never leaves the machine.

## 6. Filesystem Access & Safety Model

Full access is the feature; unmediated access is the bug. Three tiers:

| Tier | Actions | Policy |
|---|---|---|
| Read | read, search, index, summarize | Allowed within user-granted roots |
| Write | create, edit, move within granted roots | Allowed; journaled with undo (trash, not delete) |
| Dangerous | delete, chmod, shell commands, anything outside roots, network sends | Explicit confirmation per action (voice: "confirm delete") |

- **Granted roots**: user picks folders at setup (`~/Documents`, `~/Projects`,
  …). Denylist always wins (`~/.ssh`, browser profiles, keychains).
- **Audit log**: append-only JSONL of every tool call and its diff/outcome.
- **Undo**: writes go through a shadow journal; moves/deletes go to a
  FRIDAY-managed trash with `friday undo`.
- Implemented with the Agent SDK's permission hooks (`canUseTool`), not by
  trusting the model's judgment.

## 7. Memory Design

1. **Working memory** — current conversation + auto-compaction (SDK handles).
2. **Long-term memory** — extracted facts/preferences ("user's accountant is
   Dana", "always export invoices as PDF") in SQLite, retrieved by relevance
   and injected into context.
3. **File index** — background watcher (watchdog) incrementally embeds
   documents in granted roots (text, PDF, docx, code) into sqlite-vec;
   powers semantic search and "what changed in X this week".

## 8. Roadmap

### Phase 0 — Scaffolding (small)
Repo layout, `uv` project, config format (`friday.toml`), CI (lint + tests),
update CLAUDE.md with real commands.

### Phase 1 — Text-mode agent with file powers (the real MVP)
`friday` CLI REPL: Claude Agent SDK loop, granted-roots permission gate,
audit log, read/search/write file tools, streaming responses.
**Done when:** "summarize ~/Documents/taxes and rename the files by date"
works safely from the terminal.

### Phase 2 — Voice I/O
Push-to-talk hotkey → faster-whisper → agent → Piper TTS with sentence
streaming and barge-in. Then wake word ("Hey Friday") via openWakeWord.
**Done when:** a full spoken round-trip feels < 2 s and can be interrupted.

### Phase 3 — Memory & semantic index
sqlite-vec index over granted roots, background watcher, long-term memory
store, `friday remember/forget`.
**Done when:** "find that lease PDF from last spring" works by meaning, and
FRIDAY recalls preferences across restarts.

### Phase 4 — Skills & system integration
MCP client wiring: calendar, email, web search/fetch, app launching,
clipboard; scheduled/proactive tasks ("every Friday summarize my week").
**Done when:** third-party capability = drop in an MCP server + config entry.

### Phase 5 — Desktop presence
Tauri tray app: chat panel, live transcript, permission prompts as native
dialogs, settings UI. Installer packaging.

## 9. Repo Layout (target)

```
friday.io/
├── friday/
│   ├── agent/        # Agent SDK loop, prompts, model routing
│   ├── voice/        # wake word, VAD, STT, TTS, audio io
│   ├── fs/           # roots, permission gate, journal/undo, trash
│   ├── memory/       # long-term store, embeddings, file index
│   ├── skills/       # MCP server configs and adapters
│   ├── server/       # FastAPI daemon + WebSocket API
│   └── cli.py
├── ui/               # Tauri app (Phase 5)
├── tests/
└── docs/
```

## 10. Key Risks

- **Voice latency** is the make-or-break UX metric — mitigate with streaming
  at every stage and model routing (small model for chit-chat turns).
- **Filesystem trust**: one bad delete destroys user trust — trash + journal
  + confirmation tiers are non-negotiable from Phase 1.
- **Privacy**: file contents reach the LLM API during reasoning — make the
  granted-roots and redaction story explicit in docs and settings.
- **Scope creep**: JARVIS is infinite; the phase gates above are the defense.
  Ship the text-mode file agent first.
