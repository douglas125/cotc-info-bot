"""Damage-estimate unit + parity tests.

These tests bypass the classifier and feed synthetic
:class:`ClassifiedEffect` lists into the aggregator, then exercise the
damage-estimate helpers. Goals:

  - **Parity with `damage/full_calc.py`** for a known sub-bucket
    layout: the buff multiplier built by ``damage_estimate`` matches a
    hand-computed result within 1e-9.
  - **Per-DPS scope filtering** for cap-up and skill-potency-up
    follows the design (self-only counts only when this DPS is the
    source; team-wide counts for everyone).
  - **Multi-cast** factor is read from ``classified`` effects, not
    from sub-bucket sums; a self-multi-cast of 2.0 doubles the
    effective hits used by the best-skill picker.
  - **Best-skill filter** drops skills with effective_hits < 4 unless
    self-multi-cast lifts them above the threshold.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from analysis import damage_estimate
from analysis.types import (
    AssumptionProfile,
    BucketedTeam,
    ClassifiedEffect,
)
from db import repo


def _eff(**overrides) -> ClassifiedEffect:
    base = dict(
        source_form_id=1, source_skill_id=1, source_kind="active",
        category="stat_up", targets=("atk",), direction="up",
        magnitude=0.20, duration_turns=3, condition=None,
        boost_required=None, target_scope="all_allies",
        raw_description="20% Atk Up", confidence="high",
    )
    base.update(overrides)
    return ClassifiedEffect(**base)


def _bucketed(
    *,
    front: tuple[int, ...] = (1,),
    back: tuple[int, ...] = (),
    sub_bucket_sums: dict[str, float] | None = None,
    classified: list[ClassifiedEffect] | None = None,
    cap_orbs: int = 0,
    divine_beast: bool = False,
    team_cap_up: float = 0.0,
    team_skill_potency_up: float = 0.0,
    team_soul_potency_up: float = 0.0,
) -> BucketedTeam:
    return BucketedTeam(
        frontrow_form_ids=front,
        backrow_form_ids=back,
        pet_id=None,
        divine_beast=divine_beast,
        cap_orbs=cap_orbs,
        raw_sub_bucket_sums=sub_bucket_sums or {},
        team_damage_cap_up=team_cap_up,
        team_skill_potency_up=team_skill_potency_up,
        team_soul_potency_up=team_soul_potency_up,
        classified=tuple(classified or []),
        unparsed=(),
        profile=AssumptionProfile(),
    )


# ---------------------------------------------------------------------------
# Parity with damage/full_calc.py.
# ---------------------------------------------------------------------------

def test_buff_multiplier_with_no_buffs_is_one():
    bt = _bucketed()
    mult = damage_estimate._buff_multiplier_for(
        bt, weapon="sword", element=None, dps_form_id=1,
    )
    assert mult == pytest.approx(1.0, abs=1e-9)


def test_buff_multiplier_g1_only_is_one_plus_capped_sum():
    """G1 stat-up of 20% (under 30% cap) yields 1.20."""
    sums = {"g1.active.atk_up": 0.20}
    bt = _bucketed(sub_bucket_sums=sums)
    mult = damage_estimate._buff_multiplier_for(
        bt, weapon="sword", element=None, dps_form_id=1,
    )
    assert mult == pytest.approx(1.20, abs=1e-9)


def test_buff_multiplier_g1_caps_at_30_percent():
    """G1 stat-up of 45% caps to 30% under default sub-bucket cap."""
    sums = {"g1.active.atk_up": 0.45}
    bt = _bucketed(sub_bucket_sums=sums)
    mult = damage_estimate._buff_multiplier_for(
        bt, weapon="sword", element=None, dps_form_id=1,
    )
    assert mult == pytest.approx(1.30, abs=1e-9)


def test_buff_multiplier_g2_filters_by_weapon():
    """Sword DPS gets credit for Sword DMG Up but not Axe DMG Up."""
    sums = {
        "g2.active.sword_dmg_up": 0.20,
        "g2.active.axe_dmg_up": 0.20,
    }
    bt = _bucketed(sub_bucket_sums=sums)
    mult = damage_estimate._buff_multiplier_for(
        bt, weapon="sword", element=None, dps_form_id=1,
    )
    # Sword 20% only — axe filtered out.
    assert mult == pytest.approx(1.20, abs=1e-9)


def test_buff_multiplier_g3_filters_by_element():
    """Fire elemental DPS picks up Fire Res Down only."""
    sums = {
        "g3.active.fire_res_down": 0.15,
        "g3.active.ice_res_down": 0.15,
    }
    bt = _bucketed(sub_bucket_sums=sums)
    mult = damage_estimate._buff_multiplier_for(
        bt, weapon=None, element="fire", dps_form_id=1,
    )
    assert mult == pytest.approx(1.15, abs=1e-9)


def test_buff_multiplier_combines_g1_g2_g3_g6():
    """Full stack: 20% G1 × 20% G2 × 15% G3 × 1.10 G6 = 1.20×1.20×1.15×1.10."""
    sums = {
        "g1.active.atk_up": 0.20,
        "g2.passive.sword_dmg_up": 0.20,
        "g3.active.sword_res_down": 0.15,
    }
    bt = _bucketed(sub_bucket_sums=sums, divine_beast=True)
    mult = damage_estimate._buff_multiplier_for(
        bt, weapon="sword", element=None, dps_form_id=1,
    )
    expected = 1.20 * 1.20 * 1.15 * 1.10
    assert mult == pytest.approx(expected, abs=1e-9)


def test_buff_multiplier_includes_team_skill_potency_up():
    """Team skill_potency_up multiplies (1 + Σ skill_potency_up)."""
    sums = {"g1.active.atk_up": 0.20}
    bt = _bucketed(sub_bucket_sums=sums, team_skill_potency_up=1.00)
    mult = damage_estimate._buff_multiplier_for(
        bt, weapon="sword", element=None, dps_form_id=1,
    )
    # G1 1.20 × Skill Potency 2.00 = 2.40
    assert mult == pytest.approx(2.40, abs=1e-9)


# ---------------------------------------------------------------------------
# Per-DPS cap-up scope filtering.
# ---------------------------------------------------------------------------

def test_cap_up_breakdown_team_wide_includes_orbs():
    bt = _bucketed(cap_orbs=2)
    team_wide, self_only = damage_estimate.cap_up_breakdown_for_dps(bt, 1)
    assert team_wide == pytest.approx(200_000.0)
    assert self_only == pytest.approx(0.0)


def test_cap_up_breakdown_self_scope_only_counts_for_origin():
    """Pardis's self cap-up shouldn't help Black Knight as DPS."""
    self_cap = _eff(
        source_form_id=1, category="damage_cap_up", targets=(),
        direction="up", magnitude=100_000.0, target_scope="self",
    )
    bt = _bucketed(classified=[self_cap])
    team_for_1, self_for_1 = damage_estimate.cap_up_breakdown_for_dps(bt, 1)
    assert self_for_1 == pytest.approx(100_000.0)
    team_for_2, self_for_2 = damage_estimate.cap_up_breakdown_for_dps(bt, 2)
    assert self_for_2 == pytest.approx(0.0)
    assert team_for_2 == pytest.approx(0.0)


def test_cap_up_breakdown_all_allies_scope_helps_everyone():
    all_cap = _eff(
        source_form_id=1, category="damage_cap_up", targets=(),
        direction="up", magnitude=100_000.0, target_scope="all_allies",
    )
    bt = _bucketed(classified=[all_cap])
    for fid in (1, 2, 99):
        team, self_only = damage_estimate.cap_up_breakdown_for_dps(bt, fid)
        # ``team_damage_cap_up`` field on the BucketedTeam was passed at 0.0
        # in the constructor here — that's intentional: the breakdown helper
        # iterates ``classified`` directly, so even if the BucketedTeam's
        # cached total is empty the per-DPS breakdown still surfaces it.
        assert team == pytest.approx(100_000.0)
        assert self_only == pytest.approx(0.0)


def test_potency_up_breakdown_self_scope_filters_per_dps():
    self_pot = _eff(
        source_form_id=1, category="skill_potency_up", targets=(),
        direction="up", magnitude=1.00, target_scope="self",
    )
    bt = _bucketed(classified=[self_pot])
    team_for_1, self_for_1 = damage_estimate.potency_up_breakdown_for_dps(bt, 1)
    assert self_for_1 == pytest.approx(1.00)
    team_for_2, self_for_2 = damage_estimate.potency_up_breakdown_for_dps(bt, 2)
    assert self_for_2 == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Multi-cast factor.
# ---------------------------------------------------------------------------

def test_self_multi_cast_factor_default_is_one():
    bt = _bucketed()
    assert damage_estimate.self_multi_cast_factor(bt, 1) == pytest.approx(1.0)


def test_self_multi_cast_factor_picks_self_scoped_for_origin():
    mc = _eff(
        source_form_id=1, category="multi_cast", targets=(),
        direction="n/a", magnitude=3.0, target_scope="self",
    )
    bt = _bucketed(classified=[mc])
    assert damage_estimate.self_multi_cast_factor(bt, 1) == pytest.approx(3.0)
    # Other DPS doesn't get someone else's self-multi-cast.
    assert damage_estimate.self_multi_cast_factor(bt, 2) == pytest.approx(1.0)


def test_self_multi_cast_factor_takes_max_of_multiple_sources():
    mc1 = _eff(
        source_form_id=1, category="multi_cast", source_skill_id=10,
        targets=(), direction="n/a", magnitude=2.0, target_scope="self",
    )
    mc2 = _eff(
        source_form_id=1, category="multi_cast", source_skill_id=11,
        targets=(), direction="n/a", magnitude=3.0, target_scope="self",
    )
    bt = _bucketed(classified=[mc1, mc2])
    assert damage_estimate.self_multi_cast_factor(bt, 1) == pytest.approx(3.0)


def test_effective_hits_helper():
    assert damage_estimate.effective_hits(4, 1.0) == 4
    assert damage_estimate.effective_hits(4, 2.0) == 8
    assert damage_estimate.effective_hits(None, 2.0) == 0


# ---------------------------------------------------------------------------
# Best-skill picker filter.
# ---------------------------------------------------------------------------

@pytest.fixture()
def conn(tmp_db_path: Path):
    c = repo.connect(tmp_db_path)
    cid = repo.upsert_character(c, "Tester", base_role="warrior", base_weapon="sword")
    fid = repo.insert_form(
        c, character_id=cid, display_name="Tester",
        rarity="5*", variant_kind="base", server="global",
    )
    yield c, fid
    c.close()


def test_best_skills_drops_three_hit_skill_at_default_multi_cast(conn):
    """3-hit skill is excluded from damage-relevant set under multi-cast 1.0."""
    c, fid = conn
    repo.insert_skills(c, fid, [
        {"slot_order": 1, "name": "Three-hit", "kind": "active",
         "power_min": 100, "power_max": 100, "hits": 3, "description": "..."},
        {"slot_order": 2, "name": "Five-hit", "kind": "active",
         "power_min": 80, "power_max": 80, "hits": 5, "description": "..."},
    ])
    out = damage_estimate._best_skills_for(c, fid, multi_cast=1.0, top=5)
    names = [r.name for r in out]
    assert "Three-hit" not in names
    assert "Five-hit" in names


def test_best_skills_includes_two_hit_with_triplecast(conn):
    """2-hit × 3 = 6 effective hits, qualifies."""
    c, fid = conn
    repo.insert_skills(c, fid, [
        {"slot_order": 1, "name": "Two-hit", "kind": "active",
         "power_min": 100, "power_max": 100, "hits": 2, "description": "..."},
    ])
    out = damage_estimate._best_skills_for(c, fid, multi_cast=3.0, top=5)
    names = [r.name for r in out]
    assert "Two-hit" in names


def test_best_skills_drops_one_hit_even_with_triplecast(conn):
    """1-hit × 3 = 3 effective hits — still below the 4 threshold."""
    c, fid = conn
    repo.insert_skills(c, fid, [
        {"slot_order": 1, "name": "One-hit-big", "kind": "active",
         "power_min": 500, "power_max": 500, "hits": 1, "description": "..."},
    ])
    out = damage_estimate._best_skills_for(c, fid, multi_cast=3.0, top=5)
    assert out == []


def test_best_skills_excludes_non_damage_kinds(conn):
    """Passive/EX skills with power columns shouldn't appear."""
    c, fid = conn
    repo.insert_skills(c, fid, [
        {"slot_order": 1, "name": "Passive", "kind": "passive",
         "power_min": 80, "power_max": 80, "hits": 5, "description": "..."},
        {"slot_order": 2, "name": "Active5x", "kind": "active",
         "power_min": 80, "power_max": 80, "hits": 5, "description": "..."},
    ])
    out = damage_estimate._best_skills_for(c, fid, multi_cast=1.0, top=5)
    assert [r.name for r in out] == ["Active5x"]


def test_best_skills_rank_repeat_text_by_effective_hits(conn):
    c, fid = conn
    repo.insert_skills(c, fid, [
        {"slot_order": 1, "name": "Three-hit", "kind": "active",
         "power_min": 65, "power_max": 65, "hits": 3,
         "description": "3x single-target Sword (3x 65 Power)"},
        {"slot_order": 2, "name": "Repeater", "kind": "active",
         "power_min": 70, "power_max": 70, "hits": 1,
         "description": "1x AoE Sword (1x 70 Power) If Boost MAX, repeat this attack once (up to 3x)"},
    ])
    out = damage_estimate._best_skills_for(c, fid, multi_cast=2.0, top=2)
    assert [r.name for r in out] == ["Repeater", "Three-hit"]
    assert out[0].weapon == "sword"
    assert out[0].repeat_factor == 4.0
    assert damage_estimate.effective_hits_for_skill(out[0], 2.0) == 8


def test_summary_uses_best_skill_attack_type_for_multiplier(conn):
    c, fid = conn
    c.execute(
        "UPDATE characters SET base_weapon = 'tome' "
        "WHERE id = (SELECT character_id FROM character_forms WHERE id = ?)",
        (fid,),
    )
    repo.insert_skills(c, fid, [
        {"slot_order": 1, "name": "Sword DPS", "kind": "active",
         "power_min": 70, "power_max": 70, "hits": 4,
         "description": "4x single-target Sword (4x 70 Power)"},
    ])
    bt = _bucketed(
        front=(fid,),
        sub_bucket_sums={"g2.active.sword_dmg_up": 0.30},
    )
    report = damage_estimate.build(bt, c)
    assert report.per_dps[0].weapon == "sword"
    assert report.per_dps[0].buff_multiplier == pytest.approx(1.30)


# ---------------------------------------------------------------------------
# Cap-reached heuristic.
# ---------------------------------------------------------------------------

def test_caps_each_hit_requires_good_tier_and_240_potency():
    # Good tier at 100% potency up: 120 power × 2 = 240 → caps.
    assert damage_estimate.caps_each_hit(
        power=120, skill_potency_up=1.00, team_damage_cap_up=100_000,
    )
    # So-so tier: doesn't cap regardless of potency.
    assert not damage_estimate.caps_each_hit(
        power=200, skill_potency_up=1.00, team_damage_cap_up=50_000,
    )
    # Good tier, low potency: doesn't reach 240 realised.
    assert not damage_estimate.caps_each_hit(
        power=100, skill_potency_up=0.50, team_damage_cap_up=100_000,
    )


def test_realised_potency_helper():
    assert damage_estimate.realised_potency(120, 1.00) == pytest.approx(240.0)
    assert damage_estimate.realised_potency(None, 1.00) == pytest.approx(0.0)
