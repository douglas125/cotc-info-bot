"""Actionable summary layer for team analysis reports."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from . import damage_estimate
from .types import BucketedTeam, DamageReport, PerDpsDamageSummary, SkillDamageRow


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
        return self.team_cap_up + self.self_cap_up

    @property
    def total_potency_up(self) -> float:
        return self.team_potency_up + self.self_potency_up


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


def support_summaries(bucketed: BucketedTeam, damage: DamageReport, *, exclude: Iterable[int]) -> list[str]:
    """Summarise non-shortlisted members by useful contribution, not DPS math."""
    excluded = set(exclude)
    names = {d.form_id: d.display_name for d in damage.per_dps}
    form_ids = [fid for fid in bucketed.all_form_ids if fid not in excluded]
    lines: list[str] = []
    for fid in form_ids:
        labels = _contribution_labels(bucketed, fid)
        if not labels:
            dps = next((d for d in damage.per_dps if d.form_id == fid), None)
            labels = ["damage skill available"] if dps and dps.best_skills else ["no parsed team role"]
        lines.append(f"{names.get(fid, f'form#{fid}')}: {', '.join(labels)}")
    return lines


def gap_lines(bucketed: BucketedTeam, damage: DamageReport, dps: list[DpsInsight]) -> list[str]:
    """High-signal warnings and next actions for the summary."""
    lines: list[str] = []
    if bucketed.cap_orbs == 0:
        lines.append(
            "No free +100k cap orbs were included. Pass --cap-orbs for visible orb equips; "
            "each character can equip at most one."
        )
    elif bucketed.cap_orbs > len(bucketed.all_form_ids):
        lines.append(
            f"Free cap orbs={bucketed.cap_orbs} exceeds team size {len(bucketed.all_form_ids)}; "
            "each character can equip at most one."
        )

    if damage.cap_tier != "Good":
        need = max(0.0, 100_000.0 - damage.team_damage_cap_up)
        lines.append(
            f"Team-wide cap support is {_compact(damage.team_damage_cap_up)} ({damage.cap_tier}); "
            f"add about {_compact(need)} team/A4/skill cap to reach Good."
        )

    non_capping = [row.summary.display_name for row in dps if not row.caps_each_hit]
    if non_capping:
        lines.append(
            f"{', '.join(non_capping)} best listed skill(s) do not pass the quick cap check; "
            "look for more potency or cap support before relying on capped-hit damage."
        )

    if not dps:
        lines.append("No member has a parsed damage skill with at least 4 effective hits.")

    return lines


def format_dps_line(dps: DpsInsight) -> str:
    skill = dps.best_skill.name or f"slot-{dps.best_skill.skill_id}"
    type_tag = "/".join(t for t in (dps.summary.weapon, dps.summary.element) if t) or "unknown"
    cap_note = "caps" if dps.caps_each_hit else "does not cap"
    return (
        f"{dps.summary.display_name} ({type_tag}): {skill}, "
        f"{dps.best_skill.power_max or '?'}p x {dps.effective_hits} effective hits, "
        f"mult x{dps.summary.buff_multiplier:.2f}, cap {_compact(dps.total_cap_up)}, "
        f"potency +{dps.total_potency_up * 100:.0f}% - {cap_note}"
    )


def _contribution_labels(bucketed: BucketedTeam, form_id: int) -> list[str]:
    labels: list[str] = []
    effects = [e for e in bucketed.classified if e.source_form_id == form_id]
    if any(e.category in {"undying", "regen", "heal", "cleanse", "shield"} for e in effects):
        labels.append("survivability")
    if any(e.category in {"stat_up", "stat_down", "dmg_up", "res_down"} for e in effects):
        labels.append("buff/debuff support")
    if any(e.category == "damage_cap_up" for e in effects):
        labels.append("damage cap support")
    if any(e.category == "skill_potency_up" for e in effects):
        labels.append("potency support")
    if any(e.category == "multi_cast" for e in effects):
        labels.append("self multi-cast")
    return labels


def _compact(value: float) -> str:
    if abs(value) >= 1000 and value % 1000 == 0:
        return f"{int(value // 1000)}k"
    return f"{value:,.0f}"
