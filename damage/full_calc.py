"""Full 6-group bucket model + final multipliers.

Models the canonical taxonomy in ``buff_debuff/README.md``: G1 Stats,
G2 DMG Up, G3 Res Down, G4 Ultimate (three multiplying sub-pools), G5
Pets (two multiplying sub-pools), G6 Divine Beast, plus the four final
multipliers (Crit, Hell/Heaven/Living World, Soul Potency, Skill
Potency). Defensive bucket math is applied on the defender side.

The spreadsheet calc in :mod:`damage.spreadsheet_calc` is a strict
subset of this model — same group structure, lumped per-type
sub-buckets, no G6, no final multipliers, no defender. The parity
test in ``tests/test_damage.py`` proves the reduction.
"""

from __future__ import annotations

from typing import Mapping, Optional

from .types import DEFAULT_SUB_BUCKET_CAP, ELEMENTS, WEAPONS


def cap_sub_bucket(
    sub_bucket_sum: float, cap: float = DEFAULT_SUB_BUCKET_CAP
) -> float:
    """Clamp a sub-bucket's total to its cap. Default cap is 30%."""
    return min(sub_bucket_sum, cap)


def additive_group(
    sub_bucket_sums: Optional[Mapping[str, float]] = None,
    caps: Optional[Mapping[str, float]] = None,
    default_cap: float = DEFAULT_SUB_BUCKET_CAP,
) -> float:
    """Return ``1 + Σ min(sum, cap)`` over each sub-bucket.

    Used for fully-additive groups: G1 (Stats), G2 (DMG Up matching
    the attack type), G3 (Res Down matching the attack type). Per
    ``buff_debuff/README.md``: each sub-bucket is capped independently,
    then the capped values sum into the group.

    Pass ``default_cap=float('inf')`` to disable capping (e.g., to
    replicate the V1.1 spreadsheet, which trusts caller-pre-capped
    inputs).
    """
    if not sub_bucket_sums:
        return 1.0
    cap_overrides = caps or {}
    total = 0.0
    for key, raw_sum in sub_bucket_sums.items():
        cap = cap_overrides.get(key, default_cap)
        total += min(raw_sum, cap)
    return 1.0 + total


def multiplicative_group(
    stats_sums: Optional[Mapping[str, float]] = None,
    dmg_up_sums: Optional[Mapping[str, float]] = None,
    res_down_sums: Optional[Mapping[str, float]] = None,
    caps: Optional[Mapping[str, float]] = None,
    default_cap: float = DEFAULT_SUB_BUCKET_CAP,
) -> float:
    """Three sub-pools multiplying: Stats × DMG Up × Res Down.

    Used for G4 (Ultimate) and G5 (Pets). Pass ``res_down_sums=None``
    for G5, where Pet Res Down is theoretical and absent in the live
    roster.
    """
    stats = additive_group(stats_sums, caps, default_cap)
    dmg = additive_group(dmg_up_sums, caps, default_cap)
    res = additive_group(res_down_sums, caps, default_cap)
    return stats * dmg * res


def apply_umbrella(
    target_sums: dict[str, float],
    kind: str,
    magnitude: float,
    key_template: str,
) -> None:
    """Spread an umbrella buff over its per-type sub-buckets in place.

    Per ``buff_debuff/README.md`` rule 4: ``All Damage Up X%``,
    ``Physical Damage Up X%``, and ``Elemental Damage Up X%`` add
    ``X%`` to **every** relevant per-type sub-bucket independently;
    each sub-bucket caps on its own.

    ``kind`` is one of ``"physical"`` (8 weapons), ``"elemental"`` (6
    elements), or ``"all"`` (14 types). ``key_template`` is a format
    string with a ``{type}`` placeholder, e.g.
    ``"g2.active.{type}_dmg_up"``.
    """
    if kind == "physical":
        types = WEAPONS
    elif kind == "elemental":
        types = ELEMENTS
    elif kind == "all":
        types = WEAPONS + ELEMENTS
    else:
        raise ValueError(
            f"unknown umbrella kind: {kind!r} "
            "(expected 'physical', 'elemental', or 'all')"
        )
    for t in types:
        key = key_template.format(type=t)
        target_sums[key] = target_sums.get(key, 0.0) + magnitude


def crit_multiplier(crit_active: bool, crit_damage_up_sum: float = 0.0) -> float:
    """Crit final multiplier: ``1.25 + Σ Crit Damage Up`` when active.

    Crit chance Up lives in G1 (capped 30%); only Crit Damage Up
    contributes here, and it is uncapped.
    """
    if not crit_active:
        return 1.0
    return 1.25 + crit_damage_up_sum


def alignment_multiplier(matching_alignment_up_sum: float = 0.0) -> float:
    """Hell / Heaven / Living World final multiplier.

    Triggered when the attacker's weapon alignment matches the enemy's
    alignment. The ``matching_alignment_up_sum`` is the additive total
    of all matching ``damage up vs [alignment]`` attributes. Hell
    weapons cap at 200% (caller responsibility — see edge_cases.md).
    """
    return 1.0 + matching_alignment_up_sum


def soul_potency_multiplier(soul_potency_up_sum: float = 0.0) -> float:
    """Soul Potency final multiplier (uncapped, additive sum)."""
    return 1.0 + soul_potency_up_sum


def skill_potency_multiplier(skill_potency_up_sum: float = 0.0) -> float:
    """Skill Potency final multiplier (uncapped, additive sum)."""
    return 1.0 + skill_potency_up_sum


def divine_beast_multiplier(active: bool) -> float:
    """G6 — flat ``1.10`` when the Divine Beast is active, else ``1.0``."""
    return 1.10 if active else 1.0


def effective_damage(
    base_term: float,
    g1: float = 1.0,
    g2: float = 1.0,
    g3: float = 1.0,
    g4: float = 1.0,
    g5: float = 1.0,
    g6: float = 1.0,
    crit: float = 1.0,
    hell_heaven_lw: float = 1.0,
    soul_potency: float = 1.0,
    skill_potency: float = 1.0,
    defender_g1: float = 1.0,
    defender_g2: float = 1.0,
    defender_g3: float = 1.0,
    defender_g4: float = 1.0,
    defender_g5: float = 1.0,
) -> float:
    """Compose group products + final multipliers + defender into damage.

    Per the formula in ``buff_debuff/README.md``::

        damage = base
               × G1 × G2 × G3 × G4 × G5 × G6
               × Crit × HellHeavenLW × SoulPotency × SkillPotency

    plus defender-side division (rule 9 in the README).
    """
    attacker = (
        base_term
        * g1
        * g2
        * g3
        * g4
        * g5
        * g6
        * crit
        * hell_heaven_lw
        * soul_potency
        * skill_potency
    )
    defender = defender_g1 * defender_g2 * defender_g3 * defender_g4 * defender_g5
    return attacker / defender
