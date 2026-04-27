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


def _seed_full_kit(conn) -> int:
    """Seed a Castti-shaped kit with actives + passive + divine + EX + 3-tier ultimate + latent."""
    ch_id = repo.upsert_character(
        conn, canonical_name="Castti", base_role="apothecary", base_weapon="axe",
    )
    form_id = repo.insert_form(
        conn, character_id=ch_id, display_name="Castti", rarity="5*",
        sheet_gid=999, source_row=5, name_color_hex="#CC0000",
        hyperlink_url="https://docs.google.com/spreadsheets/d/abc#gid=999&range=A5",
    )
    rows = []
    # 9 actives
    for i in range(1, 10):
        rows.append({
            "slot_order": i, "name": None, "sp_cost": 30 + i, "kind": "active",
            "learn_board": (i % 6) + 1 if i > 2 else None, "tier_level": None,
            "initial_use": None, "cooldown": None,
            "description": f"Active skill {i} description text",
            "power_min": None, "power_max": None, "hits": None,
        })
    # divine, EX
    rows.append({
        "slot_order": 10, "name": None, "sp_cost": 40, "kind": "divine",
        "learn_board": None, "tier_level": None, "initial_use": None, "cooldown": None,
        "description": "1x ST Axe (260-450 Power)",
        "power_min": 260, "power_max": 450, "hits": 1,
    })
    rows.append({
        "slot_order": 11, "name": None, "sp_cost": None, "kind": "ex",
        "learn_board": None, "tier_level": None, "initial_use": None, "cooldown": None,
        "description": "All allies +15% Atk Up for 5t",
        "power_min": None, "power_max": None, "hits": None,
    })
    # 3-tier ultimate
    for tl, hs in [(1, 50), (10, 100), (20, 150)]:
        rows.append({
            "slot_order": 11 + tl, "name": None, "sp_cost": None, "kind": "ultimate",
            "learn_board": None, "tier_level": tl, "initial_use": None, "cooldown": None,
            "description": f"All Allies Heal + Recover {hs} SP",
            "power_min": None, "power_max": None, "hits": None,
        })
    # passive, latent
    rows.append({
        "slot_order": 32, "name": None, "sp_cost": None, "kind": "passive",
        "learn_board": 1, "tier_level": None, "initial_use": None, "cooldown": None,
        "description": "After Frontrow ally Axe attack, perform 1x AoE Axe",
        "power_min": 220, "power_max": 220, "hits": 1,
    })
    rows.append({
        "slot_order": 33, "name": None, "sp_cost": None, "kind": "latent",
        "learn_board": None, "tier_level": None, "initial_use": 1, "cooldown": 5,
        "description": "Gain 'Every Drop Counts' for 1 turn",
        "power_min": None, "power_max": None, "hits": None,
    })
    repo.insert_skills(conn, form_id, rows)
    repo.insert_equipment(conn, form_id, [
        {"slot": None, "name": "Healer's Charm", "description": "+heal",
         "is_exclusive": False}
    ])
    repo.insert_affinities(conn, form_id, [
        ("weakness", "Fire", None), ("weapon", "Axe", None),
    ])
    repo.upsert_profile(
        conn, form_id,
        splash_art_url="https://example.com/castti.png",
        self_buffs_text="A travelling apothecary.",
    )
    return form_id


def test_form_to_embed_basic_shape(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    form_id = _seed(conn)
    built = embeds.form_to_embed(conn, form_id)
    conn.close()

    assert built is not None
    assert isinstance(built, list)
    assert len(built) >= 1
    header = built[0]
    assert "Cyrus" in header.title
    assert "★★★★★" in header.title
    # 5* color was seeded as #CC0000
    assert header.color is not None and header.color.value == 0xCC0000
    assert header.url is not None and header.url.startswith("https://")
    assert header.thumbnail.url == "https://example.com/cyrus.png"
    assert header.image.url == "https://example.com/cyrus.png"


def test_form_to_embed_has_skill_and_affinity_fields(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    form_id = _seed(conn)
    built = embeds.form_to_embed(conn, form_id)
    conn.close()

    # Header embed: affinities only.
    header_field_names = [f.name for f in built[0].fields]
    assert "Weakness" in header_field_names
    assert "Element" in header_field_names
    assert "Weapon" in header_field_names

    # Skills embed: actives in description (code-block table), latent as field.
    skills_embed = next(
        (e for e in built if e.title and e.title.startswith("Skills")), None,
    )
    assert skills_embed is not None
    assert skills_embed.description is not None
    assert "```" in skills_embed.description
    assert "Fireball" in skills_embed.description
    assert "Hellfire" in skills_embed.description

    skills_field_names = [f.name for f in skills_embed.fields]
    assert "Latent" in skills_field_names
    latent_field = next(f for f in skills_embed.fields if f.name == "Latent")
    assert "init 2t" in latent_field.value
    assert "cd 3t" in latent_field.value

    # Gear embed: A4 accessories + profile.
    gear_embed = built[-1]
    gear_field_names = [f.name for f in gear_embed.fields]
    assert "A4 Accessories" in gear_field_names
    assert "Profile" in gear_field_names
    eq_field = next(f for f in gear_embed.fields if f.name == "A4 Accessories")
    assert "Scholar's Tome" in eq_field.value
    assert "exclusive" in eq_field.value


def test_form_to_embed_returns_none_for_missing_form(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    built = embeds.form_to_embed(conn, form_id=99999)
    conn.close()
    assert built is None


def test_form_to_embed_fields_respect_discord_limits(tmp_db_path: Path) -> None:
    """Field values must stay <= 1024 chars, names <= 256, descriptions <= 4096."""
    conn = repo.connect(tmp_db_path)
    ch_id = repo.upsert_character(conn, canonical_name="LongDude",
                                   base_role="scholar", base_weapon="tome")
    form_id = repo.insert_form(
        conn, character_id=ch_id, display_name="LongDude", rarity="5*",
    )
    long_desc = "very long fire damage description " * 30
    repo.insert_skills(conn, form_id, [
        {"slot_order": i, "name": f"Skill{i}", "sp_cost": 10, "kind": "active",
         "learn_board": None, "tier_level": None,
         "initial_use": None, "cooldown": None,
         "description": long_desc,
         "power_min": None, "power_max": None, "hits": None}
        for i in range(1, 31)
    ])
    built = embeds.form_to_embed(conn, form_id)
    conn.close()
    assert built is not None
    total_chars = 0
    for e in built:
        for f in e.fields:
            assert len(f.value) <= embeds.FIELD_VALUE_LIMIT, \
                f"field {f.name!r} value too long: {len(f.value)}"
            assert len(f.name) <= embeds.FIELD_NAME_LIMIT
            total_chars += len(f.name) + len(f.value)
        if e.description:
            assert len(e.description) <= embeds.EMBED_DESCRIPTION_LIMIT
            total_chars += len(e.description)
        if e.title:
            total_chars += len(e.title)
    assert total_chars <= embeds.TOTAL_CHARS_PER_MESSAGE


def test_form_to_embed_three_embeds_for_full_kit(tmp_db_path: Path) -> None:
    """A character with skills + gear + profile should produce 3 embeds."""
    conn = repo.connect(tmp_db_path)
    form_id = _seed_full_kit(conn)
    built = embeds.form_to_embed(conn, form_id)
    conn.close()
    assert built is not None
    assert len(built) == 3


def test_gear_embed_omitted_when_empty(tmp_db_path: Path) -> None:
    """No gear and no self_buffs_text → no third embed; footer goes on skills embed."""
    conn = repo.connect(tmp_db_path)
    ch_id = repo.upsert_character(conn, canonical_name="Bare", base_role="r", base_weapon="w")
    form_id = repo.insert_form(
        conn, character_id=ch_id, display_name="Bare", rarity="3*",
    )
    repo.insert_skills(conn, form_id, [
        {"slot_order": 1, "name": None, "sp_cost": 10, "kind": "active",
         "learn_board": None, "tier_level": None,
         "initial_use": None, "cooldown": None,
         "description": "single attack",
         "power_min": None, "power_max": None, "hits": None},
    ])
    built = embeds.form_to_embed(conn, form_id)
    conn.close()
    assert built is not None
    # Header + skills only.
    titles = [e.title for e in built]
    # No gear embed (gear embed has no title in our builder).
    assert all(
        not (e.title is None and any(f.name in ("A4 Accessories", "Profile") for f in e.fields))
        for e in built
    )
    assert len(built) == 2


def test_artwork_set_on_first_embed_only(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    form_id = _seed_full_kit(conn)
    built = embeds.form_to_embed(conn, form_id)
    conn.close()
    assert built is not None
    # Embed 0: thumbnail + image.
    assert built[0].thumbnail.url == "https://example.com/castti.png"
    assert built[0].image.url == "https://example.com/castti.png"
    # Other embeds: no artwork attached.
    for e in built[1:]:
        assert e.thumbnail.url is None
        assert e.image.url is None


def test_skills_embed_uses_code_block(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    form_id = _seed_full_kit(conn)
    built = embeds.form_to_embed(conn, form_id)
    conn.close()
    assert built is not None
    skills_embed = next(e for e in built if e.title and e.title.startswith("Skills"))
    # Active table goes in description.
    assert skills_embed.description is not None
    assert skills_embed.description.startswith("```")
    assert "Description" in skills_embed.description.splitlines()[1]
    # Passive / Divine / EX live as fields, also code-blocked.
    field_names = [f.name for f in skills_embed.fields]
    for expected in ("Passive", "Divine (TP)", "EX"):
        assert expected in field_names
        f = next(x for x in skills_embed.fields if x.name == expected)
        assert "```" in f.value


def test_ultimate_levels_are_folded(tmp_db_path: Path) -> None:
    """Three ultimate rows (Lv1/Lv10/Lv20) collapse to one Ultimate field with three tier lines."""
    conn = repo.connect(tmp_db_path)
    form_id = _seed_full_kit(conn)
    built = embeds.form_to_embed(conn, form_id)
    conn.close()
    assert built is not None
    skills_embed = next(e for e in built if e.title and e.title.startswith("Skills"))
    ult = next((f for f in skills_embed.fields if f.name == "Ultimate"), None)
    assert ult is not None
    assert "Lv1" in ult.value
    assert "Lv10" in ult.value
    assert "Lv20" in ult.value


def test_collapse_ultimates_handles_solo_row() -> None:
    """A single ultimate row with no tier_level returns one untiered group."""
    class Row(dict):
        def __getitem__(self, k):
            return super().__getitem__(k)

    rows = [Row({"description": "Solo ult", "tier_level": None})]
    out = embeds._collapse_ultimates(rows)
    assert len(out) == 1
    assert out[0]["headline"] == "Solo ult"
    assert out[0]["tiers"] == [(None, "Solo ult")]


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
