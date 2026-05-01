"""Aggregator binning, dedupe, and umbrella expansion tests.

These tests bypass the classifier and feed synthetic ClassifiedEffect
lists straight into the bucketing helpers, so they're independent of
Phase 2 pattern population.
"""
from __future__ import annotations

from analysis import aggregator
from analysis.types import AssumptionProfile, ClassifiedEffect


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


def test_stat_up_bins_into_g1_active_atk_up():
    sums = aggregator._bin_into_sub_buckets([_eff(magnitude=0.20)])
    assert sums == {"g1.active.atk_up": 0.20}


def test_stat_down_bins_into_g1_active_def_down():
    e = _eff(category="stat_down", targets=("def",), direction="down")
    sums = aggregator._bin_into_sub_buckets([e])
    assert sums == {"g1.active.def_down": 0.20}


def test_passive_kind_routes_to_passive_source():
    e = _eff(source_kind="passive", magnitude=0.15)
    sums = aggregator._bin_into_sub_buckets([e])
    assert sums == {"g1.passive.atk_up": 0.15}


def test_equipment_kind_routes_to_passive_source():
    e = _eff(source_kind="equipment", magnitude=0.10)
    sums = aggregator._bin_into_sub_buckets([e])
    assert sums == {"g1.passive.atk_up": 0.10}


def test_ultimate_dmg_up_routes_to_g4_sub_pool_b():
    """Per buff_debuff/README.md: ultimate-source DMG Up lives in G4
    sub-pool B (Ultimate DMG Up), not G2."""
    e = _eff(source_kind="ultimate", category="dmg_up",
             targets=("sword",), direction="up", magnitude=0.30)
    sums = aggregator._bin_into_sub_buckets([e])
    assert sums == {"g4.ultimate.sword_dmg_up": 0.30}


def test_ultimate_stat_up_routes_to_g4_sub_pool_a():
    """Ultimate-source stat buffs go to G4 sub-pool A, not G1."""
    e = _eff(source_kind="ultimate", category="stat_up",
             targets=("atk",), direction="up", magnitude=0.30)
    sums = aggregator._bin_into_sub_buckets([e])
    assert sums == {"g4.ultimate.atk_up": 0.30}


def test_ultimate_res_down_routes_to_g4_sub_pool_c():
    """Ultimate-source res_down goes to G4 sub-pool C, not G3."""
    e = _eff(source_kind="ultimate", category="res_down",
             targets=("fire",), direction="down", magnitude=0.30)
    sums = aggregator._bin_into_sub_buckets([e])
    assert sums == {"g4.ultimate.fire_res_down": 0.30}


def test_dmg_up_with_specific_weapon_target():
    e = _eff(category="dmg_up", targets=("sword",), direction="up", magnitude=0.20)
    sums = aggregator._bin_into_sub_buckets([e])
    assert sums == {"g2.active.sword_dmg_up": 0.20}


def test_umbrella_physical_fans_out_to_all_eight_weapon_buckets():
    e = _eff(category="dmg_up", targets=("umbrella:physical",),
             direction="up", magnitude=0.10)
    sums = aggregator._bin_into_sub_buckets([e])
    weapons = ("sword", "dagger", "bow", "axe", "staff", "tome", "fan", "spear")
    expected = {f"g2.active.{w}_dmg_up": 0.10 for w in weapons}
    assert sums == expected


def test_umbrella_elemental_fans_out_to_all_six_element_buckets():
    e = _eff(category="dmg_up", targets=("umbrella:elemental",),
             direction="up", magnitude=0.10)
    sums = aggregator._bin_into_sub_buckets([e])
    elements = ("light", "dark", "wind", "ice", "fire", "lightning")
    expected = {f"g2.active.{el}_dmg_up": 0.10 for el in elements}
    assert sums == expected


def test_umbrella_all_fans_out_to_fourteen_keys():
    e = _eff(category="dmg_up", targets=("umbrella:all",),
             direction="up", magnitude=0.10)
    sums = aggregator._bin_into_sub_buckets([e])
    assert len(sums) == 8 + 6


def test_two_different_skills_stack_additively():
    a = _eff(source_skill_id=1, magnitude=0.20)
    b = _eff(source_skill_id=2, magnitude=0.20)
    sums = aggregator._bin_into_sub_buckets([a, b])
    assert sums == {"g1.active.atk_up": 0.40}


def test_same_skill_dedupe_keeps_higher_magnitude():
    a = _eff(source_skill_id=7, magnitude=0.20)
    b = _eff(source_skill_id=7, magnitude=0.30)
    deduped = aggregator._dedupe_same_skill([a, b])
    assert len(deduped) == 1
    assert deduped[0].magnitude == 0.30


def test_same_skill_dedupe_distinguishes_by_target():
    atk = _eff(source_skill_id=7, targets=("atk",), magnitude=0.20)
    sword = _eff(source_skill_id=7, category="dmg_up",
                 targets=("sword",), magnitude=0.15)
    deduped = aggregator._dedupe_same_skill([atk, sword])
    assert len(deduped) == 2


def test_res_down_bins_into_g3():
    e = _eff(category="res_down", targets=("fire",), direction="down", magnitude=0.20)
    sums = aggregator._bin_into_sub_buckets([e])
    assert sums == {"g3.active.fire_res_down": 0.20}


def test_survivability_only_categories_dont_bin_into_groups():
    regen = _eff(category="regen", targets=(), direction="n/a", magnitude=150.0,
                 target_scope="all_allies")
    assert aggregator._bin_into_sub_buckets([regen]) == {}


def test_damage_cap_up_does_not_bin_into_groups():
    cap = _eff(category="damage_cap_up", targets=(), direction="up",
               magnitude=100_000.0)
    assert aggregator._bin_into_sub_buckets([cap]) == {}


def test_multi_cast_does_not_bin_into_groups():
    """Multi-cast is per-DPS effective-hits multiplier, not a bucket buff."""
    mc = _eff(category="multi_cast", targets=(), direction="n/a",
              magnitude=2.0, target_scope="self")
    assert aggregator._bin_into_sub_buckets([mc]) == {}


def test_assumption_profile_filters_out_high_boost_effects():
    """A boost-2 profile should drop effects gated on boost MAX (3)."""
    # Aggregator-level filtering happens via aggregate_team using
    # AssumptionProfile.includes_boost; verify the helper directly.
    profile = AssumptionProfile(boost_level=2)
    assert profile.includes_boost(3) is False
    assert profile.includes_boost(2) is True
