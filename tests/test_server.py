"""Daemon tests with a fake agent — no Claude CLI, no network."""

import base64

import pytest

pytest.importorskip("fastapi")

from starlette.testclient import TestClient  # noqa: E402

from friday.config import FridayConfig
from friday.fs.permissions import Decision, Tier, Verdict
from friday.memory.store import MemoryStore
from friday.server.app import create_app
from friday.server.voice_bridge import VoiceBridge


class FakeAgent:
    """Echoes; asks for confirmation when told to 'do something dangerous'."""

    def __init__(self, confirm, store, history=None):
        self._confirm = confirm
        self.store = store
        self.history = history

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        pass

    async def ask(self, prompt):
        if self.history:
            self.history.append("user", prompt)
        if "dangerous" in prompt:
            decision = Decision(Verdict.CONFIRM, Tier.DANGEROUS, "test requires confirmation")
            approved = await self._confirm("Bash", {"command": "rm x"}, decision)
            response = "approved" if approved else "declined"
            yield ("text", response)
        else:
            yield ("tool", "Read(x.txt)")
            response = f"echo: {prompt}"
            yield ("text", response)
        if self.history:
            self.history.append("assistant", response)
        yield ("done", "$0.01")


@pytest.fixture
def client(tmp_path):
    from friday.memory.history import ConversationStore

    config = FridayConfig(data_dir=tmp_path)
    config.autonomy_enabled = False
    store = MemoryStore(config.db_path)
    history = ConversationStore(config.db_path)
    store.remember("The demo is on Friday.")
    app = create_app(
        config, agent_factory=lambda confirm: FakeAgent(confirm, store, history)
    )
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
        sync = ws.receive_json()
        assert sync["type"] == "sync"
        ws.send_json({"type": "user", "text": "hello"})
        events = _collect_turn(ws)
    assert [e["type"] for e in events] == ["user", "turn_start", "tool", "text", "done"]
    assert events[3]["text"] == "echo: hello"
    assert events[4]["cost"] == "$0.01"


@pytest.mark.parametrize("approved,expected", [(True, "approved"), (False, "declined")])
def test_confirmation_roundtrip(client, approved, expected):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()  # sync
        ws.send_json({"type": "user", "text": "do something dangerous"})
        assert ws.receive_json()["type"] == "user"
        assert ws.receive_json()["type"] == "turn_start"
        confirm = ws.receive_json()
        assert confirm["type"] == "confirm"
        assert confirm["tool"] == "Bash"
        ws.send_json({"type": "confirm_response", "id": confirm["id"], "approved": approved})
        events = _collect_turn(ws)
    assert events[0]["text"] == expected


def test_multiple_clients_share_the_same_turn(client):
    with client.websocket_connect("/ws") as first, client.websocket_connect("/ws") as second:
        first.receive_json()
        second.receive_json()
        first.send_json({"type": "user", "text": "shared"})
        first_events = _collect_turn(first)
        second_events = _collect_turn(second)
    assert [e["type"] for e in first_events] == [
        "user",
        "turn_start",
        "tool",
        "text",
        "done",
    ]
    assert [e["type"] for e in second_events] == [
        "user",
        "turn_start",
        "tool",
        "text",
        "done",
    ]
    assert second_events[3]["text"] == "echo: shared"


def test_permission_can_be_approved_from_another_client(client):
    with client.websocket_connect("/ws") as first, client.websocket_connect("/ws") as second:
        first.receive_json()
        second.receive_json()
        first.send_json({"type": "user", "text": "dangerous shared action"})
        for ws in (first, second):
            assert ws.receive_json()["type"] == "user"
            assert ws.receive_json()["type"] == "turn_start"
        first_confirm = first.receive_json()
        second_confirm = second.receive_json()
        assert first_confirm["id"] == second_confirm["id"]
        second.send_json(
            {
                "type": "confirm_response",
                "id": second_confirm["id"],
                "approved": True,
            }
        )
        first_events = _collect_turn(first)
    assert first_events[0]["text"] == "approved"


def test_status_and_memories_endpoints(client):
    status = client.get("/api/status").json()
    assert status["version"]
    memories = client.get("/api/memories").json()
    assert memories[0]["fact"] == "The demo is on Friday."


def test_history_survives_websocket_reconnect(client):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "user", "text": "remember this chat"})
        _collect_turn(ws)
    with client.websocket_connect("/ws") as ws:
        sync = ws.receive_json()
    assert [m["text"] for m in sync["messages"]] == [
        "remember this chat",
        "echo: remember this chat",
    ]
    assert client.get("/api/history").json()[0]["text"] == "remember this chat"


class FakeTranscriber:
    def transcribe(self, path):
        assert path.exists()
        return "spoken request"


class FakeSpeaker:
    sample_rate = 16_000

    def synthesize(self, text):
        yield b"\x00\x00\x01\x00"


def test_voice_turn_uses_same_agent_session(tmp_path):
    from friday.memory.history import ConversationStore

    config = FridayConfig(data_dir=tmp_path, autonomy_enabled=False)
    store = MemoryStore(config.db_path)
    history = ConversationStore(config.db_path)
    bridge = VoiceBridge(config, FakeTranscriber(), FakeSpeaker())
    app = create_app(
        config,
        agent_factory=lambda confirm: FakeAgent(confirm, store, history),
        voice_bridge_factory=lambda _config: bridge,
    )
    with TestClient(app) as test_client, test_client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json(
            {
                "type": "voice",
                "audio": base64.b64encode(b"fake webm").decode(),
                "format": "webm",
            }
        )
        events = _collect_turn(ws)
    types = [event["type"] for event in events]
    assert "transcript" in types
    assert "audio" in types
    assert next(e for e in events if e["type"] == "transcript")["text"] == "spoken request"


def test_index_serves_chat_panel(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "FRIDAY" in page.text
