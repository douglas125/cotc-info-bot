"""Build the two `/analyze_team` views — matrix and analysis.

The slash command surfaces a dropdown selector with two options:

  - **Damage matrix** (default) — minimal text header + the rendered
    PNG matrix from :mod:`analysis.matrix_image` shown inline via
    ``embed.set_image(url="attachment://...")``.
  - **Analysis breakdown** — the full text-heavy embed (Best use, gaps,
    survivability, cap, support roles).

Both build functions return :class:`RenderedTeamMessage` so the view
in :mod:`bot.team_views` can swap between them without re-rendering
the matrix on every toggle.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from io import BytesIO

import discord

from analysis import damage_estimate, insights, matrix_image
from analysis.matrix_image import RenderedMatrixImage
from analysis.types import (
    BucketedTeam,
    DamageReport,
    NameResolution,
    SurvivabilityVerdict,
    TeamReport,
)
from bot.embeds import EMBED_DESCRIPTION_LIMIT, FIELD_VALUE_LIMIT, _attach_footer, _truncate
from damage.types import ELEMENTS, WEAPONS
from db import repo


@dataclass
class RenderedTeamMessage:
    """A built embed plus the optional file attachment for the matrix view."""

    embed: discord.Embed
    file: discord.File | None = None


def build_matrix_message(
    conn: sqlite3.Connection,
    report: TeamReport,
    *,
    rendered_image: RenderedMatrixImage,
) -> RenderedTeamMessage:
    """Default view — minimal text header + the matrix image attached inline.

    The caller is responsible for wrapping ``rendered_image.data`` in a
    fresh :class:`discord.File` (the stream is consumed on send/edit;
    on toggle-back we re-create from cached bytes).
    """
    embed = discord.Embed(
        title="Team Analysis",
        color=discord.Color.blurple(),
    )
    type_multipliers = damage_estimate.final_multipliers_for_team(report.bucketed)
    embed.description = _truncate(
        _header_description(conn, report) + "\n" + _matrix_headline(type_multipliers),
        EMBED_DESCRIPTION_LIMIT,
    )
    embed.set_image(url=f"attachment://{rendered_image.filename}")
    _attach_footer(embed, repo.latest_sync_run(conn))
    file = discord.File(BytesIO(rendered_image.data), filename=rendered_image.filename)
    return RenderedTeamMessage(embed=embed, file=file)


def build_analysis_message(
    conn: sqlite3.Connection,
    report: TeamReport,
) -> RenderedTeamMessage:
    """Second view — the full text breakdown from PR #44."""
    embed = discord.Embed(
        title="Team Analysis",
        color=discord.Color.blurple(),
    )
    embed.description = _truncate(_header_description(conn, report), EMBED_DESCRIPTION_LIMIT)
    type_multipliers = damage_estimate.final_multipliers_for_team(report.bucketed)

    ranked = insights.ranked_dps(report.bucketed, report.damage, limit=3)
    embed.add_field(
        name="Best use",
        value=_truncate(_best_use_block(ranked), FIELD_VALUE_LIMIT),
        inline=False,
    )
    embed.add_field(
        name="Damage potential by type",
        value=_truncate(_type_matrix_block(report.bucketed, type_multipliers), FIELD_VALUE_LIMIT),
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
        value=_truncate(
            _cap_block(conn, report.bucketed, report.damage, ranked),
            FIELD_VALUE_LIMIT,
        ),
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
    return RenderedTeamMessage(embed=embed, file=None)


def _matrix_headline(type_multipliers: dict[str, float]) -> str:
    """One-line summary of the top types — sits below the header block.

    Picks the top 3 weapon and top 1 element multiplier so a reader
    sees this team's identity without scrolling to the image.
    """
    weapon_pairs = sorted(
        ((w, type_multipliers[w]) for w in WEAPONS),
        key=lambda kv: kv[1], reverse=True,
    )[:3]
    element_pairs = sorted(
        ((e, type_multipliers[e]) for e in ELEMENTS),
        key=lambda kv: kv[1], reverse=True,
    )[:1]
    bits = [f"{n.title()} ×{m:.2f}" for n, m in weapon_pairs + element_pairs]
    return f"_Top damage types:_ {' · '.join(bits)} — full breakdown in image below."


def _header_description(conn: sqlite3.Connection, report: TeamReport) -> str:
    front = _names(conn, report.bucketed.frontrow_form_ids)
    back = _names(conn, report.bucketed.backrow_form_ids)
    boost_label = {0: "0", 1: "1", 2: "2", 3: "MAX"}.get(
        report.bucketed.profile.boost_level, str(report.bucketed.profile.boost_level)
    )
    effective_orbs = report.bucketed.effective_cap_orbs
    profile_line = (
        f"_Profile:_ boost={boost_label}; cap_orbs={report.bucketed.cap_orbs} "
        f"entered/{effective_orbs} counted (game rule: max 3 free orbs stack)"
    )
    if report.bucketed.divine_beast:
        profile_line += "; divine_beast"

    lines = [
        f"**Frontrow:** {', '.join(front) if front else '-'}",
        f"**Backrow:** {', '.join(back) if back else '-'}",
        profile_line,
    ]
    alias_line = _alias_trail(report.name_resolutions)
    if alias_line:
        lines.append(alias_line)
    unresolved_line = _unresolved_trail(report.name_resolutions)
    if unresolved_line:
        lines.append(unresolved_line)
    classified, total = insights.parser_confidence(report.bucketed)
    if total:
        ratio = round((classified / total) * 100)
        lines.append(
            f"_Parser confidence:_ {classified}/{total} effects classified ({ratio}%)"
        )
    lines.append("_Assumes classified buffs are active during the damage window._")
    return "\n".join(lines)


def _alias_trail(resolutions: tuple[NameResolution, ...]) -> str | None:
    pairs = [
        f"{r.typed} → {r.resolved_display_name}"
        for r in resolutions
        if r.is_alias and r.resolved_display_name and r.typed != r.resolved_display_name
    ]
    if not pairs:
        return None
    return f"_Resolved {len(pairs)} input(s) via alias/fuzzy:_ {', '.join(pairs)}"


def _unresolved_trail(resolutions: tuple[NameResolution, ...]) -> str | None:
    misses = [r.typed for r in resolutions if r.via == "unresolved"]
    if not misses:
        return None
    return f"_Unresolved input(s):_ {', '.join(misses)} — excluded from analysis"


def _best_use_block(ranked: list[insights.DpsInsight]) -> str:
    if not ranked:
        return "_No parsed primary DPS candidate._"
    return "\n".join(
        f"{i}. {insights.format_dps_line(dps)}" for i, dps in enumerate(ranked, start=1)
    )


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


def _type_matrix_block(
    bucketed: BucketedTeam, type_multipliers: dict[str, float],
) -> str:
    """Full per-type damage multiplier matrix (all 8 weapons, all 6 elements).

    Each cell is the team's final multiplier for an attack of that type.
    Cells where a team member has guaranteed crit are flagged with ``★``
    and use the crit-applied value (``1.25 + Σ Crit Damage Up`` in the
    final pool). A footnote line names the source of the crit when
    present.
    """
    weapon_pairs = sorted(
        ((w, type_multipliers[w]) for w in WEAPONS),
        key=lambda kv: kv[1], reverse=True,
    )
    element_pairs = sorted(
        ((e, type_multipliers[e]) for e in ELEMENTS),
        key=lambda kv: kv[1], reverse=True,
    )
    weapon_line = _format_type_line(bucketed, weapon_pairs)
    element_line = _format_type_line(bucketed, element_pairs)
    lines = [
        f"**Weapons:** {weapon_line}",
        f"**Elements:** {element_line}",
    ]
    crit_footer = _crit_footnote(bucketed)
    if crit_footer:
        lines.append(crit_footer)
    return "\n".join(lines)


def _format_type_line(
    bucketed: BucketedTeam, pairs: list[tuple[str, float]],
) -> str:
    """Render one row of the matrix — every populated cell, sorted desc."""
    if not pairs:
        return "—"
    cells: list[str] = []
    for name, mult in pairs:
        marker = "★" if damage_estimate.type_has_guaranteed_crit(bucketed, name) else ""
        cells.append(f"{name.title()} {marker}×{mult:.2f}")
    return " · ".join(cells)


def _crit_footnote(bucketed: BucketedTeam) -> str | None:
    """Footnote naming the source(s) of guaranteed crit, when any."""
    if not bucketed.crit_types:
        return None
    sources: list[str] = []
    seen: set[int] = set()
    for e in bucketed.classified:
        if e.category != "crit_guaranteed":
            continue
        if e.source_form_id in seen:
            continue
        seen.add(e.source_form_id)
        sources.append(str(e.source_form_id))
    types_str = "/".join(t.title() for t in sorted(bucketed.crit_types))
    return f"_★ guaranteed crit applied on {types_str} (1.25 + Σ Crit Damage Up)._"


def _cap_block(
    conn: sqlite3.Connection,
    bucketed: BucketedTeam,
    damage: DamageReport,
    ranked: list[insights.DpsInsight],
) -> str:
    """Per-character cap/potency breakdown — the per-DPS view."""
    effective_orbs = bucketed.effective_cap_orbs
    orb_contribution = effective_orbs * 100_000
    other_team_cap = max(0.0, damage.team_damage_cap_up - orb_contribution)
    bits = [
        f"**Team-wide:** +{insights._compact(damage.team_damage_cap_up)} ({damage.cap_tier}) "
        f"= {effective_orbs} orb(s) (+{insights._compact(orb_contribution)}) + "
        f"skill/A4 (+{insights._compact(other_team_cap)})",
    ]
    self_lines = _self_cap_lines(conn, bucketed)
    if self_lines:
        bits.append("**Self-only cap-up (per-DPS only):** " + ", ".join(self_lines))
    if damage.team_skill_potency_up:
        bits.append(
            f"**Team skill potency:** +{damage.team_skill_potency_up * 100:.0f}%"
        )
    if damage.team_soul_potency_up:
        bits.append(
            f"**Team soul potency:** +{damage.team_soul_potency_up * 100:.0f}%"
        )
    bridge_lines = _potency_bridge_lines(conn, bucketed, ranked)
    if bridge_lines:
        bits.append("**Single-ally potency bridges:** " + ", ".join(bridge_lines))
    return "\n".join(bits)


def _self_cap_lines(
    conn: sqlite3.Connection, bucketed: BucketedTeam,
) -> list[str]:
    out: list[str] = []
    for fid in bucketed.all_form_ids:
        _team, self_cap = damage_estimate.cap_up_breakdown_for_dps(bucketed, fid)
        if self_cap <= 0:
            continue
        row = repo.get_form(conn, fid)
        name = row["display_name"] if row else f"form#{fid}"
        out.append(f"{name} +{insights._compact(self_cap)}")
    return out


def _potency_bridge_lines(
    conn: sqlite3.Connection,
    bucketed: BucketedTeam,
    ranked: list[insights.DpsInsight],
) -> list[str]:
    """Surface single-ally / self skill_potency_up sources keyed by DPS."""
    dps_ids = {r.summary.form_id for r in ranked}
    out: list[str] = []
    seen_pairs: set[tuple[int, int]] = set()
    for e in bucketed.classified:
        if e.category != "skill_potency_up":
            continue
        if e.target_scope not in {"self", "single_ally"}:
            continue
        if e.target_scope == "self" and e.source_form_id not in dps_ids:
            continue
        key = (e.source_form_id, hash((e.target_scope, round(e.magnitude, 4))))
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        row = repo.get_form(conn, e.source_form_id)
        source = row["display_name"] if row else f"form#{e.source_form_id}"
        scope = "self" if e.target_scope == "self" else "single ally"
        out.append(f"{source} +{e.magnitude * 100:.0f}% ({scope})")
    return out


def _names(conn: sqlite3.Connection, form_ids) -> list[str]:
    out: list[str] = []
    for fid in form_ids:
        row = repo.get_form(conn, fid)
        out.append(row["display_name"] if row else f"form#{fid}")
    return out
