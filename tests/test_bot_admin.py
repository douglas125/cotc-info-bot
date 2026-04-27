"""Admin-gating tests for /refresh."""
from __future__ import annotations

import config


def test_parse_admin_ids_basic() -> None:
    assert config.parse_admin_ids("123,456") == {123, 456}


def test_parse_admin_ids_handles_whitespace_and_blanks() -> None:
    assert config.parse_admin_ids("  123 ,, 456,  ") == {123, 456}


def test_parse_admin_ids_drops_invalid_pieces() -> None:
    assert config.parse_admin_ids("123,not_an_id,456") == {123, 456}


def test_parse_admin_ids_empty_or_none() -> None:
    assert config.parse_admin_ids(None) == set()
    assert config.parse_admin_ids("") == set()
    assert config.parse_admin_ids(",,") == set()


def test_get_setting_prefers_env_over_toml(monkeypatch, tmp_path) -> None:
    # Point USER_CONFIG_PATH at a tmp file that has a value the env should win against.
    fake_toml = tmp_path / "config.toml"
    fake_toml.write_text('discord_token = "from_toml"\n', encoding="utf-8")
    monkeypatch.setattr(config, "USER_CONFIG_PATH", fake_toml)

    monkeypatch.setenv("DISCORD_BOT_TOKEN", "from_env")
    assert config.get_setting("DISCORD_BOT_TOKEN", "discord_token") == "from_env"

    monkeypatch.delenv("DISCORD_BOT_TOKEN")
    assert config.get_setting("DISCORD_BOT_TOKEN", "discord_token") == "from_toml"


def test_get_setting_falls_back_to_default(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(config, "USER_CONFIG_PATH", tmp_path / "missing.toml")
    monkeypatch.delenv("SOME_VAR", raising=False)
    assert config.get_setting("SOME_VAR", "some_key") is None
    assert config.get_setting("SOME_VAR", "some_key", default="dflt") == "dflt"
