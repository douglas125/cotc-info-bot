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


def test_build_enemy_embed_renders_stats_grid(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    enemy_id = _seed_lloris(conn)
    embed = enemy_embeds.build_enemy_embed(conn, enemy_id, "EX3")
    assert embed is not None
    assert embed.title is not None
    assert "Sly Leader Lloris" in embed.title
    assert "EX 3" in embed.title
    # Description shows category + region.
    assert "Solistia" in (embed.description or "")
    # Stats field
    fields = {f.name: f.value for f in embed.fields}
    assert "Stats" in fields
    assert "1,143,210" in fields["Stats"]
    assert "822,762" in fields["Stats"]
    # Break shields field
    assert "Break Shields" in fields
    assert "30" in fields["Break Shields"]
    assert "18" in fields["Break Shields"]


def test_build_enemy_embed_returns_none_for_missing_enemy(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    assert enemy_embeds.build_enemy_embed(conn, 99999, "EX3") is None


def test_build_enemy_embed_returns_none_for_missing_rank(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    enemy_id = _seed_lloris(conn)
    assert enemy_embeds.build_enemy_embed(conn, enemy_id, "Rank1") is None


def test_stats_field_fits_in_discord_field_limit(tmp_db_path: Path) -> None:
    """An encounter with 3 members × 10 stats must still fit under 1024 chars."""
    conn = repo.connect(tmp_db_path)
    enemy_id = repo.upsert_enemy(
        conn, canonical_name="Big Encounter", category="Lvl 75",
        region="Osterra", sheet_gid=1, source_row=3,
        name_color_hex=None, hyperlink_url=None, is_npc=False,
    )
    f = repo.insert_enemy_form(conn, enemy_id=enemy_id, rank="EX3", rank_order=6)
    rows = []
    for pos in range(3):
        for stat in ("HP", "Shields", "P. Atk", "P. Def", "E. Atk",
                     "E. Def", "Speed", "Crit", "CritDef", "Equip Atk"):
            rows.append({
                "position": pos, "member_name": f"Member{pos}",
                "stat_name": stat, "stat_value": str(1234567 + pos),
            })
    repo.insert_enemy_member_stats(conn, f, rows)
    embed = enemy_embeds.build_enemy_embed(conn, enemy_id, "EX3")
    assert embed is not None
    stats_field = next(f for f in embed.fields if f.name == "Stats")
    assert len(stats_field.value) <= enemy_embeds.FIELD_VALUE_LIMIT


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


def test_default_rank_picks_lowest() -> None:
    assert enemy_embeds.default_rank(["EX2", "Rank2", "EX3"]) == "Rank2"
    assert enemy_embeds.default_rank(["Default"]) == "Default"
    assert enemy_embeds.default_rank([]) is None
