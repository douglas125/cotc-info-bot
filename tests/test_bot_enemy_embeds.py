"""Tests for bot/enemy_embeds.py — pure embed builders."""
from __future__ import annotations

from pathlib import Path

import pytest

discord = pytest.importorskip("discord", reason="discord.py not installed")

from bot import enemy_embeds  # noqa: E402
from db import repo  # noqa: E402


def _seed_lloris(conn, *, with_ex3: bool = True, with_default_npc: bool = False) -> int:
    enemy_id = repo.upsert_enemy(
        conn, canonical_name="Sly Leader Lloris",
        category="Solistia Lvl 25", region="Solistia",
        sheet_gid=795720982, source_row=3,
        name_color_hex="#ffffff",
        hyperlink_url="#gid=795720982&range=B4",
        is_npc=with_default_npc,
    )
    if with_ex3:
        f = repo.insert_enemy_form(
            conn, enemy_id=enemy_id, rank="EX3", rank_order=6,
        )
        repo.insert_enemy_member_stats(conn, f, [
            {"position": 0, "member_name": "Leader Lloris",
             "stat_name": "HP", "stat_value": "1,143,210"},
            {"position": 0, "member_name": "Leader Lloris",
             "stat_name": "Shields", "stat_value": "30"},
            {"position": 0, "member_name": "Leader Lloris",
             "stat_name": "P. Atk", "stat_value": "1,752"},
            {"position": 1, "member_name": "Mini Lloris",
             "stat_name": "HP", "stat_value": "822,762"},
            {"position": 1, "member_name": "Mini Lloris",
             "stat_name": "Shields", "stat_value": "18"},
            {"position": 1, "member_name": "Mini Lloris",
             "stat_name": "P. Atk", "stat_value": "1,344"},
        ])
    if with_default_npc:
        f = repo.insert_enemy_form(
            conn, enemy_id=enemy_id, rank="Default", rank_order=0,
        )
        repo.insert_enemy_member_stats(conn, f, [
            {"position": 0, "member_name": "Leader Lloris",
             "stat_name": "HP", "stat_value": "1000"},
        ])
    return enemy_id


def test_build_enemy_embed_renders_per_member_stat_fields(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    enemy_id = _seed_lloris(conn)
    embed = enemy_embeds.build_enemy_embed(conn, enemy_id, "EX3")
    assert embed is not None
    assert embed.title is not None
    assert "Sly Leader Lloris" in embed.title
    assert "EX 3" in embed.title
    # Description shows category + region.
    assert "Solistia" in (embed.description or "")
    fields = {f.name: f for f in embed.fields}
    assert "Leader Lloris" in fields
    assert "Mini Lloris" in fields
    assert "1,143,210" in fields["Leader Lloris"].value
    assert "822,762" in fields["Mini Lloris"].value
    # Multi-member encounters render inline so Discord packs them in rows.
    assert fields["Leader Lloris"].inline is True
    assert fields["Mini Lloris"].inline is True
    assert "Weaknesses" not in fields
    assert "Stats" not in fields
    assert embed.image.url is None


def test_build_enemy_embed_renders_weakness_labels(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    enemy_id = _seed_lloris(conn)
    # Seed weaknesses on the form too.
    form = repo.get_enemy_form_by_rank(conn, enemy_id, "EX3")
    repo.insert_enemy_weaknesses(conn, form["id"], [
        ["Axe", "Bow", "Ice", "Wind", "Dark"],
        ["Dagger", "Bow", "Ice", "Lightning", "Dark"],
    ])
    embed = enemy_embeds.build_enemy_embed(conn, enemy_id, "EX3")
    assert embed is not None
    assert embed.image.url == f"attachment://enemy_weaknesses_{enemy_id}_ex3.png"
    message = enemy_embeds.build_enemy_message(conn, enemy_id, "EX3")
    assert message is not None
    assert message.file is not None
    assert message.file.filename == f"enemy_weaknesses_{enemy_id}_ex3.png"
    assert message.embed.image.url == f"attachment://enemy_weaknesses_{enemy_id}_ex3.png"


def test_build_enemy_embed_returns_none_for_missing_enemy(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    assert enemy_embeds.build_enemy_embed(conn, 99999, "EX3") is None


def test_build_enemy_embed_returns_none_for_missing_rank(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    enemy_id = _seed_lloris(conn)
    assert enemy_embeds.build_enemy_embed(conn, enemy_id, "Rank1") is None


def test_stats_field_fits_in_discord_field_limit(tmp_db_path: Path) -> None:
    """A 6-member encounter (Yunnie EX 3 shape) must still fit per-field."""
    conn = repo.connect(tmp_db_path)
    enemy_id = repo.upsert_enemy(
        conn, canonical_name="Big Encounter", category="Lvl 75",
        region="Osterra", sheet_gid=1, source_row=3,
        name_color_hex=None, hyperlink_url=None, is_npc=False,
    )
    f = repo.insert_enemy_form(conn, enemy_id=enemy_id, rank="EX3", rank_order=6)
    member_names = ("Yunnie", "Hunter", "Thief", "Merchant",
                    "Warrior", "Apothecary")
    stat_names = ("HP", "Shields", "P. Atk", "P. Def", "E. Atk",
                  "E. Def", "Speed", "Crit", "CritDef", "Equip Atk")
    rows = []
    for pos, member in enumerate(member_names):
        for stat in stat_names:
            rows.append({
                "position": pos, "member_name": member,
                "stat_name": stat, "stat_value": str(1234567 + pos),
            })
    repo.insert_enemy_member_stats(conn, f, rows)
    embed = enemy_embeds.build_enemy_embed(conn, enemy_id, "EX3")
    assert embed is not None
    member_fields = [
        field for field in embed.fields if field.name in member_names
    ]
    assert len(member_fields) == len(member_names)
    for field in member_fields:
        assert field.inline is True
        assert len(field.value) <= enemy_embeds.FIELD_VALUE_LIMIT
        for stat in stat_names:
            assert stat in field.value
    # Numeric stat values render with thousands separators.
    yunnie_field = next(f for f in member_fields if f.name == "Yunnie")
    assert "1,234,567" in yunnie_field.value


def test_default_npc_renders_single_non_inline_field(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    enemy_id = _seed_lloris(conn, with_ex3=False, with_default_npc=True)
    embed = enemy_embeds.build_enemy_embed(conn, enemy_id, "Default")
    assert embed is not None
    member_fields = [f for f in embed.fields if f.name == "Leader Lloris"]
    assert len(member_fields) == 1
    assert member_fields[0].inline is False
    # Single-member 1000 still gets a separator.
    assert "1,000" in member_fields[0].value


def test_long_member_names_truncate_to_avoid_inline_wrap(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    enemy_id = repo.upsert_enemy(
        conn, canonical_name="Verbose", category="Lvl 1",
        region="Osterra", sheet_gid=3, source_row=3,
        name_color_hex=None, hyperlink_url=None, is_npc=False,
    )
    f = repo.insert_enemy_form(conn, enemy_id=enemy_id, rank="EX3", rank_order=6)
    long_name = "Extraordinary Apothecary"
    repo.insert_enemy_member_stats(conn, f, [
        {"position": 0, "member_name": long_name,
         "stat_name": "HP", "stat_value": "100"},
        {"position": 1, "member_name": "Short",
         "stat_name": "HP", "stat_value": "50"},
    ])
    embed = enemy_embeds.build_enemy_embed(conn, enemy_id, "EX3")
    assert embed is not None
    field_names = [f.name for f in embed.fields]
    assert long_name not in field_names  # untruncated form must not appear
    assert any(
        n.startswith("Extraordinary") and n.endswith("…")
        and len(n) <= enemy_embeds._MEMBER_NAME_DISPLAY_LIMIT
        for n in field_names
    )
    assert "Short" in field_names


def test_non_numeric_stat_values_pass_through(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    enemy_id = repo.upsert_enemy(
        conn, canonical_name="Quirky", category="Lvl 1",
        region="Osterra", sheet_gid=2, source_row=3,
        name_color_hex=None, hyperlink_url=None, is_npc=False,
    )
    f = repo.insert_enemy_form(conn, enemy_id=enemy_id, rank="EX3", rank_order=6)
    repo.insert_enemy_member_stats(conn, f, [
        {"position": 0, "member_name": "Q", "stat_name": "HP", "stat_value": "-"},
        {"position": 0, "member_name": "Q", "stat_name": "Shields",
         "stat_value": "???"},
        {"position": 0, "member_name": "Q", "stat_name": "P. Atk",
         "stat_value": "1500"},
    ])
    embed = enemy_embeds.build_enemy_embed(conn, enemy_id, "EX3")
    assert embed is not None
    field = next(field for field in embed.fields if field.name == "Q")
    assert "-" in field.value
    assert "???" in field.value
    assert "1,500" in field.value


def test_safe_enemy_url_prefixes_anchor() -> None:
    url = enemy_embeds._safe_enemy_url("#gid=123&range=A1")
    assert url is not None
    assert "1Of4zz3rlV973Rt2kzHqoSWjiJmfhb77iMnAYofCT3Gs" in url
    assert url.endswith("#gid=123&range=A1")


def test_safe_enemy_url_passes_through_full_urls() -> None:
    assert enemy_embeds._safe_enemy_url("https://example.com/x") == "https://example.com/x"
    assert enemy_embeds._safe_enemy_url(None) is None
    assert enemy_embeds._safe_enemy_url("") is None


def test_available_ranks_filters_to_what_exists(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    enemy_id = _seed_lloris(conn)  # has only EX3
    assert enemy_embeds.available_ranks(conn, enemy_id) == ["EX3"]


def test_available_ranks_collapses_to_default_for_npcs(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    enemy_id = _seed_lloris(conn, with_ex3=False, with_default_npc=True)
    assert enemy_embeds.available_ranks(conn, enemy_id) == ["Default"]


def test_default_rank_picks_highest() -> None:
    assert enemy_embeds.default_rank(["EX2", "Rank2", "EX3"]) == "EX3"
    assert enemy_embeds.default_rank(["Default"]) == "Default"
    assert enemy_embeds.default_rank([]) is None
