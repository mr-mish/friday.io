from pathlib import Path

from friday.config import DEFAULT_DENIED, FridayConfig, load_config


def test_missing_config_yields_safe_defaults():
    config = load_config(None) if not Path("friday.toml").exists() else FridayConfig()
    assert config.granted_roots == []
    assert len(config.denied_paths) == len(DEFAULT_DENIED)


def test_load_from_toml(tmp_path: Path):
    cfg_file = tmp_path / "friday.toml"
    cfg_file.write_text(
        """
[filesystem]
granted_roots = ["~/Documents"]
denied_paths = ["~/Documents/private"]

[agent]
model = "claude-sonnet-5"
system_prompt_extra = "Call me boss."
"""
    )
    config = load_config(cfg_file)
    assert Path("~/Documents").expanduser() in config.granted_roots
    assert config.model == "claude-sonnet-5"
    assert config.system_prompt_extra == "Call me boss."
    # user denies extend the defaults, never replace them
    assert Path("~/Documents/private").expanduser() in config.denied_paths
    assert Path("~/.ssh").expanduser() in config.denied_paths


def test_audit_log_path_derives_from_data_dir(tmp_path: Path):
    config = FridayConfig(data_dir=tmp_path)
    assert config.audit_log_path == tmp_path / "audit.jsonl"


def test_verify_threshold_default_is_consistent(tmp_path: Path):
    # When the key is omitted, the loaded value must match the dataclass
    # default (and verify.SpeakerVerifier.DEFAULT_THRESHOLD), not a stray 0.75.
    cfg_file = tmp_path / "friday.toml"
    cfg_file.write_text("[voice]\nverify_speaker = true\n")
    loaded = load_config(cfg_file)
    assert loaded.verify_threshold == FridayConfig().verify_threshold


def test_verify_threshold_respects_explicit_value(tmp_path: Path):
    cfg_file = tmp_path / "friday.toml"
    cfg_file.write_text("[voice]\nverify_threshold = 0.8\n")
    assert load_config(cfg_file).verify_threshold == 0.8
