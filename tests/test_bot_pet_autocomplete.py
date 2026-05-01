"""Tests for the /pet autocomplete and free-text resolution helpers."""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("discord", reason="discord.py not installed")

from bot.commands import _autocomplete_pets, _resolve_pet_id  # noqa: E402
from db import repo  # noqa: E402


def _seed_two_white_rabbits(conn) -> tuple[int, int]:
    quest = repo.upsert_pet(conn, {
        "canonical_name": "White Rabbit",
        "display_name_jp": "ウサギ 白 (White Rabbit)",
        "source_text": "Quest", "ability_text": "AoE Taunt",
        "max_boost": "Lv2",
        "prep_base": 1, "prep_lv10": 0,
        "cooldown_base": 9, "cooldown_lv5": 8,
        "hp": 140, "sp": 21, "patk": 5, "pdef": 12,
        "matk": 29, "mdef": 22, "crit": 19, "speed": 38,
        "sheet_gid": 243040141, "source_row": 77,
        "name_color_hex": None, "hyperlink_url": "#gid=243040141&range=A78",
    })
    login = repo.upsert_pet(conn, {
        "canonical_name": "White Rabbit",
        "display_name_jp": "白 (White Rabbit)",
        "source_text": "New Year 2023 Login (JP)\n(Until Jan 31st 2023)",
        "ability_text": "Grant owner Evade Magic",
        "max_boost": "Lv2",
        "prep_base": 2, "prep_lv10": 1,
        "cooldown_base": 5, "cooldown_lv5": 4,
        "hp": 120, "sp": 22, "patk": 13, "pdef": 11,
        "matk": 13, "mdef": 11, "crit": 28, "speed": 50,
        "sheet_gid": 243040141, "source_row": 189,
        "name_color_hex": None, "hyperlink_url": "#gid=243040141&range=A190",
    })
    return quest, login


def _seed_red_brown_cat(conn) -> int:
    return repo.upsert_pet(conn, {
        "canonical_name": "Red Brown Cat",
        "display_name_jp": "赤茶 (Red Brown Cat)",
        "source_text": "Quest", "ability_text": "Grant 30%",
        "max_boost": None,
        "prep_base": 13, "prep_lv10": 12,
        "cooldown_base": 13, "cooldown_lv5": 12,
        "hp": 300, "sp": 11, "patk": 23, "pdef": 20,
        "matk": 24, "mdef": 22, "crit": 15, "speed": 15,
        "sheet_gid": 243040141, "source_row": 25,
        "name_color_hex": None, "hyperlink_url": "#gid=243040141&range=A26",
    })


def test_autocomplete_prefix_first(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    _seed_red_brown_cat(conn)
    repo.upsert_pet(conn, {
        "canonical_name": "Black Cat",
        "display_name_jp": "黒 (Black Cat)",
        "source_text": "Quest", "ability_text": "Evade",
        "max_boost": "Lv2",
        "prep_base": 2, "prep_lv10": 1, "cooldown_base": 5, "cooldown_lv5": 4,
        "hp": 140, "sp": 21, "patk": 20, "pdef": 13,
        "matk": 15, "mdef": 15, "crit": 30, "speed": 32,
        "sheet_gid": 243040141, "source_row": 17,
        "name_color_hex": None, "hyperlink_url": None,
    })

    choices = _autocomplete_pets(conn, "red")
    assert choices[0].name == "Red Brown Cat"
    assert choices[0].value.isdigit()
    conn.close()


def test_autocomplete_disambiguates_duplicate_names(tmp_db_path: Path) -> None:
    """Both White Rabbits → both choices include a source-text hint."""
    conn = repo.connect(tmp_db_path)
    _seed_two_white_rabbits(conn)
    choices = _autocomplete_pets(conn, "white")
    assert len(choices) == 2
    labels = [c.name for c in choices]
    assert all(label.startswith("White Rabbit — ") for label in labels)
    # First-line of each pet's source_text appears as the hint.
    assert any("Quest" in label for label in labels)
    assert any("Login" in label for label in labels)
    # Both choices must be distinct (i.e. they don't collapse to one).
    assert len(set(c.value for c in choices)) == 2
    conn.close()


def test_autocomplete_omits_hint_when_unique(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    _seed_red_brown_cat(conn)
    choices = _autocomplete_pets(conn, "red")
    # Single-match → label is just the canonical name.
    assert choices[0].name == "Red Brown Cat"
    conn.close()


def test_resolve_pet_id_by_id_string(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    pid = _seed_red_brown_cat(conn)
    assert _resolve_pet_id(conn, str(pid)) == pid
    conn.close()


def test_resolve_pet_id_exact_then_prefix(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    pid = _seed_red_brown_cat(conn)
    assert _resolve_pet_id(conn, "Red Brown Cat") == pid
    assert _resolve_pet_id(conn, "red brown") == pid  # prefix
    assert _resolve_pet_id(conn, "no such pet") is None
    conn.close()


def test_resolve_pet_id_empty(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    _seed_red_brown_cat(conn)
    assert _resolve_pet_id(conn, "") is None
    assert _resolve_pet_id(conn, "   ") is None
    conn.close()
