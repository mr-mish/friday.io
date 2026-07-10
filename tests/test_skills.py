from pathlib import Path

import pytest

from friday.agent.core import _skill_servers
from friday.config import FridayConfig, SkillConfig, TaskConfig, load_config
from friday.fs.permissions import PermissionGate, Verdict


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "friday.toml"
    path.write_text(body)
    return path


def test_skills_and_tasks_parse(tmp_path: Path):
    config = load_config(
        _write(
            tmp_path,
            """
[agent]
allow_web = true

[skills.weather]
command = "uvx"
args = ["mcp-weather"]
trust = "allow"

[skills.notion]
url = "https://mcp.example.com/mcp"

[tasks.weekly]
prompt = "Summarize my week."
description = "Friday review"
""",
        )
    )
    assert config.allow_web is True
    assert config.skills["weather"].trust == "allow"
    assert config.skills["notion"].trust == "confirm"  # default
    assert config.tasks["weekly"].prompt == "Summarize my week."


def test_invalid_trust_rejected(tmp_path: Path):
    with pytest.raises(ValueError, match="trust"):
        load_config(_write(tmp_path, '[skills.x]\ncommand = "x"\ntrust = "yolo"\n'))


def test_trusted_skill_tools_auto_allowed():
    config = FridayConfig(skills={"weather": SkillConfig(command="uvx", trust="allow")})
    gate = PermissionGate(config)
    assert gate.evaluate("mcp__weather__forecast", {}).verdict is Verdict.ALLOW


def test_untrusted_skill_tools_confirm():
    config = FridayConfig(skills={"notion": SkillConfig(url="https://x", trust="confirm")})
    gate = PermissionGate(config)
    assert gate.evaluate("mcp__notion__create_page", {}).verdict is Verdict.CONFIRM
    # tools from servers not in config at all also confirm
    assert gate.evaluate("mcp__mystery__anything", {}).verdict is Verdict.CONFIRM


def test_web_tools_confirm_by_default_allow_when_enabled():
    gate = PermissionGate(FridayConfig())
    assert gate.evaluate("WebSearch", {"query": "x"}).verdict is Verdict.CONFIRM
    gate = PermissionGate(FridayConfig(allow_web=True))
    assert gate.evaluate("WebSearch", {"query": "x"}).verdict is Verdict.ALLOW
    assert gate.evaluate("WebFetch", {"url": "https://x"}).verdict is Verdict.ALLOW


def test_skill_servers_built_from_config():
    config = FridayConfig(
        skills={
            "weather": SkillConfig(command="uvx", args=["mcp-weather"], env={"K": "v"}),
            "notion": SkillConfig(url="https://mcp.example.com/mcp"),
            "memory": SkillConfig(command="evil"),  # reserved name must be ignored
        }
    )
    servers = _skill_servers(config)
    assert servers["weather"] == {
        "type": "stdio",
        "command": "uvx",
        "args": ["mcp-weather"],
        "env": {"K": "v"},
    }
    assert servers["notion"] == {"type": "http", "url": "https://mcp.example.com/mcp"}
    assert "memory" not in servers


def test_task_config_defaults():
    task = TaskConfig(prompt="p")
    assert task.description == ""
