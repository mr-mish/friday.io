"""The inbox: how autonomous FRIDAY reaches the user.

Unattended runs can't pop dialogs, so results, declined actions, and
watchdog alerts land here. The daemon pushes unread notices to a connected
chat panel; `friday --inbox` reads them from the CLI. Quiet hours only gate
*spoken* announcements — the inbox always accepts writes.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, time
from pathlib import Path

_QUIET = re.compile(r"^(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})$")


def in_quiet_hours(spec: str, now: time) -> bool:
    """spec like "22:00-08:00" (overnight ranges wrap midnight)."""
    if not spec:
        return False
    m = _QUIET.match(spec)
    if not m:
        raise ValueError(f"bad quiet_hours {spec!r} — use HH:MM-HH:MM")
    start = time(int(m.group(1)), int(m.group(2)))
    end = time(int(m.group(3)), int(m.group(4)))
    if start <= end:
        return start <= now < end
    return now >= start or now < end  # overnight


@dataclass
class Notice:
    id: int
    ts: str
    source: str
    message: str
    read: bool


class Inbox:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS notifications ("
            "id INTEGER PRIMARY KEY, ts TEXT, source TEXT, message TEXT, "
            "read INTEGER DEFAULT 0)"
        )
        self._db.commit()

    def add(self, source: str, message: str) -> Notice:
        ts = datetime.now(UTC).isoformat()
        cursor = self._db.execute(
            "INSERT INTO notifications (ts, source, message) VALUES (?, ?, ?)",
            (ts, source, message),
        )
        self._db.commit()
        return Notice(cursor.lastrowid, ts, source, message, False)

    def unread(self) -> list[Notice]:
        rows = self._db.execute(
            "SELECT id, ts, source, message, read FROM notifications WHERE read = 0 ORDER BY id"
        ).fetchall()
        return [Notice(*row[:4], bool(row[4])) for row in rows]

    def mark_read(self, ids: list[int]) -> None:
        self._db.executemany(
            "UPDATE notifications SET read = 1 WHERE id = ?", [(i,) for i in ids]
        )
        self._db.commit()
