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
                        splash_art_url=None,
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
    for i in range(1, 10):
        rows.append({
            "slot_order": i, "name": None, "sp_cost": 30 + i, "kind": "active",
            "learn_board": (i % 6) + 1 if i > 2 else None, "tier_level": None,
            "initial_use": None, "cooldown": None,
            "description": f"Active skill {i} description text",
            "power_min": None, "power_max": None, "hits": None,
        })
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
    for tl, hs in [(1, 50), (10, 100), (20, 150)]:
        rows.append({
            "slot_order": 11 + tl, "name": None, "sp_cost": None, "kind": "ultimate",
            "learn_board": None, "tier_level": tl, "initial_use": None, "cooldown": None,
            "description": f"All Allies Heal + Recover {hs} SP",
            "power_min": None, "power_max": None, "hits": None,
        })
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
        splash_art_url=None,
        self_buffs_text="A travelling apothecary.",
    )
    return form_id


def test_section_keys_and_labels_are_consistent() -> None:
    assert embeds.SECTIONS == ("actives", "passives", "ultimate", "a4", "info")
    assert set(embeds.SECTION_LABELS.keys()) == set(embeds.SECTIONS)
    assert set(embeds.SECTION_DESCRIPTIONS.keys()) == set(embeds.SECTIONS)
    assert embeds.DEFAULT_SECTION == "actives"


def test_build_section_actives_basic_shape(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    form_id = _seed(conn)
    embed = embeds.build_section_embed(conn, form_id, "actives")
    conn.close()

    assert embed is not None
    assert "Cyrus" in embed.title
    assert "⭐⭐⭐⭐⭐" in embed.title
    assert embed.color is not None and embed.color.value == 0xCC0000
    assert embed.url is not None and embed.url.startswith("https://")
    # No artwork: the image-source code path was removed.
    assert embed.thumbnail.url is None
    assert embed.image.url is None

    field_names = [f.name for f in embed.fields]
    assert "Active" in field_names
    # Latent belongs to the Passives section, not Actives.
    assert "Latent" not in field_names
    active_field = next(f for f in embed.fields if f.name == "Active")
    assert "Fireball" in active_field.value
    assert "Hellfire" in active_field.value


def test_build_section_passives_includes_latent(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    form_id = _seed(conn)
    embed = embeds.build_section_embed(conn, form_id, "passives")
    conn.close()

    assert embed is not None
    field_names = [f.name for f in embed.fields]
    assert "Latent" in field_names
    latent_field = next(f for f in embed.fields if f.name == "Latent")
    assert "init 2t" in latent_field.value
    assert "cd 3t" in latent_field.value


def test_skill_line_has_no_slot_number_or_b_prefix(tmp_db_path: Path) -> None:
    """Skill bullets must not show a leading "N." index, and board markers
    must render as "1*" not "B1*"."""
    conn = repo.connect(tmp_db_path)
    form_id = _seed_full_kit(conn)
    embed = embeds.build_section_embed(conn, form_id, "actives")
    conn.close()
    active_field = next(f for f in embed.fields if f.name == "Active")
    # No "**N.**" leading index pattern.
    import re as _re
    assert not _re.search(r"\*\*\d+\.\*\*", active_field.value)
    # Board markers render without the "B" prefix.
    assert "`B1⭐`" not in active_field.value
    assert "`B2⭐`" not in active_field.value
    assert "`1⭐`" in active_field.value or "`2⭐`" in active_field.value


def test_build_section_a4_basic_shape(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    form_id = _seed(conn)
    embed = embeds.build_section_embed(conn, form_id, "a4")
    conn.close()

    assert embed is not None
    assert "Cyrus" in embed.title
    field_names = [f.name for f in embed.fields]
    assert "A4 Accessory" in field_names
    assert "Active" not in field_names
    a4_field = next(f for f in embed.fields if f.name == "A4 Accessory")
    assert "Scholar's Tome" in a4_field.value
    assert "exclusive" in a4_field.value


def test_build_section_a4_handles_no_equipment(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    ch = repo.upsert_character(conn, canonical_name="NoGear", base_role="r", base_weapon="w")
    form_id = repo.insert_form(conn, character_id=ch, display_name="NoGear", rarity="3*")
    embed = embeds.build_section_embed(conn, form_id, "a4")
    conn.close()
    a4_field = next(f for f in embed.fields if f.name == "A4 Accessory")
    assert "no a4 accessory" in a4_field.value.lower()


def test_build_section_info_basic_shape(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    form_id = _seed(conn)
    embed = embeds.build_section_embed(conn, form_id, "info")
    conn.close()

    assert embed is not None
    assert "Cyrus" in embed.title
    field_names = [f.name for f in embed.fields]
    assert "Weakness" in field_names
    assert "Element" in field_names
    assert "Weapon" in field_names
    assert "Profile" in field_names
    assert "Character Art" in field_names
    art_field = next(f for f in embed.fields if f.name == "Character Art")
    assert "spreadsheet" in art_field.value.lower()


def test_build_section_returns_none_for_missing_form(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    embed = embeds.build_section_embed(conn, form_id=99999, section="actives")
    conn.close()
    assert embed is None


def test_build_section_actives_respects_field_limits(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    ch = repo.upsert_character(conn, canonical_name="LongDude", base_role="r", base_weapon="w")
    form_id = repo.insert_form(conn, character_id=ch, display_name="LongDude", rarity="5*")
    long_desc = "very long fire damage description " * 30
    repo.insert_skills(conn, form_id, [
        {"slot_order": i, "name": f"Skill{i}", "sp_cost": 10, "kind": "active",
         "learn_board": None, "tier_level": None,
         "initial_use": None, "cooldown": None,
         "description": long_desc,
         "power_min": None, "power_max": None, "hits": None}
        for i in range(1, 31)
    ])
    embed = embeds.build_section_embed(conn, form_id, "actives")
    conn.close()
    for f in embed.fields:
        assert len(f.value) <= embeds.FIELD_VALUE_LIMIT
        assert len(f.name) <= embeds.FIELD_NAME_LIMIT


def test_build_section_ultimate_folds_tiers(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    form_id = _seed_full_kit(conn)
    embed = embeds.build_section_embed(conn, form_id, "ultimate")
    conn.close()
    ult = next(f for f in embed.fields if f.name == "Ultimate")
    assert "Lv1" in ult.value
    assert "Lv10" in ult.value
    assert "Lv20" in ult.value


def test_collapse_ultimates_handles_solo_row() -> None:
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


def test_safe_url_passes_full_urls() -> None:
    assert embeds._safe_url("https://example.com/x") == "https://example.com/x"
    assert embeds._safe_url("http://example.com/x") == "http://example.com/x"


def test_safe_url_prefixes_sheet_fragments() -> None:
    """The Sheets API returns in-doc anchors as fragments only (`#rangeid=…`).

    Every form in the live DB has this shape; Discord rejects the bare
    fragment, so we prefix it with the spreadsheet edit URL.
    """
    out = embeds._safe_url("#rangeid=1460640204")
    assert out is not None
    assert out.startswith("https://docs.google.com/spreadsheets/d/")
    assert out.endswith("#rangeid=1460640204")


def test_safe_url_rejects_garbage() -> None:
    assert embeds._safe_url(None) is None
    assert embeds._safe_url("") is None
    assert embeds._safe_url("not a url") is None
    assert embeds._safe_url("javascript:alert(1)") is None


def test_color_from_hex_handles_garbage() -> None:
    assert embeds._color_from_hex(None) is None
    assert embeds._color_from_hex("") is None
    assert embeds._color_from_hex("not a color") is None
    c = embeds._color_from_hex("#00FF00")
    assert c is not None and c.value == 0x00FF00


def test_rarity_prefix() -> None:
    assert embeds._rarity_prefix("5*") == "⭐⭐⭐⭐⭐"
    assert embeds._rarity_prefix("4*") == "⭐⭐⭐⭐"
    assert embeds._rarity_prefix("3*") == "⭐⭐⭐"
    assert embeds._rarity_prefix("free35") == "⭐⭐⭐→⭐⭐⭐⭐⭐"
    assert embeds._rarity_prefix(None) == ""
    assert embeds._rarity_prefix("???") == ""


def test_rarity_label() -> None:
    assert embeds._rarity_label("5*") == "5⭐"
    assert embeds._rarity_label("4*") == "4⭐"
    assert embeds._rarity_label("3*") == "3⭐"
    assert embeds._rarity_label("free35") == "3⭐→5⭐"
    assert embeds._rarity_label(None) == "?"


def test_header_description_has_no_unescaped_star(tmp_db_path: Path) -> None:
    """Regression: rarity in the description must not contain a bare ``*``
    (Discord parses ``*X*`` as italic, mangling the rarity readout)."""
    conn = repo.connect(tmp_db_path)
    form_id = _seed(conn)
    embed = embeds.build_section_embed(conn, form_id, "actives")
    conn.close()
    assert embed is not None and embed.description is not None
    assert "5*" not in embed.description
    assert "5⭐" in embed.description


def test_header_description_tags_sea_and_ex(tmp_db_path: Path) -> None:
    """SEA-only EX form should advertise both qualifiers in the description."""
    conn = repo.connect(tmp_db_path)
    ch = repo.upsert_character(conn, canonical_name="Lynette EX",
                                base_role="dancer", base_weapon="fan")
    form_id = repo.insert_form(
        conn, character_id=ch, display_name="Lynette EX", rarity="5*",
        variant_kind="ex", server="sea",
    )
    embed = embeds.build_section_embed(conn, form_id, "actives")
    conn.close()
    assert embed is not None and embed.description is not None
    assert "EX form" in embed.description
    assert "SEA only" in embed.description
    assert "Dancer" in embed.description
    assert "Fan" in embed.description
