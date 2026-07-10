from pathlib import Path

import pytest

from friday.config import FridayConfig
from friday.fs.permissions import PermissionGate, Tier, Verdict


@pytest.fixture
def gate(tmp_path: Path) -> PermissionGate:
    root = tmp_path / "granted"
    root.mkdir()
    (root / "secrets").mkdir()
    config = FridayConfig(granted_roots=[root])
    config.denied_paths.append(root / "secrets")
    return PermissionGate(config)


def root(gate: PermissionGate) -> Path:
    return gate.config.granted_roots[0]


def test_read_inside_granted_root_is_allowed(gate):
    d = gate.evaluate("Read", {"file_path": str(root(gate) / "notes.txt")})
    assert d.verdict is Verdict.ALLOW
    assert d.tier is Tier.READ


def test_write_inside_granted_root_is_allowed(gate):
    d = gate.evaluate("Write", {"file_path": str(root(gate) / "new.txt"), "content": "x"})
    assert d.verdict is Verdict.ALLOW
    assert d.tier is Tier.WRITE


def test_read_outside_granted_roots_requires_confirmation(gate, tmp_path):
    d = gate.evaluate("Read", {"file_path": str(tmp_path / "elsewhere.txt")})
    assert d.verdict is Verdict.CONFIRM


def test_denied_path_wins_even_inside_granted_root(gate):
    d = gate.evaluate("Read", {"file_path": str(root(gate) / "secrets" / "key.pem")})
    assert d.verdict is Verdict.DENY


def test_default_deny_list_includes_ssh():
    gate = PermissionGate(FridayConfig(granted_roots=[Path.home()]))
    d = gate.evaluate("Read", {"file_path": "~/.ssh/id_rsa"})
    assert d.verdict is Verdict.DENY


def test_bash_always_requires_confirmation(gate):
    d = gate.evaluate("Bash", {"command": "ls -la"})
    assert d.verdict is Verdict.CONFIRM
    assert d.tier is Tier.DANGEROUS


def test_bash_destructive_pattern_is_hard_denied(gate):
    d = gate.evaluate("Bash", {"command": "rm -rf /"})
    assert d.verdict is Verdict.DENY


def test_bash_touching_denied_path_is_denied(gate):
    d = gate.evaluate("Bash", {"command": f"cat {root(gate) / 'secrets' / 'key.pem'}"})
    assert d.verdict is Verdict.DENY


def test_unknown_tool_defaults_to_confirmation(gate):
    d = gate.evaluate("LaunchMissiles", {"target": "somewhere"})
    assert d.verdict is Verdict.CONFIRM
    assert d.tier is Tier.DANGEROUS


def test_glob_inside_root_allowed(gate):
    d = gate.evaluate("Glob", {"pattern": "**/*.md", "path": str(root(gate))})
    assert d.verdict is Verdict.ALLOW
