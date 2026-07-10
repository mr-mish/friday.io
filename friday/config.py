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
class FridayConfig:
    granted_roots: list[Path] = field(default_factory=list)
    denied_paths: list[Path] = field(
        default_factory=lambda: [Path(p).expanduser() for p in DEFAULT_DENIED]
    )
    model: str | None = None  # None = Agent SDK default
    data_dir: Path = field(default_factory=lambda: Path(DEFAULT_DATA_DIR).expanduser())
    system_prompt_extra: str = ""

    @property
    def audit_log_path(self) -> Path:
        return self.data_dir / "audit.jsonl"


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
    return config
