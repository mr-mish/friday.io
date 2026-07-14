import json
from pathlib import Path

from friday.fs.audit import AuditLog, change_preview, summarize_result


def test_record_appends_jsonl(tmp_path: Path):
    log = AuditLog(tmp_path / "nested" / "audit.jsonl")
    log.record("tool_request", tool="Read", verdict="allow")
    log.record("confirmation", tool="Bash", approved=False)

    lines = (tmp_path / "nested" / "audit.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2
    first, second = (json.loads(line) for line in lines)
    assert first["event"] == "tool_request"
    assert first["tool"] == "Read"
    assert "ts" in first
    assert second["approved"] is False


def test_change_preview_create_vs_modify(tmp_path: Path):
    target = tmp_path / "note.txt"
    create = change_preview("Write", {"file_path": str(target), "content": "hello"})
    assert create == {"action": "create", "bytes_before": 0, "bytes_after": 5}

    target.write_text("hello")
    modify = change_preview("Write", {"file_path": str(target), "content": "hello world"})
    assert modify["action"] == "modify"
    assert modify["bytes_before"] == 5
    assert modify["bytes_after"] == 11


def test_change_preview_edit_delta(tmp_path: Path):
    target = tmp_path / "note.txt"
    target.write_text("abc")
    preview = change_preview(
        "Edit", {"file_path": str(target), "old_string": "a", "new_string": "aaaa"}
    )
    assert preview["bytes_delta"] == 3


def test_change_preview_ignores_non_write_tools():
    assert change_preview("Read", {"file_path": "/tmp/x"}) is None
    assert change_preview("Bash", {"command": "ls"}) is None


def test_summarize_result_truncates_long_output():
    assert summarize_result("ok") == "ok"
    assert summarize_result({"a": 1}) == '{"a": 1}'
    long = summarize_result("x" * 1000)
    assert long.endswith("…") and len(long) <= 502
