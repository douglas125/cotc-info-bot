"""Render a :class:`analysis.types.TeamReport` into a Discord embed.

Layout:

  Header:  team layout, profile flags, assumptions footer
  Field:   Survivability tier + citations
  Field:   Damage cap up — total + sources
  Field:   Per-DPS damage table (auto-pick across all 8 + highlighted DPS)
  Fields:  Offensive coverage matrix — per-source-kind rows for G1/G2/G3,
           plus G4/G5 if present, plus G6 active flag
  Field:   Unparsed skills (only when non-empty)
  Footer:  sync stamp + feedback prompt + assumption note

The matrix follows the buff_debuff/README.md layout: G2 (DMG Up) and
G3 (Res Down) show one row per source kind (active / passive /
ultimate) with per-weapon and per-element columns, so a sword team's
audit can see "Active Sword 30%, Passive Sword 15%, Ultimate Sword
20%" at a glance.
"""
from __future__ import annotations

import sqlite3

import discord

from analysis import coverage, damage_estimate
from analysis.coverage import (
    MatrixRow,
    matrix_rows_for_group,
    umbrella_summary,
)
from analysis.types import (
    DamageReport,
    SurvivabilityVerdict,
    TeamReport,
)
from bot.embeds import (
    EMBED_DESCRIPTION_LIMIT,
    FIELD_VALUE_LIMIT,
    _attach_footer,
    _truncate,
)
from db import repo


def build(conn: sqlite3.Connection, report: TeamReport) -> discord.Embed:
    embed = discord.Embed(
        title="Team Analysis",
        color=discord.Color.blurple(),
    )
    embed.description = _truncate(_header_description(conn, report), EMBED_DESCRIPTION_LIMIT)

    embed.add_field(
        name="Survivability",
        value=_truncate(_survivability_block(report.survivability), FIELD_VALUE_LIMIT),
        inline=False,
    )
    embed.add_field(
        name="Damage cap up",
        value=_truncate(_cap_block(report.bucketed, report.damage), FIELD_VALUE_LIMIT),
        inline=False,
    )
    embed.add_field(
        name="Per-DPS damage",
        value=_truncate(_per_dps_block(report.bucketed, report.damage), FIELD_VALUE_LIMIT),
        inline=False,
    )
    if not coverage.is_empty(report.coverage):
        for name, value in _coverage_fields(report):
            if not value:
                continue
            embed.add_field(name=name, value=_truncate(value, FIELD_VALUE_LIMIT), inline=False)
    if report.bucketed.unparsed:
        embed.add_field(
            name=f"Unparsed skills ({len(report.bucketed.unparsed)})",
            value=_truncate(_unparsed_block(report.bucketed.unparsed), FIELD_VALUE_LIMIT),
            inline=False,
        )

    _attach_footer(embed, repo.latest_sync_run(conn))
    return embed


# ---------------------------------------------------------------------------
# Section builders.
# ---------------------------------------------------------------------------

def _header_description(conn: sqlite3.Connection, report: TeamReport) -> str:
    front = _names(conn, report.bucketed.frontrow_form_ids)
    back = _names(conn, report.bucketed.backrow_form_ids)
    boost_label = {0: "0", 1: "1", 2: "2", 3: "MAX"}.get(
        report.bucketed.profile.boost_level, str(report.bucketed.profile.boost_level)
    )
    lines = [
        f"**Frontrow:** {', '.join(front) if front else '—'}",
        f"**Backrow:** {', '.join(back) if back else '—'}",
    ]
    flags: list[str] = [f"boost={boost_label}"]
    if report.bucketed.divine_beast:
        flags.append("divine_beast")
    if report.bucketed.cap_orbs:
        flags.append(f"cap_orbs={report.bucketed.cap_orbs}")
    if report.bucketed.pet_id is not None:
        flags.append(f"pet={report.bucketed.pet_id}")
    lines.append(f"_Profile:_ { ' · '.join(flags) }")
    lines.append("_Assumes all classified buffs active during damage window._")
    return "\n".join(lines)


def _survivability_block(verdict: SurvivabilityVerdict) -> str:
    head = f"**{verdict.tier}** ({verdict.primary_source_display})"
    if not verdict.citations:
        return head
    cites = "\n".join(f"• {c.snippet}" for c in verdict.citations[:4])
    return f"{head}\n{cites}"


def _cap_block(bucketed, damage: DamageReport) -> str:
    head = (
        f"**Total team-wide:** +{damage.team_damage_cap_up:,.0f}  "
        f"(*{damage.cap_tier}*)"
    )
    bits = [head]
    orbs_value = bucketed.cap_orbs * 100_000
    if orbs_value:
        bits.append(f"• free orbs: +{orbs_value:,.0f}")
    skill_cap = damage.team_damage_cap_up - orbs_value
    if skill_cap > 0:
        bits.append(f"• team-scoped skills/equipment: +{skill_cap:,.0f}")
    if damage.team_skill_potency_up:
        bits.append(f"• skill potency up (team): +{damage.team_skill_potency_up * 100:.0f}%")
    if damage.team_soul_potency_up:
        bits.append(f"• soul potency up (team): +{damage.team_soul_potency_up * 100:.0f}%")
    bits.append("_Self-scoped cap-up / potency-up shown per-DPS below._")
    return "\n".join(bits)


def _per_dps_block(bucketed, damage: DamageReport) -> str:
    if not damage.per_dps:
        return "_No team members to evaluate._"
    rows: list[str] = []
    qualifying = [d for d in damage.per_dps if d.best_skills]
    rendering = qualifying if qualifying else damage.per_dps
    for dps in rendering:
        marker = "→ " if dps.is_highlighted_dps else "• "
        type_tag = f"{dps.weapon or '?'}/{dps.element or '?'}"
        team_wide_cap, self_cap = damage_estimate.cap_up_breakdown_for_dps(
            bucketed, dps.form_id,
        )
        team_potency, self_potency = damage_estimate.potency_up_breakdown_for_dps(
            bucketed, dps.form_id,
        )
        cap_for_dps = team_wide_cap + self_cap
        potency_for_dps = team_potency + self_potency
        multi_cast = damage_estimate.self_multi_cast_factor(bucketed, dps.form_id)
        cap_self_str = f" (+{self_cap:,.0f} self)" if self_cap else ""
        mcast_str = f", ×{multi_cast:.0f} multi-cast" if multi_cast > 1.0 else ""
        best = dps.best_skills[0] if dps.best_skills else None
        if best:
            eff_hits = damage_estimate.effective_hits(best.hits, multi_cast)
            potency = damage_estimate.realised_potency(best.power_max, potency_for_dps)
            caps = damage_estimate.caps_each_hit(
                power=best.power_max,
                skill_potency_up=potency_for_dps,
                team_damage_cap_up=cap_for_dps,
            )
            cap_flag = "✓" if caps else "✗"
            best_line = (
                f"  best `{best.name or best.skill_kind}` "
                f"power={best.power_min}-{best.power_max} hits={best.hits or '?'}"
                f"{f' (eff {eff_hits})' if multi_cast > 1.0 else ''} "
                f"realised={potency:.0f} cap_each={cap_flag}"
            )
        else:
            best_line = "  _no damage-relevant skill (effective_hits ≥ 4 required)_"
        rows.append(
            f"{marker}**{dps.display_name}** ({type_tag}) — "
            f"×{dps.buff_multiplier:.2f}, cap +{cap_for_dps:,.0f}{cap_self_str}{mcast_str}\n"
            f"{best_line}"
        )
    if not qualifying:
        rows.append("_No DPS has a skill with effective_hits ≥ 4 — classifier may be missing multi-cast patterns._")
    return "\n".join(rows)


def _coverage_fields(report: TeamReport) -> list[tuple[str, str]]:
    """Build the matrix as multiple embed fields.

    One field per group; G2/G3 show per-source-kind rows with both
    per-type entries and umbrella summaries (when present). Field
    values stay under 1024 chars by capping per-row entries.
    """
    fields: list[tuple[str, str]] = []
    raw = report.bucketed.raw_sub_bucket_sums

    g1_text = _g1_text(raw)
    if g1_text:
        fields.append(("G1 Stats (Atk/Mag/Def/MDef/Crit)", g1_text))

    for group, label, suffix in (
        ("g2", "G2 DMG Up (per weapon / per element)", "dmg_up"),
        ("g3", "G3 Res Down (per weapon / per element)", "res_down"),
    ):
        text = _typed_group_text(raw, group=group, suffix=suffix)
        if text:
            fields.append((label, text))

    if report.coverage.g4:
        fields.append(("G4 Ultimate sub-pools", _free_keys_text(report.coverage.g4)))
    if report.coverage.g5:
        fields.append(("G5 Pet sub-pools", _free_keys_text(report.coverage.g5)))
    if report.coverage.g6_active:
        fields.append(("G6 Divine Beast", "Active (×1.10)"))

    return fields


def _g1_text(raw: dict | object) -> str:
    rows = matrix_rows_for_group(raw, group="g1", suffix=None)
    return _format_rows(rows)


def _typed_group_text(raw: dict | object, *, group: str, suffix: str) -> str:
    rows = matrix_rows_for_group(raw, group=group, suffix=suffix)
    if not rows:
        return ""
    lines: list[str] = []
    for row in rows:
        # Try to summarise umbrella if every weapon/element matches.
        umbrella = umbrella_summary(raw, group=group, source=row.source, suffix=suffix)
        cell_strs = []
        for cell in row.cells:
            cell_strs.append(f"{cell.label} {coverage.render_pct(cell.magnitude)}")
        line = f"**{row.source.title()}** — " + ", ".join(cell_strs)
        if umbrella is not None:
            phys, elem = umbrella
            extras: list[str] = []
            if phys > 0:
                extras.append(f"Phys floor {coverage.render_pct(phys)}")
            if elem > 0:
                extras.append(f"Elem floor {coverage.render_pct(elem)}")
            if extras:
                line += f"  _({', '.join(extras)})_"
        lines.append(line)
    return "\n".join(lines)


def _format_rows(rows: list[MatrixRow]) -> str:
    lines: list[str] = []
    for row in rows:
        cells = ", ".join(
            f"{c.label} {coverage.render_pct(c.magnitude)}" for c in row.cells
        )
        lines.append(f"**{row.source.title()}** — {cells}")
    return "\n".join(lines)


def _free_keys_text(d: dict) -> str:
    return ", ".join(
        f"{coverage.label_for_key(k)} {coverage.render_pct(v)}"
        for k, v in coverage.top_n(d, n=8)
    )


def _unparsed_block(unparsed) -> str:
    lines = []
    for u in unparsed[:6]:
        snippet = " ".join((u.raw_description or "").split())[:120]
        lines.append(f"• [{u.source_kind}] {snippet}")
    if len(unparsed) > 6:
        lines.append(f"… and {len(unparsed) - 6} more (run `analysis.audit --debug`)")
    return "\n".join(lines)


def _names(conn: sqlite3.Connection, form_ids) -> list[str]:
    out: list[str] = []
    for fid in form_ids:
        row = repo.get_form(conn, fid)
        out.append(row["display_name"] if row else f"form#{fid}")
    return out
