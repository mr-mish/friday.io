from pathlib import Path

from friday.fs.undo import UndoJournal


def test_modify_then_undo_restores_previous_content(tmp_path: Path):
    journal = UndoJournal(tmp_path / "data")
    f = tmp_path / "report.txt"
    f.write_text("original")

    journal.record_change(f)  # what the agent does just before writing
    f.write_text("clobbered")

    message = journal.undo_last()
    assert "Restored" in message
    assert f.read_text() == "original"


def test_create_then_undo_trashes_the_file(tmp_path: Path):
    journal = UndoJournal(tmp_path / "data")
    f = tmp_path / "new.txt"

    journal.record_change(f)  # file does not exist yet -> "create"
    f.write_text("made by friday")

    message = journal.undo_last()
    assert "Removed" in message
    assert not f.exists()
    # the content is trashed, not destroyed
    trashed = list((tmp_path / "data" / "undo" / "snapshots").glob("trashed_*"))
    assert trashed and trashed[0].read_text() == "made by friday"


def test_undo_walks_backwards_through_history(tmp_path: Path):
    journal = UndoJournal(tmp_path / "data")
    f = tmp_path / "story.txt"
    for version in ("v1", "v2", "v3"):
        journal.record_change(f)
        f.write_text(version)

    journal.undo_last()
    assert f.read_text() == "v2"
    journal.undo_last()
    assert f.read_text() == "v1"


def test_history_marks_undone_entries(tmp_path: Path):
    journal = UndoJournal(tmp_path / "data")
    f = tmp_path / "a.txt"
    journal.record_change(f)
    f.write_text("x")
    journal.undo_last()

    changes = journal.history()
    assert changes[0].undone is True


def test_undo_with_empty_journal(tmp_path: Path):
    assert UndoJournal(tmp_path / "data").undo_last() == "Nothing to undo."
