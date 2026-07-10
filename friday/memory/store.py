"""Long-term memory: facts and preferences that persist across sessions.

Backed by SQLite FTS5 (BM25 keyword search). Retrieval is an interface —
`search(query)` — so a vector/embedding backend can replace the internals
later without touching callers (see docs/PLAN.md §7).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass
class Memory:
    id: int
    fact: str
    created: str


def fts_query(text: str) -> str:
    """Quote every term so user text can't break FTS5 MATCH syntax."""
    terms = [t.replace('"', "") for t in text.split()]
    return " ".join(f'"{t}"' for t in terms if t)


class MemoryStore:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS memories USING fts5(fact, created UNINDEXED)"
        )
        self._db.commit()

    def remember(self, fact: str) -> int:
        created = datetime.now(UTC).isoformat()
        cursor = self._db.execute(
            "INSERT INTO memories (fact, created) VALUES (?, ?)", (fact.strip(), created)
        )
        self._db.commit()
        return cursor.lastrowid

    def forget(self, memory_id: int) -> bool:
        cursor = self._db.execute("DELETE FROM memories WHERE rowid = ?", (memory_id,))
        self._db.commit()
        return cursor.rowcount > 0

    def search(self, query: str, limit: int = 8) -> list[Memory]:
        match = fts_query(query)
        if not match:
            return []
        rows = self._db.execute(
            "SELECT rowid, fact, created FROM memories WHERE memories MATCH ? "
            "ORDER BY bm25(memories) LIMIT ?",
            (match, limit),
        ).fetchall()
        return [Memory(*row) for row in rows]

    def recent(self, limit: int = 50) -> list[Memory]:
        rows = self._db.execute(
            "SELECT rowid, fact, created FROM memories ORDER BY rowid DESC LIMIT ?", (limit,)
        ).fetchall()
        return [Memory(*row) for row in reversed(rows)]
