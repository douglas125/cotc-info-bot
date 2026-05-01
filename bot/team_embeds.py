"""Render a :class:`analysis.types.TeamReport` into a Discord embed."""
from __future__ import annotations

import sqlite3

import discord

from analysis import insights
from analysis.types import DamageReport, SurvivabilityVerdict, TeamReport
from bot.embeds import EMBED_DESCRIPTION_LIMIT, FIELD_VALUE_LIMIT, _attach_footer, _truncate
from db import repo


def build(conn: sqlite3.Connection, report: TeamReport) -> discord.Embed:
    embed = discord.Embed(
        title="Team Analysis",
        color=discord.Color.blurple(),
    )
    embed.description = _truncate(_header_description(conn, report), EMBED_DESCRIPTION_LIMIT)

    ranked = insights.ranked_dps(report.bucketed, report.damage, limit=3)
    embed.add_field(
        name="Best use",
        value=_truncate(_best_use_block(ranked), FIELD_VALUE_LIMIT),
        inline=False,
    )
    embed.add_field(
        name="Main gaps",
        value=_truncate(_gap_block(report, ranked), FIELD_VALUE_LIMIT),
        inline=False,
    )
    embed.add_field(
        name="Survivability",
        value=_truncate(_survivability_block(report.survivability), FIELD_VALUE_LIMIT),
        inline=False,
    )
    embed.add_field(
        name="Team cap and potency",
        value=_truncate(_cap_block(report.bucketed, report.damage), FIELD_VALUE_LIMIT),
        inline=False,
    )

    support = insights.support_summaries(
        report.bucketed,
        report.damage,
        exclude=[d.summary.form_id for d in ranked],
    )
    if support:
        embed.add_field(
            name="Support roles",
            value=_truncate("\n".join(f"- {line}" for line in support[:6]), FIELD_VALUE_LIMIT),
            inline=False,
        )

    _attach_footer(embed, repo.latest_sync_run(conn))
    return embed


def _header_description(conn: sqlite3.Connection, report: TeamReport) -> str:
    front = _names(conn, report.bucketed.frontrow_form_ids)
    back = _names(conn, report.bucketed.backrow_form_ids)
    boost_label = {0: "0", 1: "1", 2: "2", 3: "MAX"}.get(
        report.bucketed.profile.boost_level, str(report.bucketed.profile.boost_level)
    )
    effective_orbs = max(0, min(report.bucketed.cap_orbs, 3, len(report.bucketed.all_form_ids)))
    lines = [
        f"**Frontrow:** {', '.join(front) if front else '-'}",
        f"**Backrow:** {', '.join(back) if back else '-'}",
        f"_Profile:_ boost={boost_label}; cap_orbs={report.bucketed.cap_orbs} entered/{effective_orbs} counted",
        "_Assumes classified buffs are active during the damage window._",
    ]
    if report.bucketed.divine_beast:
        lines[2] += "; divine_beast"
    return "\n".join(lines)


def _best_use_block(ranked: list[insights.DpsInsight]) -> str:
    if not ranked:
        return "_No parsed primary DPS candidate._"
    return "\n".join(f"{i}. {insights.format_dps_line(dps)}" for i, dps in enumerate(ranked, start=1))


def _gap_block(report: TeamReport, ranked: list[insights.DpsInsight]) -> str:
    gaps = insights.gap_lines(report.bucketed, report.damage, ranked)
    if not gaps:
        return "- No major gap found by the current parser."
    return "\n".join(f"- {line}" for line in gaps[:5])


def _survivability_block(verdict: SurvivabilityVerdict) -> str:
    head = f"**{verdict.tier}** ({verdict.primary_source_display})"
    if not verdict.citations:
        return head
    cites = "\n".join(f"- {c.snippet}" for c in verdict.citations[:3])
    return f"{head}\n{cites}"


def _cap_block(bucketed, damage: DamageReport) -> str:
    effective_orbs = max(0, min(bucketed.cap_orbs, 3, len(bucketed.all_form_ids)))
    bits = [
        f"Team-wide cap: +{damage.team_damage_cap_up:,.0f} ({damage.cap_tier})",
        f"Free +100k cap orbs: {effective_orbs} counted, max one per character.",
        "Other cap must come from A4/accessory/skill effects.",
    ]
    if damage.team_skill_potency_up:
        bits.append(f"Team skill potency: +{damage.team_skill_potency_up * 100:.0f}%")
    if damage.team_soul_potency_up:
        bits.append(f"Team soul potency: +{damage.team_soul_potency_up * 100:.0f}%")
    return "\n".join(bits)


def _names(conn: sqlite3.Connection, form_ids) -> list[str]:
    out: list[str] = []
    for fid in form_ids:
        row = repo.get_form(conn, fid)
        out.append(row["display_name"] if row else f"form#{fid}")
    return out
