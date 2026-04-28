"""Tests for /enemy autocomplete + resolution helpers (bot/commands.py)."""
from __future__ import annotations

from pathlib import Path

import pytest

discord = pytest.importorskip("discord", reason="discord.py not installed")

from bot.commands import _autocomplete_enemies, _resolve_enemy_id  # noqa: E402
from db import repo  # noqa: E402


def _seed(conn, name: str, category: str = "Lvl 25", is_npc: bool = False) -> int:
    return repo.upsert_enemy(
        conn, canonical_name=name, category=category,
        region="Osterra", sheet_gid=1, source_row=3,
        name_color_hex=None, hyperlink_url=None, is_npc=is_npc,
    )


def test_autocomplete_returns_choices_with_enemy_id_value(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    eid = _seed(conn, "Sly Leader Lloris", "Solistia Lvl 25")
    results = _autocomplete_enemies(conn, "Sly")
    assert len(results) == 1
    assert results[0].value == str(eid)
    assert "Sly Leader Lloris" in results[0].name
    assert "Solistia Lvl 25" in results[0].name


def test_autocomplete_caps_at_25(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    for i in range(40):
        _seed(conn, f"Enemy{i:03d}")
    results = _autocomplete_enemies(conn, "")
    assert len(results) <= 25


def test_autocomplete_prefix_matches_first(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    _seed(conn, "Mini Lloris")          # substring match
    _seed(conn, "Lloris the Brave")     # prefix match
    results = _autocomplete_enemies(conn, "Lloris")
    names = [r.name for r in results]
    assert names[0].startswith("Lloris the Brave")


def test_autocomplete_truncates_long_labels(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    long_name = "A" * 90
    _seed(conn, long_name, category="Solistia Lvl 75")
    results = _autocomplete_enemies(conn, "A")
    assert len(results) == 1
    assert len(results[0].name) <= 100


def test_resolve_by_numeric_id(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    eid = _seed(conn, "Lloris")
    assert _resolve_enemy_id(conn, str(eid)) == eid


def test_resolve_by_exact_name_case_insensitive(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    eid = _seed(conn, "Sly Leader Lloris")
    assert _resolve_enemy_id(conn, "sly leader lloris") == eid


def test_resolve_by_prefix(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    eid = _seed(conn, "Sly Leader Lloris")
    assert _resolve_enemy_id(conn, "Sly") == eid


def test_resolve_returns_none_for_unknown(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    assert _resolve_enemy_id(conn, "no-such-enemy") is None


def test_resolve_returns_none_for_blank(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    assert _resolve_enemy_id(conn, "") is None
    assert _resolve_enemy_id(conn, "   ") is None
