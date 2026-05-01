"""Type-shape sanity checks for analysis.types dataclasses."""
from __future__ import annotations

from analysis.types import (
    AssumptionProfile,
    BucketedTeam,
    CAP_TIER_GOOD,
    CAP_TIER_SOSO,
    CATEGORIES,
    ClassifiedEffect,
    CoverageMatrix,
    DIRECTIONS,
    DamageReport,
    PerDpsDamageSummary,
    SOURCE_KINDS,
    SURVIVABILITY_TIERS,
    SkillDamageRow,
    SurvivabilityCitation,
    SurvivabilityVerdict,
    TARGET_SCOPES,
    TeamReport,
    cap_tier_label,
)


def test_categories_include_damage_cap_up_and_multi_cast():
    assert "damage_cap_up" in CATEGORIES
    assert "multi_cast" in CATEGORIES
    assert "regen" in CATEGORIES
    assert "undying" in CATEGORIES
    assert "unparsed" in CATEGORIES


def test_auto_revive_is_not_a_survivability_category():
    # Auto-revive doesn't preserve buffs through death, so it does NOT
    # qualify as a survivability mechanic in this analyzer.
    assert "auto_revive" not in CATEGORIES
    assert "necromance" not in CATEGORIES


def test_target_scopes_match_survivability_assertion_keys():
    # Survivability assertions read these scope strings literally.
    assert "all_allies" in TARGET_SCOPES
    assert "other_allies" in TARGET_SCOPES
    assert "frontrow" in TARGET_SCOPES
    assert "self" in TARGET_SCOPES


def test_classified_effect_is_frozen():
    e = ClassifiedEffect(
        source_form_id=1, source_skill_id=2, source_kind="active",
        category="stat_up", targets=("atk",), direction="up",
        magnitude=0.20, duration_turns=3, condition=None,
        boost_required=None, target_scope="all_allies",
        raw_description="20% Atk Up", confidence="high",
    )
    try:
        e.magnitude = 0.50  # type: ignore[misc]
    except (AttributeError, Exception):
        pass
    assert e.magnitude == 0.20
    assert e.source_kind in SOURCE_KINDS
    assert e.direction in DIRECTIONS


def test_assumption_profile_default_is_max_boost():
    p = AssumptionProfile()
    assert p.boost_level == 3
    assert p.assume_full_hp is True
    assert p.includes_boost(None) is True
    assert p.includes_boost(0) is True
    assert p.includes_boost(3) is True


def test_assumption_profile_gates_low_boost_correctly():
    low = AssumptionProfile(boost_level=1)
    assert low.includes_boost(None) is True
    assert low.includes_boost(0) is True
    assert low.includes_boost(1) is True
    assert low.includes_boost(2) is False
    assert low.includes_boost(3) is False


def test_cap_tier_label():
    assert cap_tier_label(0) == "Low"
    assert cap_tier_label(40_000) == "Low"
    assert cap_tier_label(CAP_TIER_SOSO) == "So-so"
    assert cap_tier_label(CAP_TIER_SOSO + 1) == "So-so"
    assert cap_tier_label(CAP_TIER_GOOD - 1) == "So-so"
    assert cap_tier_label(CAP_TIER_GOOD) == "Good"
    assert cap_tier_label(300_000) == "Good"


def test_team_report_round_trips():
    profile = AssumptionProfile()
    bucketed = BucketedTeam(
        frontrow_form_ids=(1, 2, 3, 4),
        backrow_form_ids=(5, 6, 7, 8),
        pet_id=None,
        divine_beast=False,
        cap_orbs=0,
        raw_sub_bucket_sums={},
        team_damage_cap_up=0.0,
        team_skill_potency_up=0.0,
        team_soul_potency_up=0.0,
        classified=(),
        unparsed=(),
        profile=profile,
    )
    verdict = SurvivabilityVerdict(
        tier="None", primary_source_display="—", citations=(),
    )
    matrix = CoverageMatrix()
    damage = DamageReport(
        per_dps=(),
        team_damage_cap_up=0.0, cap_tier="Low",
        team_skill_potency_up=0.0, team_soul_potency_up=0.0,
        g6_active=False,
    )
    report = TeamReport(
        bucketed=bucketed, survivability=verdict,
        coverage=matrix, damage=damage,
    )
    assert report.survivability.tier in SURVIVABILITY_TIERS
    assert report.bucketed.profile is profile
    assert report.bucketed.all_form_ids == (1, 2, 3, 4, 5, 6, 7, 8)


def test_skill_damage_row_and_per_dps_summary_are_constructible():
    sdr = SkillDamageRow(
        skill_id=1, skill_kind="active", name="Fireball",
        power_min=200, power_max=200, hits=1,
    )
    summary = PerDpsDamageSummary(
        form_id=10, display_name="Tester",
        weapon="sword", element="fire",
        buff_multiplier=1.0,
        best_skills=(sdr,),
        is_highlighted_dps=False,
    )
    assert summary.best_skills[0] is sdr


def test_survivability_citation_carries_form_and_skill_ids():
    c = SurvivabilityCitation(form_id=7, skill_id=42, snippet="…")
    assert c.form_id == 7
    assert c.skill_id == 42
