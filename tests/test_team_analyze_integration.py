"""End-to-end integration tests for the dormant /analyze_team command.

Walks the full pipeline: seed DB with characters/skills/affinities,
call ``build_team_report``, build the embed, assert the structure
holds. Phase 1's empty pattern table means every skill classifies as
``unparsed``; the integration test verifies the wiring (no crashes,
correct field shapes) rather than classifier accuracy.

Doesn't import discord runtime beyond ``discord.Embed`` — no live
Discord interaction is needed.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from bot import team_commands, team_embeds
from db import repo


def _seed_two_characters(conn, *, with_skills: bool = True) -> tuple[int, int]:
    """Create two minimal characters, return their form ids."""
    c1 = repo.upsert_character(conn, "Hero", base_role="warrior", base_weapon="sword")
    c2 = repo.upsert_character(conn, "Mage", base_role="scholar", base_weapon="tome")
    f1 = repo.insert_form(
        conn, character_id=c1, display_name="Hero",
        rarity="5*", variant_kind="base", server="global",
    )
    f2 = repo.insert_form(
        conn, character_id=c2, display_name="Mage",
        rarity="5*", variant_kind="base", server="global",
    )
    repo.insert_affinities(conn, f1, [("weapon", "Sword", None)])
    repo.insert_affinities(conn, f2, [("weapon", "Tome", None), ("element", "Fire", None)])
    if with_skills:
        repo.insert_skills(conn, f1, [
            {"slot_order": 1, "name": "Slash", "kind": "active",
             "power_min": 80, "power_max": 80, "hits": 5,
             "description": "5x AoE Sword (5x 80 Power)"},
            {"slot_order": 2, "name": "Stance", "kind": "passive",
             "description": "Self 15% Atk Up"},
        ])
        repo.insert_skills(conn, f2, [
            {"slot_order": 1, "name": "Fireball", "kind": "active",
             "power_min": 100, "power_max": 100, "hits": 4,
             "description": "4x AoE Fire (4x 100 Power)"},
        ])
    return f1, f2


@pytest.fixture()
def seeded_conn(tmp_db_path: Path):
    conn = repo.connect(tmp_db_path)
    yield conn
    conn.close()


def test_build_team_report_smoke(seeded_conn):
    """Pipeline runs end-to-end on a seeded DB without raising."""
    f1, f2 = _seed_two_characters(seeded_conn)
    report = team_commands.build_team_report(
        seeded_conn,
        frontrow_form_ids=[f1, f2],
        backrow_form_ids=[],
        cap_orbs=2,
        boost_level=3,
    )
    assert report.bucketed.frontrow_form_ids == (f1, f2)
    assert report.bucketed.cap_orbs == 2
    # Phase 1: empty pattern table → every skill unparsed → tier "None".
    assert report.survivability.tier == "None"
    # Cap orbs alone should reach the Good tier (2 × 100k = 200k).
    assert report.damage.cap_tier == "Good"


def test_team_report_with_only_frontrow_passes(seeded_conn):
    f1, f2 = _seed_two_characters(seeded_conn)
    report = team_commands.build_team_report(
        seeded_conn,
        frontrow_form_ids=[f1, f2],
    )
    assert report.bucketed.backrow_form_ids == ()
    # 8 candidate DPS reduces to 2 here.
    assert len(report.damage.per_dps) == 2


def test_team_report_includes_all_eight_form_ids_when_filled(seeded_conn):
    """With 4 frontrow + 4 backrow, all 8 are candidate DPS."""
    f1, f2 = _seed_two_characters(seeded_conn)
    # Add 6 more characters to fill out the team.
    extra: list[int] = []
    for i in range(6):
        cid = repo.upsert_character(seeded_conn, f"Extra{i}", base_role="thief", base_weapon="dagger")
        fid = repo.insert_form(
            seeded_conn, character_id=cid, display_name=f"Extra{i}",
            rarity="5*", variant_kind="base", server="global",
        )
        extra.append(fid)
    front = [f1, f2, extra[0], extra[1]]
    back = [extra[2], extra[3], extra[4], extra[5]]
    report = team_commands.build_team_report(
        seeded_conn, frontrow_form_ids=front, backrow_form_ids=back,
    )
    assert len(report.damage.per_dps) == 8


def test_embed_renders_without_error(seeded_conn):
    """``team_embeds.build`` produces a valid Embed for the smoke team."""
    f1, f2 = _seed_two_characters(seeded_conn)
    report = team_commands.build_team_report(
        seeded_conn, frontrow_form_ids=[f1, f2], cap_orbs=1,
    )
    embed = team_embeds.build(seeded_conn, report)
    assert embed.title == "Team Analysis"
    # Required fields present.
    field_names = [f.name for f in embed.fields]
    assert "Survivability" in field_names
    assert "Damage cap up" in field_names
    assert "Per-DPS damage" in field_names


def test_embed_truncates_long_unparsed_list_to_field_limit(seeded_conn):
    """Even with many unparsed skills, the field stays under the 1024 cap."""
    cid = repo.upsert_character(seeded_conn, "Wordy", base_role="warrior", base_weapon="sword")
    fid = repo.insert_form(
        seeded_conn, character_id=cid, display_name="Wordy",
        rarity="5*", variant_kind="base", server="global",
    )
    long_desc = " ".join(["wordy"] * 50)
    skills = [
        {"slot_order": i, "name": f"Skill{i}", "kind": "active",
         "description": long_desc, "power_min": None, "power_max": None,
         "hits": None}
        for i in range(20)
    ]
    repo.insert_skills(seeded_conn, fid, skills)
    report = team_commands.build_team_report(seeded_conn, frontrow_form_ids=[fid])
    embed = team_embeds.build(seeded_conn, report)
    for f in embed.fields:
        # Discord caps field values at 1024.
        assert len(f.value) <= 1024


def test_register_is_a_no_op_in_phase_1():
    """The dormant command must not register anything on the tree."""

    class _StubTree:
        def __init__(self):
            self.commands: list = []

        def command(self, *_a, **_k):
            def wrap(fn):
                self.commands.append(fn)
                return fn
            return wrap

    stub = _StubTree()
    team_commands.register(stub)
    assert stub.commands == []
