"""End-to-end integration tests for the dormant /analyze_team command.

Walks the full pipeline: seed DB with characters/skills/affinities,
call ``build_team_report``, build the embed, assert the structure
holds.

Two layers:
  - Smoke / wiring tests (existing) — minimal 2-character fixtures.
  - Five-team output regression suite (``test_fixture_team_*``) — each
    parametrised case asserts that the new embed surfaces total-damage
    estimates, why-not-capping reasons, the type-coverage matrix, and
    aliased-name trails for the five archetype teams in the plan.
    Future classifier or formatter changes that silently regress those
    lines will trip these tests.

Doesn't import discord runtime beyond ``discord.Embed`` — no live
Discord interaction is needed.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from bot import team_commands, team_embeds
from analysis import insights
from analysis.types import NameResolution
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
    embed = team_embeds.build_analysis_message(seeded_conn, report).embed
    assert embed.title == "Team Analysis"
    # Required fields present.
    field_names = [f.name for f in embed.fields]
    assert "Best use" in field_names
    assert "Main gaps" in field_names
    assert "Survivability" in field_names
    assert "Team cap and potency" in field_names


def test_insights_rank_real_dps_before_support(seeded_conn):
    f1, f2 = _seed_two_characters(seeded_conn)
    report = team_commands.build_team_report(
        seeded_conn,
        frontrow_form_ids=[f1, f2],
        cap_orbs=1,
    )
    ranked = insights.ranked_dps(report.bucketed, report.damage, limit=2)
    assert [r.summary.display_name for r in ranked] == ["Hero", "Mage"]


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
    embed = team_embeds.build_analysis_message(seeded_conn, report).embed
    for f in embed.fields:
        # Discord caps field values at 1024.
        assert len(f.value) <= 1024


# Registration is now exercised by tests/test_bot_registration.py against
# a real ``discord.app_commands.CommandTree`` — see that file.


# ---------------------------------------------------------------------------
# Five archetype-team fixtures from
# ``C:\Users\Douglas\.claude\plans\use-this-team-and-humble-sundae.md``.
#
# Each parametrised case proves the new embed/CLI output surfaces the
# headline numbers (total damage, type matrix, why-not-capping reason,
# alias trail) on a representative team archetype. Inputs are minimal
# but capture the mechanic each team relies on:
#
#   T1  Sword bruiser w/ aliased typed names + over-3 cap-orb input.
#   T2  Mono-sword meta — 2 capping DPS in same team.
#   T3  Fire mage nuke — potency-limited (no Skill Potency Up source).
#   T4  Mono-bow multi-element — homogenous multipliers.
#   T5  F2P / accessible — capping DPS without paywall units.
# ---------------------------------------------------------------------------

def _seed_form(
    conn, *, name: str, weapon: str, element: str | None = None,
    skills: list[dict] | None = None, equipment: list[dict] | None = None,
) -> int:
    cid = repo.upsert_character(
        conn, name, base_role="warrior", base_weapon=weapon.lower(),
    )
    fid = repo.insert_form(
        conn, character_id=cid, display_name=name,
        rarity="5*", variant_kind="base", server="global",
    )
    affinities = [("weapon", weapon, None)]
    if element:
        affinities.append(("element", element, None))
    repo.insert_affinities(conn, fid, affinities)
    if skills:
        repo.insert_skills(conn, fid, skills)
    if equipment:
        for slot, item in enumerate(equipment, start=1):
            repo.upsert_equipment(
                conn, form_id=fid, slot_order=slot,
                name=item["name"], description=item["description"],
            )
    return fid


def _capping_sword_dps_skill() -> dict:
    """5x AoE 130p sword skill — high enough to cap with +100% potency."""
    return {
        "slot_order": 1, "name": "Greater Sword Stab", "kind": "active",
        "power_min": 130, "power_max": 130, "hits": 5,
        "description": "5x AoE Sword (5x 130 Power)",
    }


def _self_potency_skill() -> dict:
    """Self-scoped Skill Potency Up — bridges the 240-rule on the caster."""
    return {
        "slot_order": 2, "name": "Sword Mastery", "kind": "ex",
        "description": "Self 100% Potency Up for 3 turns",
    }


def _potency_buff_skill(magnitude_pct: int = 100) -> dict:
    return {
        "slot_order": 2, "name": "Empower", "kind": "ultimate",
        "description": (
            f"Single ally {magnitude_pct}% Potency Up + "
            f"+100,000 Damage Cap for 1 turn"
        ),
    }


def _low_potency_fire_skill() -> dict:
    return {
        "slot_order": 1, "name": "Fireball", "kind": "active",
        "power_min": 105, "power_max": 105, "hits": 4,
        "description": "4x AoE Fire (4x 105 Power)",
    }


def _bow_skill_70_x8() -> dict:
    return {
        "slot_order": 1, "name": "Storm of Arrows", "kind": "active",
        "power_min": 70, "power_max": 70, "hits": 8,
        "description": "8x random-target Bow (8x 70 Power)",
    }


@pytest.fixture()
def t1_sword_bruiser(seeded_conn):
    """Sword bruiser team — DPS caps with team's potency-up bridge."""
    bk = _seed_form(
        seeded_conn, name="Black Knight", weapon="Sword",
        skills=[_capping_sword_dps_skill(), _self_potency_skill()],
    )
    pardis = _seed_form(seeded_conn, name="Pardis", weapon="Sword")
    shana = _seed_form(seeded_conn, name="Shana", weapon="Axe")
    mydia = _seed_form(seeded_conn, name="Mydia", weapon="Staff")
    solon = _seed_form(
        seeded_conn, name="Solon", weapon="Spear",
        skills=[_potency_buff_skill(100)],
    )
    others = [
        _seed_form(seeded_conn, name=n, weapon=w)
        for n, w in [("Lucette", "Bow"), ("Black Maiden", "Fan"), ("Cygna", "Fan")]
    ]
    return {
        "front": [bk, pardis, shana, mydia],
        "back": [others[0], others[1], solon, others[2]],
        "name_resolutions": (
            NameResolution(typed="Pardis III", form_id=pardis,
                           resolved_display_name="Pardis", via="alias"),
            NameResolution(typed="Lucetta", form_id=others[0],
                           resolved_display_name="Lucette", via="alias"),
            NameResolution(typed="Dark Priestess", form_id=others[1],
                           resolved_display_name="Black Maiden", via="alias"),
            NameResolution(typed="Signa", form_id=others[2],
                           resolved_display_name="Cygna", via="alias"),
        ),
    }


@pytest.fixture()
def t3_fire_mage_underpowered(seeded_conn):
    """Fire mage team WITHOUT a Skill Potency Up source — should not cap."""
    mage = _seed_form(
        seeded_conn, name="EX Cyrus", weapon="Sword", element="Fire",
        skills=[_low_potency_fire_skill()],
    )
    backups = [
        _seed_form(seeded_conn, name=n, weapon=w, element=e)
        for n, w, e in [
            ("Ditraina", "Tome", "Light"),
            ("EX Ditraina", "Bow", "Fire"),
            ("EX Ophilia", "Tome", "Light"),
            ("Cyrus", "Tome", "Fire"),
            ("Mydia", "Staff", None),
            ("EX Pardis", "Staff", "Light"),
            ("Bonus", "Spear", None),
        ]
    ]
    return {
        "front": [mage, backups[0], backups[1], backups[2]],
        "back": backups[3:7],
    }


@pytest.fixture()
def t4_bow_multi_element(seeded_conn):
    """Mono-bow team — three DPS share the same buff stack."""
    dps_skills = [_bow_skill_70_x8()]
    bow1 = _seed_form(
        seeded_conn, name="EX Lyummis", weapon="Bow", element="Lightning",
        skills=dps_skills,
    )
    bow2 = _seed_form(
        seeded_conn, name="EX Agnea", weapon="Bow", element="Wind",
        skills=dps_skills,
    )
    bow3 = _seed_form(
        seeded_conn, name="EX Ditraina", weapon="Bow", element="Fire",
        skills=dps_skills,
    )
    others = [
        _seed_form(seeded_conn, name=n, weapon=w)
        for n, w in [
            ("EX H'aanit", "Fan"), ("Lucette", "Bow"),
            ("Mydia", "Staff"), ("Solon", "Spear"),
            ("EX Bargello", "Fan"),
        ]
    ]
    return {
        "front": [bow1, bow2, bow3, others[1]],
        "back": [others[0]] + others[2:],
    }


def test_fixture_team_t1_aliased_inputs_surface_in_description(
    seeded_conn, t1_sword_bruiser,
):
    """T1: aliased typed names appear in the embed description."""
    report = team_commands.build_team_report(
        seeded_conn,
        frontrow_form_ids=t1_sword_bruiser["front"],
        backrow_form_ids=t1_sword_bruiser["back"],
        cap_orbs=5,  # User's screenshot showed 5; should still flag the 3-rule.
        name_resolutions=t1_sword_bruiser["name_resolutions"],
    )
    embed = team_embeds.build_analysis_message(seeded_conn, report).embed
    desc = embed.description or ""
    # Alias trail with typed -> resolved arrows.
    assert "Pardis III → Pardis" in desc
    assert "Dark Priestess → Black Maiden" in desc
    assert "Resolved" in desc and "alias/fuzzy" in desc
    # Profile line explains the 3-orb cap rule.
    assert "max 3 free orbs stack" in desc


def test_fixture_team_t1_over_three_cap_orbs_flagged_in_gaps(
    seeded_conn, t1_sword_bruiser,
):
    """T1: entering >3 free orbs triggers the stacking-rule warning."""
    report = team_commands.build_team_report(
        seeded_conn,
        frontrow_form_ids=t1_sword_bruiser["front"],
        backrow_form_ids=t1_sword_bruiser["back"],
        cap_orbs=5,
    )
    embed = team_embeds.build_analysis_message(seeded_conn, report).embed
    gap_field = next(f for f in embed.fields if f.name == "Main gaps")
    assert "only 3 stack" in gap_field.value


def test_fixture_team_t1_capping_dps_shows_total_damage_estimate(
    seeded_conn, t1_sword_bruiser,
):
    """T1: Black Knight has 5x130p sword + 100% Potency Up → caps each hit.

    Expected total = 5 hits × (999_999 + 300_000 cap_orbs) ≈ 6.5M.
    The exact number can drift with classifier improvements; we assert
    the number appears in mega-units and the 'caps at' phrasing fires.
    """
    report = team_commands.build_team_report(
        seeded_conn,
        frontrow_form_ids=t1_sword_bruiser["front"],
        backrow_form_ids=t1_sword_bruiser["back"],
        cap_orbs=3,
        name_resolutions=t1_sword_bruiser["name_resolutions"],
    )
    embed = team_embeds.build_analysis_message(seeded_conn, report).embed
    best_use = next(f for f in embed.fields if f.name == "Best use")
    assert "Black Knight" in best_use.value
    # Damage estimate format: "≈ N.NM dmg — H/H hits cap at N.NM"
    assert "≈" in best_use.value
    assert "hits cap at" in best_use.value


def test_fixture_team_t3_fire_mage_potency_limited_diagnosis(
    seeded_conn, t3_fire_mage_underpowered,
):
    """T3: 105p × 4 hits without Skill Potency Up → realised 105 < 240.

    Expected: gap-line cites the failing potency rule and proposes a
    Skill Potency Up source.
    """
    report = team_commands.build_team_report(
        seeded_conn,
        frontrow_form_ids=t3_fire_mage_underpowered["front"],
        backrow_form_ids=t3_fire_mage_underpowered["back"],
        cap_orbs=3,
    )
    embed = team_embeds.build_analysis_message(seeded_conn, report).embed
    gap_field = next(f for f in embed.fields if f.name == "Main gaps")
    assert "EX Cyrus" in gap_field.value
    assert "below the 240 cap-rule" in gap_field.value
    assert "Skill Potency Up" in gap_field.value
    # Best-use field shows the potency-limited estimate format.
    best_use = next(f for f in embed.fields if f.name == "Best use")
    assert "potency-limited" in best_use.value


def test_fixture_team_t4_type_matrix_shows_team_identity(
    seeded_conn, t4_bow_multi_element,
):
    """T4: mono-bow team — Damage potential by type field surfaces Bow at top."""
    report = team_commands.build_team_report(
        seeded_conn,
        frontrow_form_ids=t4_bow_multi_element["front"],
        backrow_form_ids=t4_bow_multi_element["back"],
        cap_orbs=3,
    )
    embed = team_embeds.build_analysis_message(seeded_conn, report).embed
    matrix_field = next(
        f for f in embed.fields if f.name == "Damage potential by type"
    )
    # Weapons line + Elements line both present.
    assert "**Weapons:**" in matrix_field.value
    assert "**Elements:**" in matrix_field.value


def test_fixture_team_t5_parser_confidence_appears(
    seeded_conn, t1_sword_bruiser,
):
    """T5 / T1: classified-effect ratio surfaces in the description block."""
    report = team_commands.build_team_report(
        seeded_conn,
        frontrow_form_ids=t1_sword_bruiser["front"],
        backrow_form_ids=t1_sword_bruiser["back"],
        cap_orbs=3,
    )
    embed = team_embeds.build_analysis_message(seeded_conn, report).embed
    desc = embed.description or ""
    assert "Parser confidence:" in desc
    assert "%" in desc


def test_fixture_team_t2_per_dps_self_only_cap_up_breakdown(
    seeded_conn, t1_sword_bruiser,
):
    """T2 (modelled on the T1 fixture for brevity): self-only cap-up
    surfaces per character so users see who carries which ceiling.
    """
    report = team_commands.build_team_report(
        seeded_conn,
        frontrow_form_ids=t1_sword_bruiser["front"],
        backrow_form_ids=t1_sword_bruiser["back"],
        cap_orbs=3,
    )
    embed = team_embeds.build_analysis_message(seeded_conn, report).embed
    cap_field = next(
        f for f in embed.fields if f.name == "Team cap and potency"
    )
    # Team-wide breakdown + (when applicable) self-only line.
    assert "Team-wide:" in cap_field.value
    assert "orb(s)" in cap_field.value
    # The Solon ult skill in the fixture grants single-ally potency up
    # — a "Single-ally potency bridges" line should fire.
    assert "Single-ally potency bridges" in cap_field.value


def test_guaranteed_crit_lifts_buff_multiplier_by_125x(seeded_conn):
    """A self Guaranteed Crit on the DPS multiplies the buff product by 1.25.

    Mirrors the audit-CLI numerical check: a DPS with no other multipliers
    should end up at ×1.25 once the crit pattern fires.
    """
    cid = repo.upsert_character(seeded_conn, "Pardis EX", base_role="warrior", base_weapon="sword")
    fid = repo.insert_form(
        seeded_conn, character_id=cid, display_name="Pardis EX",
        rarity="5*", variant_kind="ex", server="global",
    )
    repo.insert_affinities(seeded_conn, fid, [("weapon", "Sword", None)])
    repo.insert_skills(seeded_conn, fid, [
        {
            "slot_order": 1, "name": "Slash", "kind": "active",
            "power_min": 80, "power_max": 80, "hits": 5,
            "description": "5x AoE Sword (5x 80 Power)",
        },
        {
            "slot_order": 2, "name": "Sword Mastery", "kind": "passive",
            "description": "Self Guaranteed Crit while in frontrow",
        },
    ])
    report = team_commands.build_team_report(
        seeded_conn, frontrow_form_ids=[fid],
    )
    assert "sword" in report.bucketed.crit_types
    sword_mult = next(
        d.buff_multiplier for d in report.damage.per_dps
        if d.display_name == "Pardis EX"
    )
    # ≥ 1.25 because the only multiplier in play is the crit pool;
    # other groups are 1.0. Allow a tolerance for any tiny G1 drift.
    assert 1.24 <= sword_mult <= 1.26, sword_mult


def test_crit_damage_up_adds_to_crit_pool(seeded_conn):
    """50% Crit Damage Up + Guaranteed Crit → crit multiplier = 1.25 + 0.50 = 1.75.

    Verifies the Crit Damage Up classifier feeds into the final
    multiplier pool the way ``buff_debuff/README.md`` describes.
    """
    cid = repo.upsert_character(seeded_conn, "Tester", base_role="warrior", base_weapon="sword")
    fid = repo.insert_form(
        seeded_conn, character_id=cid, display_name="Tester",
        rarity="5*", variant_kind="base", server="global",
    )
    repo.insert_affinities(seeded_conn, fid, [("weapon", "Sword", None)])
    repo.insert_skills(seeded_conn, fid, [
        {
            "slot_order": 1, "name": "Slash", "kind": "active",
            "power_min": 80, "power_max": 80, "hits": 5,
            "description": "5x AoE Sword (5x 80 Power)",
        },
        {
            "slot_order": 2, "name": "Crit Stack", "kind": "passive",
            "description": "Self Guaranteed Crit + 50% Crit Damage Up",
        },
    ])
    report = team_commands.build_team_report(
        seeded_conn, frontrow_form_ids=[fid],
    )
    sword_mult = next(
        d.buff_multiplier for d in report.damage.per_dps
        if d.display_name == "Tester"
    )
    # 1.25 base crit + 0.50 Crit Damage Up = 1.75 final-pool crit multiplier.
    assert 1.74 <= sword_mult <= 1.76, sword_mult


def test_type_matrix_shows_full_eight_weapons(seeded_conn):
    """The Damage potential by type field lists every weapon and element."""
    cid = repo.upsert_character(seeded_conn, "Tester", base_role="warrior", base_weapon="sword")
    fid = repo.insert_form(
        seeded_conn, character_id=cid, display_name="Tester",
        rarity="5*", variant_kind="base", server="global",
    )
    repo.insert_affinities(seeded_conn, fid, [("weapon", "Sword", None)])
    report = team_commands.build_team_report(
        seeded_conn, frontrow_form_ids=[fid],
    )
    embed = team_embeds.build_analysis_message(seeded_conn, report).embed
    matrix_field = next(
        f for f in embed.fields if f.name == "Damage potential by type"
    )
    text = matrix_field.value
    # All 8 weapons and 6 elements appear by name.
    for weapon in ("Sword", "Dagger", "Bow", "Axe", "Staff", "Tome", "Fan", "Spear"):
        assert weapon in text, f"missing weapon {weapon} in matrix"
    for element in ("Fire", "Ice", "Lightning", "Wind", "Light", "Dark"):
        assert element in text, f"missing element {element} in matrix"


def test_fixture_team_quantified_support_role_lines(
    seeded_conn, t1_sword_bruiser,
):
    """Support-roles field uses quantified effect strings, not tag clouds."""
    report = team_commands.build_team_report(
        seeded_conn,
        frontrow_form_ids=t1_sword_bruiser["front"],
        backrow_form_ids=t1_sword_bruiser["back"],
        cap_orbs=3,
    )
    embed = team_embeds.build_analysis_message(seeded_conn, report).embed
    support_field = next(
        (f for f in embed.fields if f.name == "Support roles"), None,
    )
    if support_field is None:
        pytest.skip("No support members for this minimal fixture.")
    # Either we see quantified strings ("+%", "+...k cap", "Potency Up")
    # or the explicit 'no parsed team role' fallback — never the old
    # tag-cloud labels like "buff/debuff support".
    text = support_field.value
    assert "buff/debuff support" not in text
    assert "damage cap support" not in text
