"""Damage estimate — per-candidate-DPS multiplier and cap-up summary.

For each candidate DPS in the team's 8 members (both rows can swap
forward), this module computes:

  - **Buff multiplier**: G1..G6 product × final multipliers (Crit, Hell/
    Heaven/Living World, Soul Potency, Skill Potency), built from the
    aggregator's sub-bucket sums. Per-type sub-buckets are filtered by
    the DPS's own weapon and element so a Sword DPS doesn't get credit
    for Axe DMG Up.
  - **Best skills**: highest-power damage skills among those with
    **effective hits ≥ 4** — where ``effective_hits = listed_hits ×
    self_multi_cast_factor``. Single-cast skills with ≤ 3 hits are
    filtered out: per the cap math, even if every hit reaches cap they
    don't accumulate enough total damage to compete with multi-hit
    skills under the same buff stack.
  - **Cap-up that benefits this DPS** (computed at report time):
      * ``team_damage_cap_up`` (orbs + scope ∈ {None, all_allies,
        other_allies, frontrow}).
      * ``self_damage_cap_up`` (effects with ``target_scope='self'``
        whose ``source_form_id`` matches this DPS).
  - **Skill potency / soul potency that benefits this DPS** (same
    scope split as cap-up).
  - **Self-multi-cast factor** active on this DPS (1.0 default, ≥2.0
    if the DPS has classified ``multi_cast`` effects on themselves).

Whether each hit reaches the per-hit cap depends on potency and the
team's cap-up tier — see ``buff_debuff/damage_cap_and_potency.md``.
"""
from __future__ import annotations

import re
import sqlite3

from damage import full_calc
from damage.types import ELEMENTS, WEAPONS

from . import coverage
from .types import (
    BucketedTeam,
    ClassifiedEffect,
    DamageReport,
    PerDpsDamageSummary,
    SkillDamageRow,
    cap_tier_label,
)
from db import repo
from .patterns import DAMAGE_CAP_PER_FREE_ORB


_TEAM_WIDE_SCOPES: frozenset[str | None] = frozenset({
    None, "all_allies", "other_allies", "frontrow",
})

# Per ``buff_debuff/damage_cap_and_potency.md``: skills with effective
# hits below this count are not damage-relevant — even at cap on every
# hit, total damage doesn't compete with higher-hit skills.
MIN_DAMAGE_RELEVANT_EFFECTIVE_HITS: int = 4

# Per ``buff_debuff/damage_cap_and_potency.md``: realised potency
# (raw potency × (1 + skill_potency_up)) at or above this means each
# hit can reach cap once the team has Good cap-up.
POTENCY_TO_REACH_CAP: float = 240.0

_RE_REPEAT_UPTO = re.compile(r"repeat this attack once \(up to (\d+)x\)")
_RE_HIT_COUNT = re.compile(
    r"\b(\d+)\s*x\s+(?:single-target|random-target|aoe|st|rt)\b",
    re.IGNORECASE,
)
_RE_POWER_RANGE = re.compile(
    r"\((\d+)x\s*(\d+)(?:\s*[~\-]\s*(\d+))?\s*Power",
    re.IGNORECASE,
)
_TYPE_TOKENS = "|".join(WEAPONS + ELEMENTS)
_RE_ATTACK_TYPE = re.compile(
    rf"\b(?:\d+\s*x|counterattack)\b[^,\n(]*?\b({_TYPE_TOKENS})\b",
    re.IGNORECASE,
)
_RE_DISPLAY_LABEL = re.compile(
    rf"\b(?P<hits>\d+\s*x)\s+(?P<shape>single-target|random-target|aoe|st|rt)?\s*"
    rf"(?P<type>{_TYPE_TOKENS})\b",
    re.IGNORECASE,
)


def build(
    bucketed: BucketedTeam,
    conn: sqlite3.Connection,
    *,
    highlighted_dps: int | None = None,
) -> DamageReport:
    """Build the team's :class:`DamageReport` — one row per candidate DPS.

    Per the user's design, all 8 team members are candidate DPS (both
    rows can swap forward). Members whose strongest damage skill has
    effective hits < 4 are still listed but their best-skill row will
    be empty, surfacing the "no damage-relevant skills" state.
    """
    per_dps: list[PerDpsDamageSummary] = []
    for form_id in bucketed.all_form_ids:
        per_dps.append(_summary_for_dps(
            bucketed, conn, form_id,
            highlighted=(form_id == highlighted_dps),
        ))

    return DamageReport(
        per_dps=tuple(per_dps),
        team_damage_cap_up=bucketed.team_damage_cap_up,
        cap_tier=cap_tier_label(bucketed.team_damage_cap_up),
        team_skill_potency_up=bucketed.team_skill_potency_up,
        team_soul_potency_up=bucketed.team_soul_potency_up,
        g6_active=bucketed.divine_beast,
    )


# ---------------------------------------------------------------------------
# Per-DPS summary.
# ---------------------------------------------------------------------------

def _summary_for_dps(
    bucketed: BucketedTeam,
    conn: sqlite3.Connection,
    form_id: int,
    *,
    highlighted: bool,
) -> PerDpsDamageSummary:
    form = repo.get_form(conn, form_id)
    display_name = form["display_name"] if form else f"form#{form_id}"

    multi_cast = self_multi_cast_factor(bucketed, form_id)
    best_skills = _best_skills_for(conn, form_id, multi_cast=multi_cast)
    weapon = best_skills[0].weapon if best_skills and best_skills[0].weapon else _affinity_label(conn, form_id, kind="weapon")
    element = best_skills[0].element if best_skills and best_skills[0].element else _affinity_label(conn, form_id, kind="element")
    multiplier = _buff_multiplier_for(
        bucketed, weapon=weapon, element=element, dps_form_id=form_id,
    )

    return PerDpsDamageSummary(
        form_id=form_id,
        display_name=display_name,
        weapon=weapon,
        element=element,
        buff_multiplier=multiplier,
        best_skills=tuple(best_skills),
        is_highlighted_dps=highlighted,
    )


def _affinity_label(
    conn: sqlite3.Connection, form_id: int, *, kind: str,
) -> str | None:
    """Return the lowercase ``icon_label`` for one affinity kind, if any.

    Falls back to the parent character's ``base_weapon`` for
    ``kind='weapon'`` when no explicit weapon affinity is set, matching
    how /character renders.
    """
    row = conn.execute(
        "SELECT icon_label FROM character_affinities "
        "WHERE form_id = ? AND kind = ? LIMIT 1",
        (form_id, kind),
    ).fetchone()
    if row and row[0]:
        return str(row[0]).lower()
    if kind == "weapon":
        row = conn.execute(
            "SELECT c.base_weapon FROM character_forms f "
            "JOIN characters c ON c.id = f.character_id "
            "WHERE f.id = ?",
            (form_id,),
        ).fetchone()
        if row and row[0]:
            return str(row[0]).lower()
    return None


def cap_up_breakdown_for_dps(
    bucketed: BucketedTeam, dps_form_id: int,
) -> tuple[float, float]:
    """Split team cap-up into (team_wide, self_only) for one DPS."""
    team_wide = float(bucketed.effective_cap_orbs) * DAMAGE_CAP_PER_FREE_ORB
    self_only = 0.0
    for e in bucketed.classified:
        if e.category != "damage_cap_up":
            continue
        if e.target_scope == "self":
            if e.source_form_id == dps_form_id:
                self_only += e.magnitude
        elif e.target_scope in _TEAM_WIDE_SCOPES:
            team_wide += e.magnitude
    return team_wide, self_only


def potency_up_breakdown_for_dps(
    bucketed: BucketedTeam, dps_form_id: int,
) -> tuple[float, float]:
    """Split skill_potency_up into (team_wide, self_only) for one DPS.

    Mirrors ``cap_up_breakdown_for_dps`` shape: self-scoped potency-up
    only counts when this DPS is the originating form.
    """
    team_wide = bucketed.team_skill_potency_up
    self_only = 0.0
    for e in bucketed.classified:
        if e.category != "skill_potency_up":
            continue
        if e.target_scope == "self" and e.source_form_id == dps_form_id:
            self_only += e.magnitude
    return team_wide, self_only


def self_multi_cast_factor(
    bucketed: BucketedTeam, dps_form_id: int,
) -> float:
    """Return the multi-cast factor active on this DPS (≥ 1.0).

    Multi-cast is always self-scoped today (per design decision); each
    DPS only benefits from their own multi-cast effects. If a DPS has
    multiple multi-cast sources, the highest factor wins (matching the
    same-skill-rule intent — multiple multi-cast sources don't stack).
    """
    factor = 1.0
    for e in bucketed.classified:
        if e.category != "multi_cast":
            continue
        if e.target_scope == "self" and e.source_form_id == dps_form_id:
            if e.magnitude > factor:
                factor = e.magnitude
        elif e.target_scope in _TEAM_WIDE_SCOPES and e.target_scope != "self":
            # Forwards-compatible: if a future skill grants multi-cast to
            # all allies, every DPS benefits.
            if e.magnitude > factor:
                factor = e.magnitude
    return factor


# ---------------------------------------------------------------------------
# Buff multiplier.
# ---------------------------------------------------------------------------

def _buff_multiplier_for(
    bucketed: BucketedTeam, *, weapon: str | None,
    element: str | None, dps_form_id: int,
) -> float:
    """Compose G1..G6 + final multipliers for a DPS with given weapon/element."""
    sums = bucketed.raw_sub_bucket_sums

    g1 = full_calc.additive_group(_g1_keys_for_attack_type(sums, weapon, element))
    g2 = full_calc.additive_group(
        _keys_for_attack_type(sums, "g2", weapon, element, "dmg_up")
    )
    g3 = full_calc.additive_group(
        _keys_for_attack_type(sums, "g3", weapon, element, "res_down")
    )
    g4 = full_calc.multiplicative_group(
        stats_sums=_keys_with_prefix(sums, "g4.ultimate.")
        if any(k.startswith("g4.ultimate.") for k in sums) else None,
    )
    g5 = full_calc.multiplicative_group(
        stats_sums=_keys_with_prefix(sums, "g5.")
        if any(k.startswith("g5.") for k in sums) else None,
    )
    g6 = full_calc.divine_beast_multiplier(bucketed.divine_beast)

    skill_team, skill_self = potency_up_breakdown_for_dps(bucketed, dps_form_id)
    skill = full_calc.skill_potency_multiplier(skill_team + skill_self)
    soul = full_calc.soul_potency_multiplier(bucketed.team_soul_potency_up)
    # Crit gating defaults to off — phase 2 may detect "Guaranteed Crit"
    # passives on the DPS and flip this on per-summary.
    crit = full_calc.crit_multiplier(crit_active=False)
    alignment = full_calc.alignment_multiplier()

    return g1 * g2 * g3 * g4 * g5 * g6 * crit * alignment * soul * skill


def final_multiplier_for_type(bucketed: BucketedTeam, attack_type: str) -> float:
    """Team-wide final multiplier for a hypothetical weapon/element type.

    This powers the coverage matrix. It intentionally excludes self-only
    potency and cap effects because there is no specific DPS attached to
    a type cell.
    """
    attack_type = (attack_type or "").lower()
    weapon = attack_type if attack_type in WEAPONS else None
    element = attack_type if attack_type in ELEMENTS else None
    return _buff_multiplier_for(
        bucketed,
        weapon=weapon,
        element=element,
        dps_form_id=-1,
    )


def _keys_with_prefix(
    sums: dict[str, float] | object, prefix: str,
) -> dict[str, float]:
    return {k: v for k, v in sums.items() if k.startswith(prefix)}


def _g1_keys_for_attack_type(
    sums: dict[str, float] | object,
    weapon: str | None,
    element: str | None,
) -> dict[str, float]:
    """Relevant G1 offensive terms for a weapon or elemental attack.

    Weapon attacks benefit from Atk Up and enemy Def Down. Elemental
    attacks benefit from Mag Up and enemy MDef Down. Defensive buffs,
    enemy Atk/Mag Down, and crit chance are rendered in the matrix but do
    not multiply the baseline damage estimate here.
    """
    wanted = {"atk_up", "def_down"} if weapon else {"mag_up", "mdef_down"} if element else set()
    if not wanted:
        return {}
    return {
        k: v for k, v in sums.items()
        if k.startswith("g1.") and k.split(".")[-1] in wanted
    }


def _keys_for_attack_type(
    sums: dict[str, float] | object,
    group: str,
    weapon: str | None,
    element: str | None,
    suffix: str,
) -> dict[str, float]:
    """Filter a group's sub-buckets to those matching the DPS's type."""
    keys: dict[str, float] = {}
    types = {t for t in (weapon, element) if t}
    if not types:
        return keys
    for k, v in sums.items():
        if not k.startswith(f"{group}."):
            continue
        if not k.endswith(f"_{suffix}"):
            continue
        parts = k.split(".")
        if len(parts) != 3:
            continue
        type_token = parts[2][: -(len(suffix) + 1)]
        if type_token in types:
            keys[k] = v
    return keys


# ---------------------------------------------------------------------------
# Best-skills picker.
# ---------------------------------------------------------------------------

_DAMAGE_KINDS: frozenset[str] = frozenset({"active", "divine", "ex", "ultimate"})


def _best_skills_for(
    conn: sqlite3.Connection, form_id: int,
    *,
    multi_cast: float = 1.0,
    top: int = 3,
) -> list[SkillDamageRow]:
    """Top damage-relevant skills for a DPS.

    Only skills with **effective_hits ≥ 4** are kept. Effective hits
    is ``listed_hits × multi_cast`` — so a 2-hit skill on a triplecasting
    unit has effective_hits = 6 and qualifies; a 1-hit skill on the
    same unit has effective_hits = 3 and is dropped. Per the cap math
    (`buff_debuff/damage_cap_and_potency.md`), low-hit skills can't
    reach competitive total damage even at cap.
    """
    rows = conn.execute(
        "SELECT id, name, kind, power_min, power_max, hits, description "
        "FROM skills "
        "WHERE form_id = ? "
        "ORDER BY id ASC",
        (form_id,),
    ).fetchall()
    candidates: list[tuple[float, SkillDamageRow]] = []
    for r in rows:
        if (r["kind"] or "") not in _DAMAGE_KINDS:
            continue
        description = r["description"] or ""
        power_min, power_max = _skill_power_range(
            description,
            power_min=r["power_min"],
            power_max=r["power_max"],
        )
        listed_hits = _skill_hit_count(description, hits=r["hits"])
        repeat_factor = _skill_repeat_factor(r["description"] or "")
        effective_hits = float(listed_hits) * multi_cast * repeat_factor
        if effective_hits < MIN_DAMAGE_RELEVANT_EFFECTIVE_HITS:
            continue
        weapon, element = _skill_attack_type(description)
        skill = SkillDamageRow(
            skill_id=r["id"],
            skill_kind=r["kind"] or "active",
            name=r["name"] or _skill_display_label(description),
            power_min=power_min,
            power_max=power_max,
            hits=listed_hits,
            weapon=weapon,
            element=element,
            repeat_factor=repeat_factor,
        )
        score = float(power_max or 0) * effective_hits
        candidates.append((score, skill))
    candidates.sort(
        key=lambda item: (
            item[0],
            item[1].power_max or 0,
            item[1].hits or 0,
            -item[1].skill_id,
        ),
        reverse=True,
    )
    return [skill for _score, skill in candidates[:top]]


def _skill_repeat_factor(description: str) -> float:
    """Return built-in repeats from a damage skill's own text.

    This is separate from self multi-cast buffs. For example Pardis's
    "repeat this attack once (up to 3x)" is one listed hit with up to
    three repeats, so the skill itself contributes four effective hits
    before his ultimate double-cast is applied.
    """
    text = " ".join((description or "").split()).lower()
    m = _RE_REPEAT_UPTO.search(text)
    if m:
        return float(int(m.group(1)) + 1)
    if "repeat this attack once" in text or "cast this a second time in a row" in text:
        return 2.0
    return 1.0


def _skill_hit_count(description: str, *, hits: int | None) -> int:
    """Best available hit count under optimistic conditional assumptions."""
    candidates = [int(hits or 0)]
    for m in _RE_HIT_COUNT.finditer(description or ""):
        candidates.append(int(m.group(1)))
    return max(candidates) if candidates else 0


def _skill_power_range(
    description: str,
    *,
    power_min: int | None,
    power_max: int | None,
) -> tuple[int | None, int | None]:
    """Use parsed DB power columns, falling back to the description."""
    if power_max is not None:
        return power_min, power_max
    m = _RE_POWER_RANGE.search(description or "")
    if not m:
        return power_min, power_max
    parsed_min = int(m.group(2))
    parsed_max = int(m.group(3) or m.group(2))
    return parsed_min, parsed_max


def _skill_attack_type(description: str) -> tuple[str | None, str | None]:
    """Infer the primary attack type from a skill description."""
    m = _RE_ATTACK_TYPE.search(description or "")
    if not m:
        return None, None
    token = m.group(1).lower()
    if token in WEAPONS:
        return token, None
    if token in ELEMENTS:
        return None, token
    return None, None


def _skill_display_label(description: str) -> str | None:
    """Build a compact label for sheet rows that have no skill name."""
    text = " ".join((description or "").split())
    m = _RE_DISPLAY_LABEL.search(text)
    if not m:
        return None
    parts = [m.group("hits").lower()]
    if m.group("shape"):
        shape = m.group("shape").lower()
        parts.append({"aoe": "AoE", "st": "ST", "rt": "RT"}.get(shape, shape.title()))
    parts.append(m.group("type").title())
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Cap-reached heuristic.
# ---------------------------------------------------------------------------

def realised_potency(power: int | None, skill_potency_up: float) -> float:
    """Compute realised per-hit potency from raw power + potency_up."""
    return float(power or 0) * (1.0 + max(0.0, skill_potency_up))


def caps_each_hit(
    *,
    power: int | None,
    skill_potency_up: float,
    team_damage_cap_up: float,
) -> bool:
    """240-potency rule + Good cap tier — a quick yes/no per skill."""
    if power is None:
        return False
    if cap_tier_label(team_damage_cap_up) != "Good":
        return False
    return realised_potency(power, skill_potency_up) >= POTENCY_TO_REACH_CAP


def effective_hits(listed_hits: int | None, multi_cast: float) -> int:
    """``listed_hits * multi_cast`` rounded down to int (display only)."""
    return int(float(listed_hits or 0) * multi_cast)


def effective_hits_for_skill(skill: SkillDamageRow, multi_cast: float) -> int:
    """Listed hits times skill-native repeats and self multi-cast."""
    return int(float(skill.hits or 0) * max(1.0, skill.repeat_factor) * multi_cast)


# Default per-hit damage cap before any Damage Cap Up — the in-game ceiling
# every hit is clamped to before stacking. See
# ``buff_debuff/damage_cap_and_potency.md``.
BASE_PER_HIT_CAP: float = 999_999.0


def total_damage_estimate(
    *,
    effective_hits: int,
    realised_potency: float,
    total_cap_up: float,
    caps_each_hit: bool,
    base_per_hit_cap: float = BASE_PER_HIT_CAP,
) -> tuple[float, int]:
    """Estimate total damage this DPS deals in one cast.

    Returns ``(estimated_total_damage, hits_at_cap)``.

    ``total_cap_up`` is **per-DPS**: ``team_wide + self_only``. Some
    Damage Cap Up sources (skill conditionals, A4 accessories) only
    apply to the originating character, so two attackers on the same
    team can have different per-hit ceilings — Black Knight with his
    EX self-cap-up has a 1.7M+ ceiling while a teammate without it
    sits at the 1.36M team-wide ceiling. Callers compute the value
    via :func:`cap_up_breakdown_for_dps` and sum.

    The per-hit ceiling is ``base_per_hit_cap + total_cap_up``. When
    the 240-potency rule says the team caps each hit, total damage is
    exactly ``effective_hits * per_hit_cap``. When the team does NOT
    cap, per-hit damage is approximated linearly via
    ``realised_potency / 240`` — conservative under the rule-of-thumb
    in ``buff_debuff/damage_cap_and_potency.md``. The audit/embed
    output labels this an estimate so users don't read it as exact.
    """
    if effective_hits <= 0:
        return 0.0, 0
    per_hit_cap = base_per_hit_cap + max(0.0, total_cap_up)
    if caps_each_hit:
        return float(effective_hits) * per_hit_cap, int(effective_hits)
    quotient = max(0.0, min(1.0, realised_potency / POTENCY_TO_REACH_CAP))
    return float(effective_hits) * per_hit_cap * quotient, 0
