"""Append-only JSONL audit log of every tool call, its verdict, and outcome."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

_WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
_RESULT_PREVIEW_CHARS = 500


class AuditLog:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event: str, **fields: object) -> None:
        entry = {"ts": datetime.now(UTC).isoformat(), "event": event, **fields}
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")


def change_preview(tool_name: str, tool_input: dict) -> dict | None:
    """A compact, before-the-write summary of a file-mutating tool call.

    Computed at PreToolUse time (before the write happens), so it captures the
    intended change — whether the file is being created or modified and the
    size delta — without needing the tool's result. Returns None for tools
    that don't mutate a file.
    """
    if tool_name not in _WRITE_TOOLS:
        return None
    path_str = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not path_str:
        return None
    path = Path(str(path_str)).expanduser()
    try:
        before = path.stat().st_size if path.exists() else 0
    except OSError:
        before = 0
    preview: dict = {"action": "modify" if before else "create", "bytes_before": before}
    content = tool_input.get("content")
    if tool_name == "Write" and isinstance(content, str):
        preview["bytes_after"] = len(content.encode("utf-8"))
    elif tool_name == "Edit":
        old = str(tool_input.get("old_string", ""))
        new = str(tool_input.get("new_string", ""))
        preview["bytes_delta"] = len(new.encode("utf-8")) - len(old.encode("utf-8"))
    return preview


def summarize_result(tool_response: object) -> str:
    """Truncate a tool's response to a short, log-friendly preview string."""
    text = tool_response if isinstance(tool_response, str) else json.dumps(
        tool_response, default=str
    )
    text = text.strip()
    if len(text) > _RESULT_PREVIEW_CHARS:
        return text[:_RESULT_PREVIEW_CHARS] + "…"
    return text
