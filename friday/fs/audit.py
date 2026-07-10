"""Append-only JSONL audit log of every tool call and its verdict."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path


class AuditLog:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event: str, **fields: object) -> None:
        entry = {"ts": datetime.now(UTC).isoformat(), "event": event, **fields}
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
