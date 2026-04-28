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
    assert embed.image.url == f"attachment://character_{form_id}_actives.png"
    assert not embed.fields


def test_build_character_message_includes_rendered_attachment(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    form_id = _seed(conn)
    message = embeds.build_character_message(conn, form_id, "actives")
    conn.close()

    assert message is not None
    assert message.file is not None
    assert message.file.filename == f"character_{form_id}_actives.png"
    assert message.embed.image.url == f"attachment://character_{form_id}_actives.png"


def test_build_section_passives_includes_latent(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    form_id = _seed(conn)
    embed = embeds.build_section_embed(conn, form_id, "passives")
    skills = repo.get_skills(conn, form_id)
    conn.close()

    assert embed is not None
    assert embed.image.url == f"attachment://character_{form_id}_passives.png"
    sections = embeds._plain_skill_sections(
        skills,
        embeds.PASSIVE_KIND_ORDER,
        "Passive Skills",
        "No passive skills recorded for this form.",
    )
    latent = next(section for section in sections if section.title == "Latent")
    assert any("init 2t" in line and "cd 3t" in line for line in latent.lines)


def test_skill_line_has_no_slot_number_or_b_prefix(tmp_db_path: Path) -> None:
    """Skill bullets must not show a leading "N." index, and board markers
    must render as "1*" not "B1*"."""
    conn = repo.connect(tmp_db_path)
    form_id = _seed_full_kit(conn)
    skills = repo.get_skills(conn, form_id)
    conn.close()
    active_lines = [
        embeds._plain_skill_line(row)
        for row in skills
        if (row["kind"] or "") == "active"
    ]
    active_text = "\n".join(active_lines)
    # No "**N.**" leading index pattern.
    import re as _re
    assert not _re.search(r"\*\*\d+\.\*\*", active_text)
    # Board markers render without the "B" prefix.
    assert "B1*" not in active_text
    assert "B2*" not in active_text
    assert "1*" in active_text or "2*" in active_text


def test_build_section_a4_basic_shape(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    form_id = _seed(conn)
    embed = embeds.build_section_embed(conn, form_id, "a4")
    conn.close()

    assert embed is not None
    assert "Cyrus" in embed.title
    assert embed.image.url == f"attachment://character_{form_id}_a4.png"
    assert not embed.fields


def test_build_section_a4_handles_no_equipment(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    ch = repo.upsert_character(conn, canonical_name="NoGear", base_role="r", base_weapon="w")
    form_id = repo.insert_form(conn, character_id=ch, display_name="NoGear", rarity="3*")
    embed = embeds.build_section_embed(conn, form_id, "a4")
    conn.close()
    assert embed is not None
    assert embed.image.url == f"attachment://character_{form_id}_a4.png"


def test_build_section_info_basic_shape(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    form_id = _seed(conn)
    embed = embeds.build_section_embed(conn, form_id, "info")
    conn.close()

    assert embed is not None
    assert "Cyrus" in embed.title
    assert embed.image.url == f"attachment://character_{form_id}_info.png"
    assert not embed.fields


def test_build_section_returns_none_for_missing_form(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    embed = embeds.build_section_embed(conn, form_id=99999, section="actives")
    conn.close()
    assert embed is None


def test_build_section_actives_renders_long_skill_attachment(tmp_db_path: Path) -> None:
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
    message = embeds.build_character_message(conn, form_id, "actives")
    conn.close()
    assert message is not None
    assert message.file is not None
    assert message.file.filename == f"character_{form_id}_actives.png"


def test_build_section_ultimate_folds_tiers(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    form_id = _seed_full_kit(conn)
    rows = [s for s in repo.get_skills(conn, form_id) if (s["kind"] or "") == "ultimate"]
    conn.close()
    lines = "\n".join(embeds._plain_ultimate_lines(rows))
    assert "Lv1" in lines
    assert "Lv10" in lines
    assert "Lv20" in lines


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
    form = repo.get_form(conn, form_id)
    conn.close()
    header = "\n".join(embeds._character_header_lines(form))
    assert "5*" not in header
    assert "5⭐" in header


def test_header_description_tags_sea_and_ex(tmp_db_path: Path) -> None:
    """SEA-only EX form should advertise both qualifiers in the description."""
    conn = repo.connect(tmp_db_path)
    ch = repo.upsert_character(conn, canonical_name="Lynette EX",
                                base_role="thief", base_weapon="dagger")
    form_id = repo.insert_form(
        conn, character_id=ch, display_name="Lynette EX", rarity="5*",
        variant_kind="ex", server="sea",
    )
    form = repo.get_form(conn, form_id)
    conn.close()
    header = "\n".join(embeds._character_header_lines(form))
    assert "EX form" in header
    assert "SEA only" in header
    assert "Thief" in header
    assert "Dagger" in header


def test_feedback_results_embed(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    repo.insert_feedback(conn, user_id=1, username="alice", guild_id=None,
                         feedback_text="kit description wrong on Castti")
    rows = repo.list_feedback(conn)
    conn.close()
    embed = embeds.feedback_results_to_embed(rows)
    assert "1 feedback submission" in embed.title
    assert any("alice" in f.name for f in embed.fields)
    assert any("Castti" in f.value for f in embed.fields)


def test_feedback_results_embed_truncates_long_body(tmp_db_path: Path) -> None:
    """The 2000-char body must be trimmed to Discord's per-field 1024 cap."""
    conn = repo.connect(tmp_db_path)
    repo.insert_feedback(conn, user_id=1, username="alice", guild_id=None,
                         feedback_text="x" * 2000)
    rows = repo.list_feedback(conn)
    conn.close()
    embed = embeds.feedback_results_to_embed(rows)
    assert len(embed.fields[0].value) <= embeds.FIELD_VALUE_LIMIT
