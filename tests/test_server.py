"""Daemon tests with a fake agent — no Claude CLI, no network."""

import pytest

pytest.importorskip("fastapi")

from starlette.testclient import TestClient  # noqa: E402

from friday.config import FridayConfig
from friday.fs.permissions import Decision, Tier, Verdict
from friday.memory.store import MemoryStore
from friday.server.app import create_app


class FakeAgent:
    """Echoes; asks for confirmation when told to 'do something dangerous'."""

    def __init__(self, confirm, store):
        self._confirm = confirm
        self.store = store

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        pass

    async def ask(self, prompt):
        if "dangerous" in prompt:
            decision = Decision(Verdict.CONFIRM, Tier.DANGEROUS, "test requires confirmation")
            approved = await self._confirm("Bash", {"command": "rm x"}, decision)
            yield ("text", "approved" if approved else "declined")
        else:
            yield ("tool", "Read(x.txt)")
            yield ("text", f"echo: {prompt}")
        yield ("done", "$0.01")


@pytest.fixture
def client(tmp_path):
    config = FridayConfig(data_dir=tmp_path)
    store = MemoryStore(config.db_path)
    store.remember("The demo is on Friday.")
    app = create_app(config, agent_factory=lambda confirm: FakeAgent(confirm, store))
    with TestClient(app) as test_client:
        yield test_client


def _collect_turn(ws):
    events = []
    while True:
        msg = ws.receive_json()
        events.append(msg)
        if msg["type"] in ("done", "error"):
            return events


def test_chat_turn_streams_tool_text_done(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "user", "text": "hello"})
        events = _collect_turn(ws)
    assert [e["type"] for e in events] == ["tool", "text", "done"]
    assert events[1]["text"] == "echo: hello"
    assert events[2]["cost"] == "$0.01"


@pytest.mark.parametrize("approved,expected", [(True, "approved"), (False, "declined")])
def test_confirmation_roundtrip(client, approved, expected):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "user", "text": "do something dangerous"})
        confirm = ws.receive_json()
        assert confirm["type"] == "confirm"
        assert confirm["tool"] == "Bash"
        ws.send_json({"type": "confirm_response", "id": confirm["id"], "approved": approved})
        events = _collect_turn(ws)
    assert events[0]["text"] == expected


def test_second_client_rejected(client):
    with client.websocket_connect("/ws"), client.websocket_connect("/ws") as second:
        msg = second.receive_json()
        assert msg["type"] == "error"


def test_status_and_memories_endpoints(client):
    status = client.get("/api/status").json()
    assert status["version"]
    memories = client.get("/api/memories").json()
    assert memories[0]["fact"] == "The demo is on Friday."


def test_index_serves_chat_panel(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "FRIDAY" in page.text
