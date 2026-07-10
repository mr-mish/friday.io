"""A content index over the user's granted folders.

"Find that lease document from last spring" needs search by content, not
filename. This walks the granted roots, extracts text from files it can read,
and keeps an SQLite FTS5 index (BM25) up to date incrementally — only files
whose mtime/size changed are re-read. The deny list is enforced at index
time: denied paths are never read, so their content can never leak through
search results.

Vector embeddings are the planned upgrade for true semantic search; this
module's `search()` interface is what an embedding backend will slot into.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from friday.fs.permissions import is_under
from friday.memory.store import fts_query

TEXT_EXTENSIONS = {
    ".txt", ".md", ".rst", ".csv", ".json", ".yaml", ".yml", ".toml", ".ini",
    ".py", ".js", ".ts", ".html", ".css", ".sh", ".sql", ".tex", ".log",
}  # fmt: skip
SKIP_DIRS = {"node_modules", "__pycache__", ".venv", "venv", ".git"}
MAX_FILE_BYTES = 2_000_000
CHUNK_CHARS = 1500


@dataclass
class Hit:
    path: str
    snippet: str


def _chunks(text: str) -> list[str]:
    return [text[i : i + CHUNK_CHARS] for i in range(0, len(text), CHUNK_CHARS)]


class FileIndex:
    def __init__(self, db_path: Path, roots: list[Path], denied: list[Path]):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.roots = roots
        self.denied = denied
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS indexed_files "
            "(path TEXT PRIMARY KEY, mtime REAL, size INTEGER)"
        )
        self._db.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS file_chunks USING fts5(body, path UNINDEXED)"
        )
        self._db.commit()

    def _eligible_files(self) -> dict[str, tuple[float, int]]:
        found: dict[str, tuple[float, int]] = {}
        for root in self.roots:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [
                    d
                    for d in dirnames
                    if not d.startswith(".")
                    and d not in SKIP_DIRS
                    and not is_under(Path(dirpath) / d, self.denied)
                ]
                for name in filenames:
                    path = Path(dirpath) / name
                    if name.startswith(".") or path.suffix.lower() not in TEXT_EXTENSIONS:
                        continue
                    if is_under(path, self.denied):
                        continue
                    try:
                        stat = path.stat()
                    except OSError:
                        continue
                    if stat.st_size <= MAX_FILE_BYTES:
                        found[str(path)] = (stat.st_mtime, stat.st_size)
        return found

    def refresh(self) -> dict[str, int]:
        """Bring the index up to date. Returns counts for reporting."""
        current = self._eligible_files()
        known = dict(
            (path, (mtime, size))
            for path, mtime, size in self._db.execute(
                "SELECT path, mtime, size FROM indexed_files"
            )
        )

        removed = [path for path in known if path not in current]
        stale = [path for path, sig in current.items() if known.get(path) != sig]

        for path in removed + stale:
            self._db.execute("DELETE FROM file_chunks WHERE path = ?", (path,))
            self._db.execute("DELETE FROM indexed_files WHERE path = ?", (path,))
        for path in stale:
            try:
                text = Path(path).read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            self._db.executemany(
                "INSERT INTO file_chunks (body, path) VALUES (?, ?)",
                [(chunk, path) for chunk in _chunks(text) if chunk.strip()],
            )
            mtime, size = current[path]
            self._db.execute(
                "INSERT INTO indexed_files (path, mtime, size) VALUES (?, ?, ?)",
                (path, mtime, size),
            )
        self._db.commit()
        return {"indexed": len(stale), "removed": len(removed), "total": len(current)}

    def search(self, query: str, limit: int = 8) -> list[Hit]:
        match = fts_query(query)
        if not match:
            return []
        rows = self._db.execute(
            "SELECT path, snippet(file_chunks, 0, '', '', '…', 24) FROM file_chunks "
            "WHERE file_chunks MATCH ? ORDER BY bm25(file_chunks) LIMIT ?",
            (match, limit),
        ).fetchall()
        return [Hit(path, snippet) for path, snippet in rows]
