from pathlib import Path

from friday.agent.core import _system_prompt
from friday.config import FridayConfig
from friday.memory.history import ConversationStore
from friday.memory.store import MemoryStore


def test_append_recent_and_search(tmp_path: Path):
    store = ConversationStore(tmp_path / "friday.db")
    store.append("user", "Plan the Atlas launch", modality="voice")
    store.append("assistant", "The Atlas launch is scheduled for Tuesday.")

    recent = store.recent()
    assert [m.role for m in recent] == ["user", "assistant"]
    assert recent[0].modality == "voice"
    assert store.search("Atlas")[0].content == "Plan the Atlas launch"


def test_history_persists_across_store_instances(tmp_path: Path):
    path = tmp_path / "friday.db"
    ConversationStore(path).append("user", "Persistent context")
    reopened = ConversationStore(path)
    assert reopened.recent()[0].content == "Persistent context"


def test_history_is_separate_by_conversation(tmp_path: Path):
    store = ConversationStore(tmp_path / "friday.db")
    store.append("user", "main thread")
    store.append("user", "other thread", conversation="other")
    assert [m.content for m in store.recent()] == ["main thread"]
    assert [m.content for m in store.recent(conversation="other")] == ["other thread"]


def test_clear_only_removes_selected_conversation(tmp_path: Path):
    store = ConversationStore(tmp_path / "friday.db")
    store.append("user", "remove me")
    store.append("user", "keep me", conversation="other")
    assert store.clear() == 1
    assert store.recent() == []
    assert store.recent(conversation="other")[0].content == "keep me"


def test_recent_history_is_restored_into_new_agent_prompt(tmp_path: Path):
    db_path = tmp_path / "friday.db"
    history = ConversationStore(db_path)
    history.append("user", "Call the launch Project Aurora")
    history.append("assistant", "I will use Project Aurora.")
    prompt = _system_prompt(
        FridayConfig(data_dir=tmp_path), MemoryStore(db_path), history
    )
    assert "Recent conversation transcript" in prompt
    assert "user: Call the launch Project Aurora" in prompt
    assert "assistant: I will use Project Aurora." in prompt
