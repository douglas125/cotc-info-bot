"""Dataclasses shared across the analysis package.

These are the contract between the classifier, the aggregator, the
survivability assessor, and the damage estimator. Everything is
``frozen=True`` so reports are safe to pass around without defensive
copies; mutation happens only inside builders that produce fresh
instances.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


# ---------------------------------------------------------------------------
# Constants used as enum-like string values throughout the package.
#
# Keeping these as strings (rather than ``enum.Enum``) keeps the dataclasses
# JSON-serialisable for the snapshot test fixtures in
# ``tests/fixtures/teams/``.
# ---------------------------------------------------------------------------

# Source of an effect — directly mirrors ``skills.kind`` plus 'equipment'
# for A4 effects classified from equipment.description.
SOURCE_KINDS: tuple[str, ...] = (
    "active", "passive", "ultimate",
    "ex", "latent", "tp_passive", "divine",
    "equipment",
)

# Categories the classifier emits. Each maps to a downstream consumer:
#   stat_up / stat_down                       -> G1 sub-buckets
#   dmg_up                                    -> G2 sub-buckets
#   res_down                                  -> G3 sub-buckets
#   crit_up / crit_dmg_up                     -> G1 / Crit final multiplier
#   soul_potency_up / skill_potency_up        -> final multipliers
#   damage_cap_up                             -> raw-units cap-up tally
#   multi_cast                                -> per-DPS effective-hits multiplier
#                                                (magnitude == factor: 2.0 doublecast,
#                                                 3.0 triplecast); always self-scoped
#                                                today, but target_scope is honoured
#                                                so the model is forwards-compatible.
#   regen / heal                              -> survivability
#   undying                                   -> survivability (Shana mechanic)
#   shield / cleanse                          -> defensive utility
#   unparsed                                  -> debug surface
#
# Auto-revive is intentionally NOT a survivability category — when a unit dies
# they lose all buffs, so auto-revive does not preserve combat continuity in
# the way regen/undying do. Skills that grant auto-revive should classify as
# 'unparsed' (for now) or be filtered out entirely.
CATEGORIES: tuple[str, ...] = (
    "stat_up", "stat_down",
    "dmg_up", "res_down",
    "crit_up", "crit_dmg_up",
    "soul_potency_up", "skill_potency_up",
    "damage_cap_up",
    "multi_cast",
    "regen", "heal", "undying", "shield", "cleanse",
    "unparsed",
)

DIRECTIONS: tuple[str, ...] = ("up", "down", "n/a")

# Effect target scope — who an effect benefits/debuffs. Used by both
# regen/heal/undying (survivability) and damage-cap-up / buff effects
# (damage estimator filters per candidate DPS by scope).
TARGET_SCOPES: tuple[str, ...] = (
    "self", "frontrow", "all_allies", "other_allies", "enemies",
)

# Confidence with which the classifier emits an effect. ``unparsed`` is
# reserved for descriptions that produced no pattern hits.
CONFIDENCES: tuple[str, ...] = ("high", "low", "unparsed")


# ---------------------------------------------------------------------------
# Effect — one classifier output for one (skill or equipment) description.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ClassifiedEffect:
    """One unit of classified buff/debuff/regen extracted from a description.

    A single description can produce zero, one, or many of these — e.g.
    "Frontrow 20% Atk Up + 15% Sword DMG Up for 3 turns" yields two
    effects, one in ``stat_up`` and one in ``dmg_up``.

    ``magnitude`` semantics:
      - For percentage categories (``stat_up``, ``stat_down``, ``dmg_up``,
        ``res_down``, ``crit_up``, ``crit_dmg_up``, ``soul_potency_up``,
        ``skill_potency_up``), magnitude is a decimal fraction:
        ``0.20`` == 20%.
      - For ``damage_cap_up``, magnitude is **raw damage units**:
        ``100000.0`` == +100k cap.
      - For qualitative categories (``regen``, ``heal``, ``undying``,
        ``shield``, ``cleanse``, ``unparsed``), magnitude is the raw
        regen/heal strength when the description gives one (e.g.
        ``150.0`` for "150 Regen Strength"); ``0.0`` when no number is
        present.
    """

    source_form_id: int
    source_skill_id: int          # repo skill row id; -1 for equipment
    source_kind: str              # one of SOURCE_KINDS
    category: str                 # one of CATEGORIES
    targets: tuple[str, ...]      # e.g. ('sword',) ('fire','ice')
                                  #      ('umbrella:physical',)
                                  #      empty for non-typed categories
    direction: str                # one of DIRECTIONS
    magnitude: float              # see docstring above
    duration_turns: int | None
    condition: str | None
    boost_required: int | None    # parsed from "If Boost MAX" etc.
    target_scope: str | None      # one of TARGET_SCOPES, or None.
                                  # For regen/heal/undying: who's protected.
                                  # For damage_cap_up / buffs: who benefits;
                                  # e.g. 'self' means only the source form's
                                  # damage as DPS gets that cap up.
    raw_description: str
    confidence: str               # one of CONFIDENCES


# ---------------------------------------------------------------------------
# Profile — assumptions the analyser operates under.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AssumptionProfile:
    """Scenario assumptions fed into the aggregator.

    The default is an *optimistic ceiling* view: max boost, conditional
    passives assumed active, channeling assumed up. The embed footer
    prints the assumption so a user can spot when their actual play
    diverges.
    """

    boost_level: int = 3              # 0/1/2/3 — 3 == MAX
    assume_full_hp: bool = True
    assume_channeling_active: bool = True

    def includes_boost(self, required: int | None) -> bool:
        """True if a conditional effect's boost gate is met by this profile."""
        if required is None:
            return True
        return self.boost_level >= required


# ---------------------------------------------------------------------------
# BucketedTeam — output of the aggregator, input to everything else.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BucketedTeam:
    """Aggregated team buff/debuff state with raw inputs preserved.

    All 8 team members (frontrow + backrow) contribute equally to the
    bucket math and the survivability verdict — passives fire from
    either row in CotC unless explicitly row-gated by the skill text.
    Row position is recorded for the embed header; some passives do
    require frontrow (e.g. Kilns's), which the classifier flags with
    ``requires_frontrow`` in a future iteration.

    Sub-bucket keys mirror the conventions used in
    ``damage/full_calc.py``: ``g1.<source_kind>.<stat>_<direction>``,
    ``g2.<source_kind>.<type>_dmg_up``, etc. Capping is **deferred** to
    ``damage/full_calc.py``'s 30%-per-sub-bucket logic — the aggregator
    feeds raw additive sums.

    ``team_damage_cap_up`` is in raw units (sum of team-wide cap-up
    effects plus ``cap_orbs * 100_000``). Self-scoped cap-up effects
    are kept in ``classified`` and applied per-DPS by the damage
    estimator, so a team-wide tally and a per-DPS tally can differ.

    ``classified`` and ``unparsed`` are kept separate so the audit CLI
    can surface skills that need new patterns without spelunking.
    """

    frontrow_form_ids: tuple[int, ...]          # length 0..4
    backrow_form_ids: tuple[int, ...]           # length 0..4
    pet_id: int | None
    divine_beast: bool
    cap_orbs: int                               # 0..3 free-orb slots
    raw_sub_bucket_sums: Mapping[str, float]
    team_damage_cap_up: float                   # raw units (team-wide only)
    team_skill_potency_up: float                # decimal fraction (team-wide only)
    team_soul_potency_up: float                 # decimal fraction (team-wide only)
    classified: tuple[ClassifiedEffect, ...]
    unparsed: tuple[ClassifiedEffect, ...]
    profile: AssumptionProfile

    @property
    def all_form_ids(self) -> tuple[int, ...]:
        """Both rows combined — what the bucket math actually iterates over."""
        return self.frontrow_form_ids + self.backrow_form_ids


# ---------------------------------------------------------------------------
# Survivability and coverage outputs.
# ---------------------------------------------------------------------------

# Tier values ordered from best to worst. Higher index = lower tier.
SURVIVABILITY_TIERS: tuple[str, ...] = (
    "Undying",
    "Full-party regen",
    "Frontrow regen",
    "Heal-only",
    "None",
)


@dataclass(frozen=True)
class SurvivabilityCitation:
    """One justification for a survivability tier verdict."""

    form_id: int
    skill_id: int
    snippet: str          # short excerpt from the skill description


@dataclass(frozen=True)
class SurvivabilityVerdict:
    tier: str                                       # one of SURVIVABILITY_TIERS
    primary_source_display: str                     # display name or '—'
    citations: tuple[SurvivabilityCitation, ...]


@dataclass(frozen=True)
class CoverageMatrix:
    """Per-bucket buff/debuff totals broken down for the embed."""

    g1: Mapping[str, float] = field(default_factory=dict)
    g2: Mapping[str, float] = field(default_factory=dict)
    g3: Mapping[str, float] = field(default_factory=dict)
    g4: Mapping[str, float] = field(default_factory=dict)
    g5: Mapping[str, float] = field(default_factory=dict)
    g6_active: bool = False


# ---------------------------------------------------------------------------
# Damage estimate per candidate DPS.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SkillDamageRow:
    """One row in the per-DPS skill summary."""

    skill_id: int
    skill_kind: str
    name: str | None
    power_min: int | None
    power_max: int | None
    hits: int | None


@dataclass(frozen=True)
class PerDpsDamageSummary:
    form_id: int
    display_name: str
    weapon: str | None                       # canonical lowercase, e.g. 'sword'
    element: str | None                      # canonical lowercase, e.g. 'fire'
    buff_multiplier: float                   # product of G1..G6 + final mults
    best_skills: tuple[SkillDamageRow, ...]  # top damage skills, by power×hits
    is_highlighted_dps: bool                 # True if user passed `dps=` for this form


# Cap tier thresholds — see ``buff_debuff/damage_cap_and_potency.md``.
CAP_TIER_GOOD = 100_000.0
CAP_TIER_SOSO = 50_000.0


def cap_tier_label(team_cap_up: float) -> str:
    """Map a total cap-up sum to one of 'Good' / 'So-so' / 'Low'."""
    if team_cap_up >= CAP_TIER_GOOD:
        return "Good"
    if team_cap_up >= CAP_TIER_SOSO:
        return "So-so"
    return "Low"


@dataclass(frozen=True)
class DamageReport:
    """Aggregated damage summary across all candidate DPS in the team."""

    per_dps: tuple[PerDpsDamageSummary, ...]
    team_damage_cap_up: float                # raw units
    cap_tier: str                            # 'Good' / 'So-so' / 'Low'
    team_skill_potency_up: float             # decimal fraction
    team_soul_potency_up: float              # decimal fraction
    g6_active: bool


# ---------------------------------------------------------------------------
# TeamReport — the final output the embed builder consumes.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TeamReport:
    bucketed: BucketedTeam
    survivability: SurvivabilityVerdict
    coverage: CoverageMatrix
    damage: DamageReport
