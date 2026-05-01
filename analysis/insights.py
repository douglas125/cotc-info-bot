"""Actionable summary layer for team analysis reports."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from . import damage_estimate
from .types import (
    BucketedTeam,
    ClassifiedEffect,
    DamageReport,
    PerDpsDamageSummary,
    SkillDamageRow,
)


@dataclass(frozen=True)
class DpsInsight:
    summary: PerDpsDamageSummary
    best_skill: SkillDamageRow
    effective_hits: int
    realised_potency: float
    caps_each_hit: bool
    team_cap_up: float
    self_cap_up: float
    team_potency_up: float
    self_potency_up: float
    score: float

    @property
    def total_cap_up(self) -> float:
        """Per-DPS damage-cap-up: team-wide plus this character's self-only.

        Self-only accessories and skill conditionals raise the per-hit
        ceiling for THIS character only — the value is intentionally
        per-DPS, not a team aggregate.
        """
        return self.team_cap_up + self.self_cap_up

    @property
    def total_potency_up(self) -> float:
        return self.team_potency_up + self.self_potency_up

    @property
    def estimated_total_damage(self) -> float:
        total, _hits_at_cap = damage_estimate.total_damage_estimate(
            effective_hits=self.effective_hits,
            realised_potency=self.realised_potency,
            total_cap_up=self.total_cap_up,
            caps_each_hit=self.caps_each_hit,
        )
        return total

    @property
    def per_hit_cap(self) -> float:
        return damage_estimate.BASE_PER_HIT_CAP + max(0.0, self.total_cap_up)


def ranked_dps(bucketed: BucketedTeam, damage: DamageReport, *, limit: int = 3) -> list[DpsInsight]:
    """Return only the team members with real damage evidence."""
    out: list[DpsInsight] = []
    for dps in damage.per_dps:
        if not dps.best_skills:
            continue
        best = dps.best_skills[0]
        multi_cast = damage_estimate.self_multi_cast_factor(bucketed, dps.form_id)
        eff_hits = damage_estimate.effective_hits_for_skill(best, multi_cast)
        team_cap, self_cap = damage_estimate.cap_up_breakdown_for_dps(bucketed, dps.form_id)
        team_pot, self_pot = damage_estimate.potency_up_breakdown_for_dps(bucketed, dps.form_id)
        realised = damage_estimate.realised_potency(best.power_max, team_pot + self_pot)
        caps = damage_estimate.caps_each_hit(
            power=best.power_max,
            skill_potency_up=team_pot + self_pot,
            team_damage_cap_up=team_cap + self_cap,
        )
        score = dps.buff_multiplier * float(best.power_max or 0) * float(eff_hits)
        out.append(DpsInsight(
            summary=dps,
            best_skill=best,
            effective_hits=eff_hits,
            realised_potency=realised,
            caps_each_hit=caps,
            team_cap_up=team_cap,
            self_cap_up=self_cap,
            team_potency_up=team_pot,
            self_potency_up=self_pot,
            score=score,
        ))
    out.sort(key=lambda x: (x.summary.is_highlighted_dps, x.score), reverse=True)
    return out[:limit]


def support_summaries(
    bucketed: BucketedTeam,
    damage: DamageReport,
    *,
    exclude: Iterable[int],
    max_effects_per_member: int = 4,
) -> list[str]:
    """Quantified per-member support contributions for non-DPS roles.

    Replaces the old tag-cloud labels (``"buff/debuff support, damage cap
    support, potency support"``) with concrete effect lines built from
    each member's own classified effects, e.g.
    ``"Solon: +100% Potency Up (single ally), +100k cap (self), +30% Atk Up (active)"``.

    The top ``max_effects_per_member`` effects per character (by ranked
    magnitude) are kept to fit the 1024-char field budget. Members with
    no support classification fall back to ``"damage skill available"``
    or ``"no parsed team role"`` matching the old behaviour.
    """
    excluded = set(exclude)
    names = {d.form_id: d.display_name for d in damage.per_dps}
    form_ids = [fid for fid in bucketed.all_form_ids if fid not in excluded]
    lines: list[str] = []
    for fid in form_ids:
        effects = _support_effects(bucketed, fid)
        ranked = _rank_effects(effects)[:max_effects_per_member]
        if ranked:
            joined = ", ".join(format_classified_effect(e) for e in ranked)
            lines.append(f"{names.get(fid, f'form#{fid}')}: {joined}")
            continue
        dps = next((d for d in damage.per_dps if d.form_id == fid), None)
        fallback = "damage skill available" if dps and dps.best_skills else "no parsed team role"
        lines.append(f"{names.get(fid, f'form#{fid}')}: {fallback}")
    return lines


def gap_lines(bucketed: BucketedTeam, damage: DamageReport, dps: list[DpsInsight]) -> list[str]:
    """High-signal warnings and next actions for the summary."""
    lines: list[str] = []
    if bucketed.cap_orbs == 0:
        lines.append(
            "No free +100k cap orbs were included. Each character can equip at most one — "
            "stacking three (Orb of King Dulin, Blade of Eternal Flaw, Sage Helva's Orb) "
            "is the cheapest path to Good cap tier."
        )
    elif bucketed.cap_orbs > len(bucketed.all_form_ids):
        lines.append(
            f"Free cap orbs={bucketed.cap_orbs} exceeds team size {len(bucketed.all_form_ids)}; "
            "each character can equip at most one."
        )
    elif bucketed.cap_orbs > 3:
        lines.append(
            f"Entered {bucketed.cap_orbs} free cap orbs but only 3 stack (game rule). "
            "Reassign the surplus to other accessory slots."
        )

    if damage.cap_tier != "Good":
        need = max(0.0, 100_000.0 - damage.team_damage_cap_up)
        lines.append(
            f"Team-wide cap support is {_compact(damage.team_damage_cap_up)} ({damage.cap_tier}); "
            f"add about {_compact(need)} team/A4/skill cap to reach Good."
        )

    for row in dps:
        if row.caps_each_hit:
            continue
        reason = why_not_capping(row)
        if reason:
            lines.append(f"{row.summary.display_name}: {reason}")

    if not dps:
        lines.append("No member has a parsed damage skill with at least 4 effective hits.")

    return lines


def format_dps_line(dps: DpsInsight) -> str:
    """One-line summary for a candidate DPS in the Best-use field.

    The first sentence covers the skill mechanics; the second is the
    headline number — estimated total damage for one cast through the
    break window, plus an explicit cap-pass tally so readers can see
    whether the estimate is "every hit at cap" or a potency-limited
    approximation.
    """
    skill = dps.best_skill.name or _skill_label_fallback(dps.best_skill)
    type_tag = "/".join(t for t in (dps.summary.weapon, dps.summary.element) if t) or "unknown"
    head = (
        f"{dps.summary.display_name} ({type_tag}): {skill}, "
        f"{dps.best_skill.power_max or '?'}p × {dps.effective_hits} hits, "
        f"×{dps.summary.buff_multiplier:.2f}"
    )
    return f"{head}\n   {format_damage_estimate(dps)}"


def format_damage_estimate(dps: DpsInsight) -> str:
    """The headline damage line — total + cap-pass tally + per-hit ceiling.

    Examples::

        ≈ 26.0M dmg — 15/15 hits cap at 1.73M (cap +730k, +100% potency)
        ≈ 12.3M dmg (potency-limited; 210/240 — 88% of 1.76M cap × 8 hits)
        ≈ 4.4M dmg (potency-limited; 130/240 — 54% of 1.36M cap × 6 hits)
    """
    per_hit_cap = dps.per_hit_cap
    cap_up_str = _compact(dps.total_cap_up)
    potency_str = f"+{dps.total_potency_up * 100:.0f}%"
    if dps.caps_each_hit:
        return (
            f"≈ {_compact(dps.estimated_total_damage)} dmg — "
            f"{dps.effective_hits}/{dps.effective_hits} hits cap at "
            f"{_compact(per_hit_cap)} (cap +{cap_up_str}, {potency_str} potency)"
        )
    quotient = max(0.0, min(1.0, dps.realised_potency / damage_estimate.POTENCY_TO_REACH_CAP))
    return (
        f"≈ {_compact(dps.estimated_total_damage)} dmg "
        f"(potency-limited; realised {dps.realised_potency:.0f}/240 — "
        f"{quotient * 100:.0f}% of {_compact(per_hit_cap)} cap × "
        f"{dps.effective_hits} hits)"
    )


def why_not_capping(dps: DpsInsight) -> str | None:
    """Return the most actionable reason the DPS doesn't cap each hit.

    Three conditions must hold for cap (per
    ``buff_debuff/damage_cap_and_potency.md``):

    1. Realised potency ≥ 240 (base potency ≥ ~120 with ~+100% potency up)
    2. Team cap-up ≥ +100k (Good tier)
    3. Competitive G1/G2/G3 buff stack (assumed by the rule of thumb)

    Returns ``None`` when ``caps_each_hit`` is True. Otherwise picks the
    binding constraint and suggests the lever to pull.
    """
    if dps.caps_each_hit:
        return None
    cap_total = dps.total_cap_up
    if cap_total < 100_000.0 and dps.realised_potency < damage_estimate.POTENCY_TO_REACH_CAP:
        return (
            f"per-hit cap +{_compact(cap_total)} (Low) AND realised potency "
            f"{dps.realised_potency:.0f} below 240 — both levers need attention."
        )
    if cap_total < 100_000.0:
        return (
            f"per-hit cap +{_compact(cap_total)} (sub-Good) — equip a free orb "
            "or pull in an A4/skill cap-up source."
        )
    if dps.realised_potency < damage_estimate.POTENCY_TO_REACH_CAP:
        return (
            f"realised potency {dps.realised_potency:.0f} below the 240 cap-rule "
            f"({dps.best_skill.power_max or '?'}p × (1+{dps.total_potency_up * 100:.0f}%)) — "
            "add a Skill Potency Up source."
        )
    return (
        "best-skill effective hits don't accumulate enough damage at cap; "
        "consider a higher-hit skill or a multi-cast source."
    )


def format_classified_effect(e: ClassifiedEffect) -> str:
    """Render one ClassifiedEffect for the Support roles field.

    Compact form: ``"+30% Sword DMG Up (active)"``,
    ``"+100k cap (self)"``, ``"Frontrow regen (80)"``,
    ``"Self Triplecast"``. Source kind is normalised onto the
    ``active``/``passive``/``ultimate`` triad to match the bucket
    diagram so support readers don't have to map ``ex``→``active``
    in their head.
    """
    src = _normalise_source_kind(e.source_kind)
    scope = _scope_label(e.target_scope)
    suffix = _suffix(src, scope)
    targets = _format_targets(e.targets)

    if e.category == "stat_up":
        stat = targets or "Atk"
        return f"+{_pct(e.magnitude)} {stat} Up{suffix}"
    if e.category == "stat_down":
        stat = targets or "Def"
        return f"-{_pct(e.magnitude)} {stat} Down{suffix}"
    if e.category == "dmg_up":
        return f"+{_pct(e.magnitude)} {targets or 'All'} DMG Up{suffix}"
    if e.category == "res_down":
        return f"-{_pct(e.magnitude)} {targets or 'All'} Res Down{suffix}"
    if e.category == "crit_up":
        return f"+{_pct(e.magnitude)} Crit Up{suffix}"
    if e.category == "crit_dmg_up":
        return f"+{_pct(e.magnitude)} Crit Dmg Up{suffix}"
    if e.category == "skill_potency_up":
        return f"+{_pct(e.magnitude)} Potency Up{suffix}"
    if e.category == "soul_potency_up":
        return f"+{_pct(e.magnitude)} Soul Potency{suffix}"
    if e.category == "damage_cap_up":
        return f"+{_compact(e.magnitude)} cap{suffix}"
    if e.category == "multi_cast":
        cast = "Triplecast" if e.magnitude >= 3 else "Doublecast"
        return f"{(scope or 'Self').title()} {cast}{(' (' + src + ')') if src and src != 'active' else ''}"
    if e.category == "regen":
        strength = f" ({int(e.magnitude)})" if e.magnitude else ""
        scope_disp = (scope or "frontrow").replace("_", " ").title()
        return f"{scope_disp} regen{strength}"
    if e.category == "heal":
        strength = f" ({int(e.magnitude)})" if e.magnitude else ""
        scope_disp = (scope or "self").replace("_", " ").title()
        return f"{scope_disp} heal{strength}"
    if e.category == "undying":
        return f"Undying{suffix}"
    if e.category == "shield":
        return f"Shield{suffix}"
    if e.category == "cleanse":
        return f"Cleanse{suffix}"
    return f"{e.category}{suffix}"


def parser_confidence(bucketed: BucketedTeam) -> tuple[int, int]:
    """Return ``(classified_count, total_count)``.

    Used by the embed footer / header to convey how trustworthy the
    bucket math is — a team with many unparsed skills yields a less
    certain damage estimate.
    """
    classified = len(bucketed.classified)
    unparsed = len(bucketed.unparsed)
    return classified, classified + unparsed


def _support_effects(bucketed: BucketedTeam, form_id: int) -> list[ClassifiedEffect]:
    """Effects originating from one form, filtered to support categories.

    Skips bare attack-skill stat buffs that are scoped 'self' on a
    one-shot basis (they're already counted in the DPS line). Keeps
    everything that benefits another character or persists.
    """
    out: list[ClassifiedEffect] = []
    for e in bucketed.classified:
        if e.source_form_id != form_id:
            continue
        if e.category == "unparsed":
            continue
        out.append(e)
    return out


_SUPPORT_PRIORITY: dict[str, int] = {
    "skill_potency_up": 0,
    "damage_cap_up": 1,
    "dmg_up": 2,
    "res_down": 3,
    "stat_up": 4,
    "stat_down": 5,
    "soul_potency_up": 6,
    "crit_dmg_up": 7,
    "crit_up": 8,
    "multi_cast": 9,
    "undying": 10,
    "regen": 11,
    "heal": 12,
    "cleanse": 13,
    "shield": 14,
}


def _rank_effects(effects: list[ClassifiedEffect]) -> list[ClassifiedEffect]:
    """Order effects so the highest-signal ones surface first.

    Potency-up and cap-up sit at the top because they're the most
    DPS-aware levers. Within a category, larger magnitudes win.
    Duplicates with identical (category, target_scope, magnitude) are
    collapsed so the same passive doesn't appear twice when re-applied.
    """
    seen: set[tuple] = set()
    deduped: list[ClassifiedEffect] = []
    for e in effects:
        key = (
            e.category, e.target_scope, e.targets,
            round(e.magnitude, 4), _normalise_source_kind(e.source_kind),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(e)
    return sorted(
        deduped,
        key=lambda e: (
            _SUPPORT_PRIORITY.get(e.category, 99),
            -e.magnitude,
        ),
    )


def _normalise_source_kind(kind: str) -> str:
    """Collapse the source-kind string onto the active/passive/ultimate triad."""
    if kind == "ultimate":
        return "ultimate"
    if kind in {"passive", "latent", "tp_passive", "equipment"}:
        return "passive"
    return "active"


def _scope_label(scope: str | None) -> str:
    if not scope:
        return ""
    return scope


def _suffix(src: str, scope: str) -> str:
    """``" (active, self)"`` style trailing tag, deduped + tidied."""
    parts: list[str] = []
    if src and src != "active":
        parts.append(src)
    if scope and scope != "all_allies":
        parts.append(scope.replace("_", " "))
    if not parts:
        return ""
    return f" ({', '.join(parts)})"


def _format_targets(targets: tuple[str, ...]) -> str:
    """Strip umbrella prefixes and render a Title-Cased label."""
    if not targets:
        return ""
    cleaned: list[str] = []
    for t in targets:
        if t.startswith("umbrella:"):
            label = t.split(":", 1)[1]
            cleaned.append({"physical": "Physical", "elemental": "Elemental",
                           "all": "All"}.get(label, label.title()))
        else:
            cleaned.append(t.title())
    return "/".join(cleaned)


def _pct(magnitude: float) -> str:
    return f"{magnitude * 100:.0f}%"


def _skill_label_fallback(skill: SkillDamageRow) -> str:
    """Build a compact display string when the sheet row has no skill name."""
    parts: list[str] = []
    if skill.hits:
        parts.append(f"{skill.hits}x")
    if skill.weapon:
        parts.append(skill.weapon.title())
    elif skill.element:
        parts.append(skill.element.title())
    if not parts:
        return f"slot-{skill.skill_id}"
    return " ".join(parts)


def _compact(value: float) -> str:
    """Compact human-friendly numeric formatter.

    - Below 1k: integer with thousands separators.
    - 1k–999k: ``"123k"`` (rounded down to the nearest thousand).
    - ≥1M: ``"12.3M"`` with one decimal.
    """
    abs_v = abs(value)
    if abs_v >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if abs_v >= 1_000:
        if value % 1_000 == 0:
            return f"{int(value // 1_000)}k"
        return f"{value / 1_000:.1f}k"
    return f"{value:,.0f}"
