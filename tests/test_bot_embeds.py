"""Embed-builder tests: pure functions over a seeded SQLite, no Discord runtime."""
from __future__ import annotations

from pathlib import Path

import pytest

from db import repo

discord = pytest.importorskip(
    "discord", reason="discord.py not installed (run conda env update -f environment.yml --prune)"
)

# Import after the importorskip so the whole module is skipped on machines
# that haven't installed discord.py yet.
from bot import embeds  # noqa: E402


def _seed(conn) -> int:
    ch_id = repo.upsert_character(conn, canonical_name="Cyrus",
                                   base_role="scholar", base_weapon="tome")
    form_id = repo.insert_form(
        conn, character_id=ch_id, display_name="Cyrus", rarity="5*",
        sheet_gid=519845584, source_row=10, name_color_hex="#CC0000",
        hyperlink_url="https://docs.google.com/spreadsheets/d/abc#gid=519845584&range=B5",
    )
    repo.insert_skills(conn, form_id, [
        {"slot_order": 1, "name": "Fireball", "sp_cost": 18, "kind": "active",
         "learn_board": None, "tier_level": None,
         "initial_use": None, "cooldown": None,
         "description": "1x single-target Fire (1x 200 Power)",
         "power_min": 200, "power_max": 200, "hits": 1},
        {"slot_order": 2, "name": "Hellfire", "sp_cost": 30, "kind": "active",
         "learn_board": 2, "tier_level": None,
         "initial_use": None, "cooldown": None,
         "description": "AoE Fire damage",
         "power_min": None, "power_max": None, "hits": None},
        {"slot_order": 99, "name": "Latent Power", "sp_cost": None, "kind": "latent",
         "learn_board": None, "tier_level": None,
         "initial_use": 2, "cooldown": 3,
         "description": "Boosts fire damage", "power_min": None, "power_max": None,
         "hits": None},
    ])
    repo.insert_equipment(conn, form_id, [
        {"slot": None, "name": "Scholar's Tome", "description": "+atk",
         "is_exclusive": True}
    ])
    repo.insert_affinities(conn, form_id, [
        ("weakness", "Wind", None),
        ("element", "Fire", None),
        ("weapon", "Tome", None),
    ])
    repo.upsert_profile(conn, form_id,
                        splash_art_url="https://example.com/cyrus.png",
                        self_buffs_text="A scholar who studies fire magic.")
    return form_id


def test_form_to_embed_basic_shape(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    form_id = _seed(conn)
    embed = embeds.form_to_embed(conn, form_id)
    conn.close()

    assert embed is not None
    assert "Cyrus" in embed.title
    assert "★★★★★" in embed.title
    # 5* color was seeded as #CC0000 = 13369344
    assert embed.color is not None and embed.color.value == 0xCC0000
    assert embed.url is not None and embed.url.startswith("https://")
    assert embed.thumbnail.url == "https://example.com/cyrus.png"


def test_form_to_embed_has_skill_and_affinity_fields(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    form_id = _seed(conn)
    embed = embeds.form_to_embed(conn, form_id)
    conn.close()

    field_names = [f.name for f in embed.fields]
    assert "Weakness" in field_names
    assert "Element" in field_names
    assert "Weapon" in field_names
    assert "Active" in field_names
    assert "Latent" in field_names
    assert "A4 Accessories" in field_names
    assert "Profile" in field_names

    active_field = next(f for f in embed.fields if f.name == "Active")
    assert "Fireball" in active_field.value
    assert "Hellfire" in active_field.value

    latent_field = next(f for f in embed.fields if f.name == "Latent")
    # latent prefix must include initial-use and cooldown turns
    assert "init 2t" in latent_field.value
    assert "cd 3t" in latent_field.value

    eq_field = next(f for f in embed.fields if f.name == "A4 Accessories")
    assert "Scholar's Tome" in eq_field.value
    assert "exclusive" in eq_field.value


def test_form_to_embed_returns_none_for_missing_form(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    embed = embeds.form_to_embed(conn, form_id=99999)
    conn.close()
    assert embed is None


def test_form_to_embed_fields_respect_discord_limits(tmp_db_path: Path) -> None:
    """Field values must stay <= 1024 chars, names <= 256."""
    conn = repo.connect(tmp_db_path)
    ch_id = repo.upsert_character(conn, canonical_name="LongDude",
                                   base_role="scholar", base_weapon="tome")
    form_id = repo.insert_form(
        conn, character_id=ch_id, display_name="LongDude", rarity="5*",
    )
    # 30 skills with very long descriptions; aggregate would blow past 1024 chars.
    long_desc = "very long fire damage description " * 30
    repo.insert_skills(conn, form_id, [
        {"slot_order": i, "name": f"Skill{i}", "sp_cost": 10, "kind": "active",
         "learn_board": None, "tier_level": None,
         "initial_use": None, "cooldown": None,
         "description": long_desc,
         "power_min": None, "power_max": None, "hits": None}
        for i in range(1, 31)
    ])
    embed = embeds.form_to_embed(conn, form_id)
    conn.close()
    for f in embed.fields:
        assert len(f.value) <= embeds.FIELD_VALUE_LIMIT, \
            f"field {f.name!r} value too long: {len(f.value)}"
        assert len(f.name) <= embeds.FIELD_NAME_LIMIT


def test_search_results_to_embed_truncates_to_top_10(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    for i in range(20):
        ch = repo.upsert_character(conn, canonical_name=f"C{i}",
                                    base_role="warrior", base_weapon="sword")
        repo.insert_form(conn, character_id=ch, display_name=f"C{i}", rarity="5*")
    repo.rebuild_fts(conn)
    rows = repo.search_forms(conn, roles=["warrior"])
    embed = embeds.search_results_to_embed(rows, query_summary="role=warrior")
    conn.close()

    assert len(rows) == 20
    top = next(f for f in embed.fields if f.name == "Top results")
    # Top field shows up to 10 lines.
    assert top.value.count("\n") <= 9
    assert embed.footer.text is not None and "20" in embed.footer.text


def test_search_results_to_embed_empty(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    embed = embeds.search_results_to_embed([], query_summary="role=ghost")
    conn.close()
    assert any(f.name == "No matches" for f in embed.fields)


def test_color_from_hex_handles_garbage() -> None:
    assert embeds._color_from_hex(None) is None
    assert embeds._color_from_hex("") is None
    assert embeds._color_from_hex("not a color") is None
    c = embeds._color_from_hex("#00FF00")
    assert c is not None and c.value == 0x00FF00


def test_rarity_prefix() -> None:
    assert embeds._rarity_prefix("5*") == "★★★★★"
    assert embeds._rarity_prefix("4*") == "★★★★"
    assert embeds._rarity_prefix("3*") == "★★★"
    assert embeds._rarity_prefix("free35") == "★★★→★★★★★"
    assert embeds._rarity_prefix(None) == ""
    assert embeds._rarity_prefix("???") == ""
