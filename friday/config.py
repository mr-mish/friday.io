"""Configuration loading for FRIDAY.

Configuration lives in ``friday.toml``. Search order:

1. ``$FRIDAY_CONFIG`` (explicit path)
2. ``./friday.toml`` (current directory)
3. ``~/.config/friday/friday.toml``

Missing config is not an error: FRIDAY starts with an empty set of granted
roots, meaning every filesystem action requires confirmation and writes are
denied. Granting roots is an explicit user act.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# Paths that are never readable or writable, even inside a granted root.
# Secrets and credential stores: the deny list always wins.
DEFAULT_DENIED = [
    "~/.ssh",
    "~/.gnupg",
    "~/.aws",
    "~/.config/gcloud",
    "~/.kube",
    "~/.netrc",
    "~/.npmrc",
    "~/.pypirc",
]

DEFAULT_DATA_DIR = "~/.local/share/friday"


@dataclass
class SkillConfig:
    """One external MCP server = one skill (calendar, email, weather, …)."""

    command: str | None = None  # stdio server: executable
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str | None = None  # http server (mutually exclusive with command)
    trust: str = "confirm"  # "confirm" = ask per call; "allow" = auto-approve


@dataclass
class TaskConfig:
    """A named prompt the user can run on demand or from cron/launchd."""

    prompt: str
    description: str = ""


@dataclass
class FridayConfig:
    granted_roots: list[Path] = field(default_factory=list)
    denied_paths: list[Path] = field(
        default_factory=lambda: [Path(p).expanduser() for p in DEFAULT_DENIED]
    )
    model: str | None = None  # None = Agent SDK default
    data_dir: Path = field(default_factory=lambda: Path(DEFAULT_DATA_DIR).expanduser())
    system_prompt_extra: str = ""
    stt_model: str = "base"  # faster-whisper size: tiny/base/small/medium
    tts_voice: str = "en_US-lessac-medium"  # Piper voice name
    language: str | None = None  # None = auto-detect
    allow_web: bool = False  # auto-approve WebSearch/WebFetch instead of confirming
    skills: dict[str, SkillConfig] = field(default_factory=dict)
    tasks: dict[str, TaskConfig] = field(default_factory=dict)
    # autonomy
    autonomy_enabled: bool = True  # daemon runs schedules/triggers
    poll_seconds: int = 30  # autonomy loop cadence in the daemon
    budget_usd: float = 1.0  # hard cap per autonomous run
    quiet_hours: str = ""  # e.g. "22:00-08:00": no spoken announcements
    triggers: dict[str, dict] = field(default_factory=dict)  # name -> {pattern, prompt}
    # hands-free voice
    wake_word: str = "hey_jarvis"  # openWakeWord model name
    verify_speaker: bool = False  # require enrolled voice for commands
    verify_threshold: float = 0.5  # ECAPA cosine floor (same speaker ≈ 0.5-0.8)

    @property
    def audit_log_path(self) -> Path:
        return self.data_dir / "audit.jsonl"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "friday.db"

    @property
    def voices_dir(self) -> Path:
        return self.data_dir / "voices"


def _expand(raw: str) -> Path:
    return Path(os.path.expandvars(raw)).expanduser()


def find_config_file() -> Path | None:
    env = os.environ.get("FRIDAY_CONFIG")
    candidates = [Path(env)] if env else []
    candidates += [Path.cwd() / "friday.toml", Path("~/.config/friday/friday.toml").expanduser()]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def load_config(path: Path | None = None) -> FridayConfig:
    path = path or find_config_file()
    if path is None:
        return FridayConfig()

    with open(path, "rb") as f:
        raw = tomllib.load(f)

    fs = raw.get("filesystem", {})
    config = FridayConfig(
        granted_roots=[_expand(p) for p in fs.get("granted_roots", [])],
        model=raw.get("agent", {}).get("model"),
        system_prompt_extra=raw.get("agent", {}).get("system_prompt_extra", ""),
    )
    # User-denied paths extend the defaults; they can never shrink them.
    config.denied_paths += [_expand(p) for p in fs.get("denied_paths", [])]
    if data_dir := raw.get("data_dir"):
        config.data_dir = _expand(data_dir)

    voice = raw.get("voice", {})
    config.stt_model = voice.get("stt_model", config.stt_model)
    config.tts_voice = voice.get("tts_voice", config.tts_voice)
    config.language = voice.get("language", config.language)

    config.allow_web = bool(raw.get("agent", {}).get("allow_web", False))
    for name, skill in raw.get("skills", {}).items():
        if skill.get("trust", "confirm") not in ("confirm", "allow"):
            raise ValueError(f"skill '{name}': trust must be 'confirm' or 'allow'")
        config.skills[name] = SkillConfig(
            command=skill.get("command"),
            args=list(skill.get("args", [])),
            env=dict(skill.get("env", {})),
            url=skill.get("url"),
            trust=skill.get("trust", "confirm"),
        )
    for name, task in raw.get("tasks", {}).items():
        config.tasks[name] = TaskConfig(
            prompt=task["prompt"], description=task.get("description", "")
        )

    autonomy = raw.get("autonomy", {})
    config.autonomy_enabled = bool(autonomy.get("enabled", True))
    config.poll_seconds = int(autonomy.get("poll_seconds", 30))
    config.budget_usd = float(autonomy.get("budget_usd", 1.0))
    config.quiet_hours = autonomy.get("quiet_hours", "")
    for name, trig in raw.get("triggers", {}).items():
        config.triggers[name] = {"pattern": trig["pattern"], "prompt": trig["prompt"]}

    config.wake_word = voice.get("wake_word", config.wake_word)
    config.verify_speaker = bool(voice.get("verify_speaker", False))
    config.verify_threshold = float(voice.get("verify_threshold", 0.75))
    return config
