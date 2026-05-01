"""Tests for bot/pet_embeds.py — pure embed builder for /pet."""
from __future__ import annotations

from pathlib import Path

import pytest

discord = pytest.importorskip("discord", reason="discord.py not installed")

from bot import pet_embeds  # noqa: E402
from db import repo  # noqa: E402


def _seed(conn, **overrides) -> int:
    pet = {
        "canonical_name":  "Red Brown Cat",
        "display_name_jp": "赤茶 (Red Brown Cat)",
        "source_text":     "Quest\n\nBeat the Titan Tower F3",
        "ability_text":    "Grant owner 30% Patk/Matk Up 1T",
        "max_boost":       None,
        "prep_base":       13, "prep_lv10": 12,
        "cooldown_base":   13, "cooldown_lv5": 12,
        "hp": 300, "sp": 11, "patk": 23, "pdef": 20,
        "matk": 24, "mdef": 22, "crit": 15, "speed": 15,
        "sheet_gid": 243040141, "source_row": 25,
        "name_color_hex": None,
        "hyperlink_url": "#gid=243040141&range=A26",
    }
    pet.update(overrides)
    return repo.upsert_pet(conn, pet)


def test_build_pet_embed_full(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    pid = _seed(conn)

    embed = pet_embeds.build_pet_embed(conn, pid)
    assert embed is not None
    assert embed.title == "Red Brown Cat"
    assert "Patk/Matk Up 1T" in (embed.description or "")
    # JP name appended as a tagline since it differs from canonical.
    assert "赤茶" in (embed.description or "")
    assert embed.url and embed.url.startswith("https://docs.google.com")
    assert embed.url.endswith("#gid=243040141&range=A26")

    by_name = {f.name: f.value for f in embed.fields}
    assert by_name["Max Boost"] == "—"
    assert by_name["Turn Preparation"] == "13 (Lv10: 12)"
    assert by_name["Turn Cooldown"] == "13 (Lv5: 12)"
    assert "HP" in by_name["Stats"] and "300" in by_name["Stats"]
    assert "Crit" in by_name["Stats"] and "15" in by_name["Stats"]
    assert "Beat the Titan Tower F3" in by_name["How to obtain"]
    conn.close()


def test_build_pet_embed_missing_max_boost_renders_dash(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    pid = _seed(conn, max_boost=None)
    embed = pet_embeds.build_pet_embed(conn, pid)
    assert embed is not None
    by_name = {f.name: f.value for f in embed.fields}
    assert by_name["Max Boost"] == "—"
    conn.close()


def test_build_pet_embed_with_max_boost(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    pid = _seed(conn, max_boost="Lv2")
    embed = pet_embeds.build_pet_embed(conn, pid)
    assert embed is not None
    by_name = {f.name: f.value for f in embed.fields}
    assert by_name["Max Boost"] == "Lv2"
    conn.close()


def test_build_pet_embed_missing_lv_modifiers_renders_base_only(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    pid = _seed(conn, prep_lv10=None, cooldown_lv5=None)
    embed = pet_embeds.build_pet_embed(conn, pid)
    assert embed is not None
    by_name = {f.name: f.value for f in embed.fields}
    assert by_name["Turn Preparation"] == "13"
    assert by_name["Turn Cooldown"] == "13"
    conn.close()


def test_build_pet_embed_missing_prep_renders_dash(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    pid = _seed(conn, prep_base=None, prep_lv10=None,
                cooldown_base=None, cooldown_lv5=None)
    embed = pet_embeds.build_pet_embed(conn, pid)
    assert embed is not None
    by_name = {f.name: f.value for f in embed.fields}
    assert by_name["Turn Preparation"] == "—"
    assert by_name["Turn Cooldown"] == "—"
    conn.close()


def test_build_pet_embed_missing_stats_render_dash(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    pid = _seed(conn, hp=None, crit=None)
    embed = pet_embeds.build_pet_embed(conn, pid)
    assert embed is not None
    stats = next(f.value for f in embed.fields if f.name == "Stats")
    assert "HP" in stats and "—" in stats
    conn.close()


def test_build_pet_embed_returns_none_for_missing_pet(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    assert pet_embeds.build_pet_embed(conn, pet_id=99999) is None
    conn.close()


def test_build_pet_embed_omits_jp_tag_when_same_as_canonical(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    pid = _seed(conn, display_name_jp="Red Brown Cat")
    embed = pet_embeds.build_pet_embed(conn, pid)
    assert embed is not None
    assert "Sheet name" not in (embed.description or "")
    conn.close()
