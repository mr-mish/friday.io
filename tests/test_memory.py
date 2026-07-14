from pathlib import Path

from friday.memory.index import FileIndex
from friday.memory.store import MemoryStore, fts_query


def test_remember_recall_forget(tmp_path: Path):
    store = MemoryStore(tmp_path / "friday.db")
    a = store.remember("The user's accountant is named Dana.")
    store.remember("Invoices should always be exported as PDF.")

    hits = store.search("accountant")
    assert len(hits) == 1 and hits[0].id == a

    assert store.forget(a) is True
    assert store.forget(a) is False
    assert store.search("accountant") == []


def test_remember_deduplicates_identical_facts(tmp_path: Path):
    store = MemoryStore(tmp_path / "friday.db")
    first = store.remember("Invoices should always be exported as PDF.")
    # Re-stating the same fact (even with surrounding whitespace) is a no-op.
    again = store.remember("  Invoices should always be exported as PDF.  ")
    assert again == first
    assert len(store.recent(limit=10)) == 1


def test_recent_returns_oldest_first_capped(tmp_path: Path):
    store = MemoryStore(tmp_path / "friday.db")
    for i in range(5):
        store.remember(f"fact number {i}")
    recent = store.recent(limit=3)
    assert [m.fact for m in recent] == ["fact number 2", "fact number 3", "fact number 4"]


def test_fts_query_neutralizes_match_syntax():
    # raw MATCH operators/quotes must not crash or change semantics
    assert fts_query('lease OR "secret*') == '"lease" "OR" "secret*"'
    store_query = fts_query("")
    assert store_query == ""


def test_index_and_search(tmp_path: Path):
    root = tmp_path / "docs"
    root.mkdir()
    (root / "lease.md").write_text("Apartment lease agreement, rent due monthly.")
    (root / "notes.txt").write_text("Groceries: milk, eggs, coffee beans.")

    index = FileIndex(tmp_path / "friday.db", roots=[root], denied=[])
    counts = index.refresh()
    assert counts["indexed"] == 2

    hits = index.search("lease rent")
    assert len(hits) == 1
    assert hits[0].path.endswith("lease.md")


def test_index_never_reads_denied_paths(tmp_path: Path):
    root = tmp_path / "docs"
    secret = root / "private"
    secret.mkdir(parents=True)
    (root / "ok.txt").write_text("visible content")
    (secret / "keys.txt").write_text("supersecret credentials")

    index = FileIndex(tmp_path / "friday.db", roots=[root], denied=[secret])
    index.refresh()
    assert index.search("supersecret") == []
    assert len(index.search("visible")) == 1


def test_index_updates_and_removals(tmp_path: Path):
    root = tmp_path / "docs"
    root.mkdir()
    f = root / "plan.md"
    f.write_text("old topic: gardening")
    index = FileIndex(tmp_path / "friday.db", roots=[root], denied=[])
    index.refresh()
    assert len(index.search("gardening")) == 1

    import os

    f.write_text("new topic: astronomy")
    os.utime(f, (f.stat().st_atime, f.stat().st_mtime + 5))
    index.refresh()
    assert index.search("gardening") == []
    assert len(index.search("astronomy")) == 1

    f.unlink()
    counts = index.refresh()
    assert counts["removed"] == 1
    assert index.search("astronomy") == []


def test_memory_tools_are_auto_allowed():
    from friday.config import FridayConfig
    from friday.fs.permissions import PermissionGate, Verdict

    gate = PermissionGate(FridayConfig())
    for name in (
        "mcp__memory__remember",
        "mcp__memory__recall",
        "mcp__memory__forget",
        "mcp__memory__search_files",
    ):
        assert gate.evaluate(name, {"fact": "x", "query": "y"}).verdict is Verdict.ALLOW
