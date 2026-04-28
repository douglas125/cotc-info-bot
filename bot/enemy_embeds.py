"""Pure embed builders for `/enemy`.

Mirrors `bot/embeds.py` in shape but for enemy data. Each builder takes a
sqlite3 connection plus an enemy_id (and optional rank) and returns a
`discord.Embed` — no Discord runtime dependency, fully unit-testable.

The /enemy command surfaces one embed per (enemy, rank) selection. The
view's dropdown swaps among the enemy's available ranks (Rank1..EX3 for
ranked enemies; NPCs have a single 'Default' form and use no dropdown).
"""
from __future__ import annotations

import sqlite3
from typing import Any, Literal

import discord

from bot.embeds import (
    EMBED_DESCRIPTION_LIMIT,
    FIELD_VALUE_LIMIT,
    TITLE_LIMIT,
    _attach_footer,
    _color_from_hex,
    _truncate,
)
from config import ENEMIES_SPREADSHEET_URL
from db import repo

Rank = Literal["Rank1", "Rank2", "Rank3", "EX1", "EX2", "EX3", "Default"]
RANKS: tuple[Rank, ...] = ("Rank1", "Rank2", "Rank3", "EX1", "EX2", "EX3")
RANK_LABELS: dict[Rank, str] = {
    "Rank1":   "Rank 1",
    "Rank2":   "Rank 2",
    "Rank3":   "Rank 3",
    "EX1":     "EX 1",
    "EX2":     "EX 2",
    "EX3":     "EX 3",
    "Default": "Default",
}
RANK_DESCRIPTIONS: dict[Rank, str] = {
    "Rank1":   "Lowest difficulty",
    "Rank2":   "",
    "Rank3":   "",
    "EX1":     "Endgame difficulty",
    "EX2":     "",
    "EX3":     "Highest difficulty",
    "Default": "Single-stat NPC",
}
RANK_ORDER: dict[str, int] = {r: i for i, r in enumerate(RANKS, start=1)}
RANK_ORDER["Default"] = 0

# Stable display order for the stats grid. Anything outside this list falls
# through to alphabetical order at the end.
_STAT_DISPLAY_ORDER: tuple[str, ...] = (
    "HP", "Shields", "P. Atk", "P. Def", "E. Atk", "E. Def",
    "Speed", "Crit", "CritDef", "Equip Atk",
)


_SHEET_EDIT_URL = f"{ENEMIES_SPREADSHEET_URL}/edit"


def _safe_enemy_url(url: str | None) -> str | None:
    """Mirror of `bot.embeds._safe_url` but for the enemy spreadsheet."""
    if not url:
        return None
    if url.startswith(("http://", "https://")):
        return url
    if url.startswith("#"):
        return _SHEET_EDIT_URL + url
    return None


def _stat_sort_key(stat_name: str) -> tuple[int, str]:
    if stat_name in _STAT_DISPLAY_ORDER:
        return (_STAT_DISPLAY_ORDER.index(stat_name), stat_name)
    return (len(_STAT_DISPLAY_ORDER), stat_name)


def available_ranks(conn: sqlite3.Connection, enemy_id: int) -> list[Rank]:
    """Return the ranks this enemy actually has, in canonical order."""
    rows = repo.get_enemy_forms(conn, enemy_id)
    seen = {row["rank"] for row in rows}
    if "Default" in seen:
        return ["Default"]
    return [r for r in RANKS if r in seen]


def default_rank(ranks: list[Rank]) -> Rank | None:
    if not ranks:
        return None
    if "Default" in ranks:
        return "Default"
    # Lowest available ranked tier — sort by canonical order so callers can
    # pass any subset in any order.
    return sorted(ranks, key=lambda r: RANK_ORDER.get(r, 99))[0]


def _build_stats_field(stats_rows: list[sqlite3.Row]) -> tuple[str | None, list[str]]:
    """Return (stats_field_value, position_labels)."""
    if not stats_rows:
        return None, []
    by_pos: dict[int, dict[str, str]] = {}
    pos_labels: dict[int, str] = {}
    for r in stats_rows:
        by_pos.setdefault(r["position"], {})[r["stat_name"]] = r["stat_value"]
        if r["position"] not in pos_labels and r["member_name"]:
            pos_labels[r["position"]] = r["member_name"]
    positions = sorted(by_pos.keys())
    if not positions:
        return None, []
    # Collect the union of stat names across positions, in display order.
    stat_names: list[str] = []
    seen_stats: set[str] = set()
    for pos in positions:
        for stat in by_pos[pos]:
            if stat not in seen_stats:
                seen_stats.add(stat)
                stat_names.append(stat)
    stat_names.sort(key=_stat_sort_key)
    # Build a code-block table:
    #     STAT          POS0_LABEL   POS1_LABEL
    #     HP            1,143,210    822,762
    #     P. Atk        1,752        1,344
    headers = [pos_labels.get(p, f"#{p+1}") for p in positions]
    # Truncate long names so the table stays narrow enough for Discord mobile.
    short_headers = [h if len(h) <= 14 else h[:13] + "…" for h in headers]
    name_width = max((len(s) for s in stat_names), default=4)
    name_width = min(name_width, 12)
    col_widths = [max(len(h), 7) for h in short_headers]
    for s_i, stat in enumerate(stat_names):
        for p_i, pos in enumerate(positions):
            v = by_pos[pos].get(stat, "—")
            col_widths[p_i] = max(col_widths[p_i], len(v))
    lines: list[str] = []
    header_line = f"{'':<{name_width}}  " + "  ".join(
        h.rjust(w) for h, w in zip(short_headers, col_widths)
    )
    lines.append(header_line)
    for stat in stat_names:
        cells = [
            by_pos[pos].get(stat, "—").rjust(col_widths[p_i])
            for p_i, pos in enumerate(positions)
        ]
        lines.append(f"{stat[:name_width]:<{name_width}}  " + "  ".join(cells))
    table = "```\n" + "\n".join(lines) + "\n```"
    pos_labels_list = [pos_labels.get(p, f"#{p+1}") for p in positions]
    return _truncate(table, FIELD_VALUE_LIMIT), pos_labels_list


def _build_weaknesses_field(
    stats_rows: list[sqlite3.Row],
    weakness_rows: list[sqlite3.Row],
) -> str | None:
    """Render per-position break-shield count + weakness icons-as-text.

    Weakness names come from the display tab's named-range formulas (e.g.
    `=Sword`, `=Wind`) — not from inserted images, which the Sheets API
    can't see. Format: `**Leader Lloris** — ×30 · Axe · Bow · Ice · Wind · Dark`.
    """
    shields_by_pos: dict[int, str] = {}
    member_by_pos: dict[int, str] = {}
    for r in stats_rows:
        if r["stat_name"] == "Shields":
            shields_by_pos[r["position"]] = r["stat_value"]
        if r["position"] not in member_by_pos and r["member_name"]:
            member_by_pos[r["position"]] = r["member_name"]
    weaknesses_by_pos: dict[int, list[str]] = {}
    for r in weakness_rows:
        weaknesses_by_pos.setdefault(r["position"], []).append(r["weakness_label"])
    positions = sorted(set(shields_by_pos) | set(weaknesses_by_pos))
    if not positions:
        return None
    parts: list[str] = []
    for p in positions:
        label = member_by_pos.get(p, f"#{p+1}")
        head = f"**{label}**"
        bits: list[str] = []
        if p in shields_by_pos:
            bits.append(f"×{shields_by_pos[p]}")
        if p in weaknesses_by_pos:
            bits.append(" · ".join(weaknesses_by_pos[p]))
        if bits:
            parts.append(f"{head} — " + " · ".join(bits))
        else:
            parts.append(head)
    return _truncate("\n".join(parts), FIELD_VALUE_LIMIT)


def _new_enemy_header_embed(enemy: sqlite3.Row, rank_label: str) -> discord.Embed:
    title_raw = f"{enemy['canonical_name']} — {rank_label}"
    embed = discord.Embed(
        title=_truncate(title_raw, TITLE_LIMIT),
        url=_safe_enemy_url(enemy["hyperlink_url"]),
        color=_color_from_hex(enemy["name_color_hex"]),
    )
    region = enemy["region"]
    category = enemy["category"]
    if region and region not in category:
        primary = f"**{region}** · {category}"
    else:
        primary = category
    embed.description = _truncate(primary, EMBED_DESCRIPTION_LIMIT)
    return embed


def build_enemy_embed(
    conn: sqlite3.Connection, enemy_id: int, rank: Rank,
) -> discord.Embed | None:
    """Build the full embed for an enemy at a specific rank.

    Returns None if the enemy or the (enemy, rank) form is missing — the
    caller turns this into an "enemy was removed" ephemeral.
    """
    enemy = repo.get_enemy(conn, enemy_id)
    if enemy is None:
        return None
    form = repo.get_enemy_form_by_rank(conn, enemy_id, rank)
    if form is None:
        return None
    stats_rows = repo.get_enemy_member_stats(conn, form["id"])
    weakness_rows = repo.get_enemy_weaknesses(conn, form["id"])
    rank_label = RANK_LABELS.get(rank, rank)
    embed = _new_enemy_header_embed(enemy, rank_label)
    stats_field, _pos_labels = _build_stats_field(stats_rows)
    if stats_field:
        embed.add_field(name="Stats", value=stats_field, inline=False)
    weaknesses_field = _build_weaknesses_field(stats_rows, weakness_rows)
    if weaknesses_field:
        embed.add_field(
            name="Weaknesses",
            value=weaknesses_field,
            inline=False,
        )
    last_sync = repo.latest_sync_run(conn)
    _attach_footer(embed, last_sync)
    return embed


# --- search-results embed (small helper for any future /enemy_search) ------

def search_results_to_embed(rows: list[sqlite3.Row], query_summary: str) -> discord.Embed:
    embed = discord.Embed(
        title=_truncate(f"Enemies matching: {query_summary}", TITLE_LIMIT),
        color=discord.Color.dark_grey(),
    )
    if not rows:
        embed.description = "No matches."
        return embed
    lines: list[str] = []
    for r in rows[:10]:
        url = _safe_enemy_url(r["hyperlink_url"])
        name = r["canonical_name"]
        category = r["category"]
        if url:
            lines.append(f"- [{name}]({url}) — *{category}*")
        else:
            lines.append(f"- {name} — *{category}*")
    embed.description = _truncate("\n".join(lines), EMBED_DESCRIPTION_LIMIT)
    return embed
