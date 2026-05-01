"""Team aggregator: form_ids + profile → BucketedTeam.

Pulls skills + A4 equipment for the active 4, runs the classifier over
each description, dedupes per ``buff_debuff/README.md`` rule 1
(same skill from same unit doesn't stack potency), then bins effects
into the six-group sub-bucket dictionary used by ``damage/full_calc.py``.

Reserves are stored on the BucketedTeam for the embed header but their
skills are not classified — inactive characters' passives don't fire in
CotC.
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from typing import Iterable

from damage.types import ELEMENTS, WEAPONS
from db import repo

from . import classifier
from .patterns import DAMAGE_CAP_PER_FREE_ORB
from .types import (
    AssumptionProfile,
    BucketedTeam,
    ClassifiedEffect,
)


# ---------------------------------------------------------------------------
# Public entry point.
# ---------------------------------------------------------------------------

_TEAM_WIDE_SCOPES: frozenset[str | None] = frozenset({
    None, "all_allies", "other_allies", "frontrow",
})


def aggregate_team(
    conn: sqlite3.Connection,
    *,
    frontrow_form_ids: Iterable[int],
    backrow_form_ids: Iterable[int] = (),
    pet_id: int | None = None,
    divine_beast: bool = False,
    cap_orbs: int = 0,
    profile: AssumptionProfile | None = None,
) -> BucketedTeam:
    """Build a :class:`BucketedTeam` from all 8 team members' skills + A4.

    Both rows contribute to passives, buffs, and survivability — passives
    fire regardless of row in CotC unless explicitly row-gated. Row
    position is preserved on the result for display; downstream consumers
    (damage estimator, survivability assessor) iterate over both rows.
    """
    profile = profile or AssumptionProfile()
    front = tuple(frontrow_form_ids)
    back = tuple(backrow_form_ids)
    all_form_ids = list(front + back)

    skills = repo.skills_for_forms(conn, all_form_ids)
    equipment = repo.equipment_for_forms(conn, all_form_ids)

    classified: list[ClassifiedEffect] = []
    unparsed: list[ClassifiedEffect] = []

    for row in skills:
        for eff in classifier.classify_skill(row, form_id=row["form_id"]):
            (unparsed if eff.confidence == "unparsed" else classified).append(eff)
    for row in equipment:
        for eff in classifier.classify_equipment(row, form_id=row["form_id"]):
            (unparsed if eff.confidence == "unparsed" else classified).append(eff)

    # Filter out conditional effects whose boost gate the profile doesn't meet.
    classified = [
        e for e in classified
        if profile.includes_boost(e.boost_required)
    ]

    deduped = _dedupe_same_skill(classified)

    sub_bucket_sums = _bin_into_sub_buckets(deduped)

    # Team-wide tallies only — self-scoped cap-up / potency-up live on
    # ``classified`` and are pulled per-DPS by the damage estimator.
    cap_up_from_orbs = float(max(0, min(cap_orbs, 3, len(all_form_ids)))) * DAMAGE_CAP_PER_FREE_ORB
    cap_up_team_wide = sum(
        e.magnitude for e in deduped
        if e.category == "damage_cap_up" and e.target_scope in _TEAM_WIDE_SCOPES
    )
    skill_potency_team_wide = sum(
        e.magnitude for e in deduped
        if e.category == "skill_potency_up" and e.target_scope in _TEAM_WIDE_SCOPES
    )
    soul_potency_team_wide = sum(
        e.magnitude for e in deduped
        if e.category == "soul_potency_up" and e.target_scope in _TEAM_WIDE_SCOPES
    )

    return BucketedTeam(
        frontrow_form_ids=front,
        backrow_form_ids=back,
        pet_id=pet_id,
        divine_beast=divine_beast,
        cap_orbs=cap_orbs,
        raw_sub_bucket_sums=dict(sub_bucket_sums),
        team_damage_cap_up=cap_up_from_orbs + cap_up_team_wide,
        team_skill_potency_up=skill_potency_team_wide,
        team_soul_potency_up=soul_potency_team_wide,
        classified=tuple(deduped),
        unparsed=tuple(unparsed),
        profile=profile,
    )


# ---------------------------------------------------------------------------
# Internal helpers.
# ---------------------------------------------------------------------------

def _dedupe_same_skill(
    effects: list[ClassifiedEffect],
) -> list[ClassifiedEffect]:
    """Apply the same-skill rule from ``buff_debuff/README.md`` rule 1.

    Two effects with identical
    ``(source_form_id, source_skill_id, category, targets, direction)``
    don't stack potency; the higher magnitude wins. This handles the
    case where a parser-emitted skill description is re-classified
    twice (e.g. via repeated tier rows for an ultimate) without
    inflating the bucket sum.
    """
    best: dict[tuple, ClassifiedEffect] = {}
    for e in effects:
        if e.source_kind == "ultimate":
            # The sheet stores multiple ultimate levels as separate rows.
            # For a team-analysis ceiling, take the strongest tier instead
            # of summing Lv.1/Lv.9/Lv.10 copies of the same effect.
            key = (
                e.source_form_id, "ultimate-tier",
                e.category, e.targets, e.direction, e.target_scope,
            )
        else:
            key = (
                e.source_form_id, e.source_skill_id,
                e.category, e.targets, e.direction,
            )
        existing = best.get(key)
        if existing is None or e.magnitude > existing.magnitude:
            best[key] = e
    return list(best.values())


def _bin_into_sub_buckets(
    effects: list[ClassifiedEffect],
) -> dict[str, float]:
    """Sum classified effects into ``g{N}.<source>.<sub>`` keyed totals.

    Mirrors ``damage/full_calc.py`` key conventions so that the damage
    estimator can call ``additive_group`` / ``multiplicative_group``
    directly on the result.
    """
    sums: dict[str, float] = defaultdict(float)
    for e in effects:
        for key in _keys_for_effect(e):
            sums[key] += e.magnitude
    return sums


def _keys_for_effect(e: ClassifiedEffect) -> list[str]:
    """Return the sub-bucket key(s) an effect contributes to.

    Most effects contribute to one key. Umbrella targets fan out across
    every per-type sub-bucket per ``buff_debuff/README.md`` rule 4.
    """
    src = _bucket_source(e.source_kind)
    if e.category == "stat_up" or e.category == "stat_down":
        # G1 — Stats. ``targets`` carries the stat name (atk/mag/def/mdef).
        direction = "up" if e.category == "stat_up" else "down"
        return [
            f"g1.{src}.{t}_{direction}"
            for t in (e.targets or ("atk",))
        ]
    if e.category == "dmg_up":
        return _expand_typed(e, group="g2", suffix="dmg_up", source=src)
    if e.category == "res_down":
        return _expand_typed(e, group="g3", suffix="res_down", source=src)
    if e.category == "crit_up":
        return [f"g1.{src}.crit_up"]
    # Final-multiplier categories don't go in G1..G6 buckets; the
    # aggregator pulls them out separately when building the BucketedTeam.
    if e.category in {
        "crit_dmg_up", "soul_potency_up", "skill_potency_up",
        "damage_cap_up",
    }:
        return []
    # Survivability-only categories don't influence the offensive sum.
    if e.category in {"regen", "heal", "undying", "shield", "cleanse"}:
        return []
    return []


def _bucket_source(source_kind: str) -> str:
    """Map a skill ``kind`` onto its bucket source label.

    Per ``buff_debuff/README.md``:
      - active / divine / ex                 -> 'active'
      - passive / latent / tp_passive / equipment -> 'passive'
      - ultimate                            -> 'ultimate'
    """
    if source_kind == "ultimate":
        return "ultimate"
    if source_kind in {"passive", "latent", "tp_passive", "equipment"}:
        return "passive"
    return "active"


def _expand_typed(
    e: ClassifiedEffect, *, group: str, suffix: str, source: str,
) -> list[str]:
    """Turn an effect's ``targets`` (and umbrellas) into bucket keys."""
    out: list[str] = []
    for t in e.targets:
        if t == "umbrella:physical":
            for w in WEAPONS:
                out.append(f"{group}.{source}.{w}_{suffix}")
        elif t == "umbrella:elemental":
            for el in ELEMENTS:
                out.append(f"{group}.{source}.{el}_{suffix}")
        elif t == "umbrella:all":
            for tt in WEAPONS + ELEMENTS:
                out.append(f"{group}.{source}.{tt}_{suffix}")
        else:
            out.append(f"{group}.{source}.{t}_{suffix}")
    return out
