"""Persistent conversation transcript shared by every FRIDAY interface."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from friday.memory.store import fts_query

DEFAULT_CONVERSATION = "main"


@dataclass
class Message:
    id: int
    role: str
    content: str
    modality: str
    created: str


class ConversationStore:
    """Append-only chat history.

    This is deliberately separate from curated long-term facts in
    :class:`MemoryStore`: transcripts answer "what did we discuss?", while
    memories answer "what lasting facts should FRIDAY know?".
    """

    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=5000")
        self._db.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS conversation_messages USING fts5("
            "role UNINDEXED, content, modality UNINDEXED, conversation UNINDEXED, "
            "created UNINDEXED)"
        )
        self._db.commit()

    def append(
        self,
        role: str,
        content: str,
        modality: str = "text",
        conversation: str = DEFAULT_CONVERSATION,
    ) -> int:
        content = content.strip()
        if not content:
            return 0
        cursor = self._db.execute(
            "INSERT INTO conversation_messages "
            "(role, content, modality, conversation, created) VALUES (?, ?, ?, ?, ?)",
            (role, content, modality, conversation, datetime.now(UTC).isoformat()),
        )
        self._db.commit()
        return cursor.lastrowid

    def recent(
        self, limit: int = 50, conversation: str = DEFAULT_CONVERSATION
    ) -> list[Message]:
        rows = self._db.execute(
            "SELECT rowid, role, content, modality, created "
            "FROM conversation_messages WHERE conversation = ? "
            "ORDER BY rowid DESC LIMIT ?",
            (conversation, limit),
        ).fetchall()
        return [Message(*row) for row in reversed(rows)]

    def search(
        self,
        query: str,
        limit: int = 8,
        conversation: str = DEFAULT_CONVERSATION,
    ) -> list[Message]:
        match = fts_query(query)
        if not match:
            return []
        rows = self._db.execute(
            "SELECT rowid, role, content, modality, created "
            "FROM conversation_messages WHERE conversation_messages MATCH ? "
            "AND conversation = ? ORDER BY bm25(conversation_messages) LIMIT ?",
            (match, conversation, limit),
        ).fetchall()
        return [Message(*row) for row in rows]

    def clear(self, conversation: str = DEFAULT_CONVERSATION) -> int:
        cursor = self._db.execute(
            "DELETE FROM conversation_messages WHERE conversation = ?", (conversation,)
        )
        self._db.commit()
        return cursor.rowcount
