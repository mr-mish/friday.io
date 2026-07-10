"""The undo safety net: every agent write is reversible.

Before FRIDAY writes or edits a file, the previous contents are snapshotted
into ``data_dir/undo/snapshots/`` and the change is journaled. ``friday
--undo`` restores the most recent change; ``friday --history`` lists them.
Creations are journaled too — undoing one deletes the file FRIDAY created.

Shell-command writes can't be tracked (the gate makes those confirm-per-call
instead); this journal covers the Write/Edit tools, which is how FRIDAY does
nearly all of its file modification.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass
class Change:
    id: int
    ts: str
    action: str  # "modify" | "create"
    path: str
    snapshot: str | None  # snapshot file for "modify", None for "create"
    undone: bool = False


class UndoJournal:
    def __init__(self, data_dir: Path):
        self.snapshots = data_dir / "undo" / "snapshots"
        self.snapshots.mkdir(parents=True, exist_ok=True)
        self.journal = data_dir / "undo" / "journal.jsonl"

    def _append(self, entry: dict) -> None:
        with open(self.journal, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    def _entries(self) -> list[dict]:
        if not self.journal.exists():
            return []
        with open(self.journal, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def record_change(self, path: Path) -> None:
        """Call BEFORE a file is written: snapshots current contents."""
        path = path.expanduser().resolve()
        entries = self._entries()
        change_id = 1 + max((e.get("id", 0) for e in entries), default=0)
        snapshot: str | None = None
        action = "create"
        if path.exists():
            action = "modify"
            snapshot_file = self.snapshots / f"{change_id:06d}_{path.name}"
            shutil.copy2(path, snapshot_file)
            snapshot = str(snapshot_file)
        self._append(
            {
                "id": change_id,
                "ts": datetime.now(UTC).isoformat(),
                "action": action,
                "path": str(path),
                "snapshot": snapshot,
            }
        )

    def history(self, limit: int = 20) -> list[Change]:
        """Most recent changes first."""
        undone_ids = set()
        changes: list[Change] = []
        for entry in self._entries():
            if entry.get("type") == "undo":
                undone_ids.add(entry["target_id"])
            else:
                changes.append(Change(**{k: entry.get(k) for k in Change.__dataclass_fields__}))
        for change in changes:
            change.undone = change.id in undone_ids
        return list(reversed(changes))[:limit]

    def undo_last(self) -> str:
        """Revert the most recent not-yet-undone change. Returns a description."""
        candidates = [c for c in self.history(limit=10_000) if not c.undone]
        if not candidates:
            return "Nothing to undo."
        change = candidates[0]
        target = Path(change.path)
        if change.action == "modify" and change.snapshot:
            shutil.copy2(change.snapshot, target)
            result = f"Restored {target} to its state from {change.ts[:19]}."
        elif change.action == "create" and target.exists():
            trash = self.snapshots / f"trashed_{change.id:06d}_{target.name}"
            shutil.move(str(target), trash)
            result = f"Removed created file {target} (kept in {trash})."
        else:
            result = f"Nothing to restore for {target} (already gone)."
        self._append({"type": "undo", "target_id": change.id, "ts": datetime.now(UTC).isoformat()})
        return result
