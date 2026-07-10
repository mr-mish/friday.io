import json
from pathlib import Path

from friday.fs.audit import AuditLog


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
