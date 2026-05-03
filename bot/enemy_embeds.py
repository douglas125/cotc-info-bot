"""Pure embed builders for `/enemy`.

The /enemy command surfaces one embed per (enemy, rank) selection. The view's
dropdown swaps among the enemy's available ranks.
"""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import json
import sqlite3
from typing import Literal

import discord

from bot import enemy_images
from bot.embeds import (
    EMBED_DESCRIPTION_LIMIT,
    FIELD_NAME_LIMIT,
    FIELD_VALUE_LIMIT,
    TITLE_LIMIT,
    _attach_footer,
    _color_from_hex,
    _safe_url,
    _truncate,
)
from config import ENEMIES_SPREADSHEET_URL
from db import repo

Rank = Literal["Rank1", "Rank2", "Rank3", "EX1", "EX2", "EX3", "Default"]
RANKS: tuple[Rank, ...] = ("EX3", "EX2", "EX1", "Rank3", "Rank2", "Rank1")
RANK_LABELS: dict[Rank, str] = {
    "Rank1": "Rank 1",
    "Rank2": "Rank 2",
    "Rank3": "Rank 3",
    "EX1": "EX 1",
    "EX2": "EX 2",
    "EX3": "EX 3",
    "Default": "Default",
}
RANK_DESCRIPTIONS: dict[Rank, str | None] = {
    "Rank1": "Lowest difficulty",
    "Rank2": None,
    "Rank3": None,
    "EX1": "Endgame difficulty",
    "EX2": None,
    "EX3": "Highest difficulty",
    "Default": "Single-stat NPC",
}
RANK_ORDER: dict[str, int] = {r: i for i, r in enumerate(RANKS, start=1)}
RANK_ORDER["Default"] = 0


@dataclass(frozen=True)
class EnemyMessage:
    embed: discord.Embed
    embeds: tuple[discord.Embed, ...] = ()
    file: discord.File | None = None


_STAT_DISPLAY_ORDER: tuple[str, ...] = (
    "HP",
    "Shields",
    "P. Atk",
    "P. Def",
    "E. Atk",
    "E. Def",
    "Speed",
    "Crit",
    "CritDef",
    "Equip Atk",
)
_STAT_RANK: dict[str, int] = {s: i for i, s in enumerate(_STAT_DISPLAY_ORDER)}
_SHEET_EDIT_URL = f"{ENEMIES_SPREADSHEET_URL}/edit"
_MEMBER_NAME_DISPLAY_LIMIT = 14


def _shorten_member_name(name: str) -> str:
    if len(name) <= _MEMBER_NAME_DISPLAY_LIMIT:
        return name
    return name[: _MEMBER_NAME_DISPLAY_LIMIT - 1] + "…"


def _safe_enemy_url(url: str | None) -> str | None:
    return _safe_url(url, base_edit_url=_SHEET_EDIT_URL)


def _stat_sort_key(stat_name: str) -> tuple[int, str]:
    return (_STAT_RANK.get(stat_name, len(_STAT_RANK)), stat_name)


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
    return sorted(ranks, key=lambda r: RANK_ORDER.get(r, 99))[0]


def _format_stat_value(value: str) -> str:
    """Add thousands separators to integer stat values; pass everything else through."""
    stripped = value.lstrip("-").replace(",", "")
    if stripped and stripped.isdigit():
        return format(int(value.replace(",", "")), ",")
    return value


def _build_member_stat_fields(
    stats_rows: list[sqlite3.Row],
) -> list[tuple[str, str, bool]]:
    """Build one (name, value, inline) tuple per member position.

    Each member's stats render as a code block with one stat per line, names
    left-padded and values right-padded so digits align within that block.
    """
    if not stats_rows:
        return []
    by_pos: dict[int, dict[str, str]] = {}
    pos_labels: dict[int, str] = {}
    for row in stats_rows:
        by_pos.setdefault(row["position"], {})[row["stat_name"]] = row["stat_value"]
        if row["position"] not in pos_labels and row["member_name"]:
            pos_labels[row["position"]] = row["member_name"]
    positions = sorted(by_pos.keys())
    if not positions:
        return []

    inline = len(positions) > 1
    fields: list[tuple[str, str, bool]] = []
    for pos in positions:
        member_stats = by_pos[pos]
        ordered = sorted(member_stats.keys(), key=_stat_sort_key)
        formatted = [(s, _format_stat_value(member_stats[s])) for s in ordered]
        name_width = max((len(s) for s, _ in formatted), default=4)
        value_width = max((len(v) for _, v in formatted), default=0)
        body = "\n".join(
            f"{s:<{name_width}}  {v:>{value_width}}" for s, v in formatted
        )
        value = _truncate("```\n" + body + "\n```", FIELD_VALUE_LIMIT)
        raw_name = pos_labels.get(pos, f"#{pos + 1}")
        name = _shorten_member_name(raw_name)[:FIELD_NAME_LIMIT]
        fields.append((name, value, inline))
    return fields


def _new_enemy_header_embed(enemy: sqlite3.Row, rank_label: str) -> discord.Embed:
    title_raw = f"{enemy['canonical_name']} - {rank_label}"
    embed = discord.Embed(
        title=_truncate(title_raw, TITLE_LIMIT),
        url=_safe_enemy_url(enemy["hyperlink_url"]),
        color=_color_from_hex(enemy["name_color_hex"]),
    )
    region = enemy["region"]
    category = enemy["category"]
    if region and region not in category:
        primary = f"**{region}** - {category}"
    else:
        primary = category
    embed.description = _truncate(primary, EMBED_DESCRIPTION_LIMIT)
    return embed


def _new_fight_notes_embed(enemy: sqlite3.Row, note: sqlite3.Row) -> discord.Embed:
    title_raw = f"{note['display_name']} - Fight notes"
    embed = discord.Embed(
        title=_truncate(title_raw, TITLE_LIMIT),
        url=_safe_url(note["source_url"]),
        color=_color_from_hex(enemy["name_color_hex"]),
        description=_truncate(note["summary"], EMBED_DESCRIPTION_LIMIT),
    )
    updated = note["source_updated_at"]
    footer = "Notes paraphrased from Game8"
    if updated:
        footer = f"{footer} - source updated {updated}"
    embed.set_footer(text=footer)
    return embed


def _action_lines(actions: list[dict[str, str]]) -> list[str]:
    lines: list[str] = []
    for action in actions:
        name = (action.get("name") or "").strip()
        effect = (action.get("effect") or "").strip()
        if not name and not effect:
            continue
        if name and effect:
            lines.append(f"**{name}** - {effect}")
        else:
            lines.append(name or effect)
    return lines


def _chunk_lines(lines: list[str], limit: int) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in lines:
        line_len = len(line) + (1 if current else 0)
        if current and current_len + line_len > limit:
            chunks.append("\n".join(current))
            current = [line]
            current_len = len(line)
        else:
            current.append(line)
            current_len += line_len
    if current:
        chunks.append("\n".join(current))
    return chunks


def _weakness_filename(enemy_id: int, rank: Rank) -> str:
    return f"enemy_weaknesses_{enemy_id}_{rank.lower()}.png"


def _build_enemy_parts(
    conn: sqlite3.Connection, enemy_id: int, rank: Rank,
) -> tuple[discord.Embed, list[sqlite3.Row], list[sqlite3.Row]] | None:
    enemy = repo.get_enemy(conn, enemy_id)
    if enemy is None:
        return None
    form = repo.get_enemy_form_by_rank(conn, enemy_id, rank)
    if form is None:
        return None

    stats_rows = repo.get_enemy_member_stats(conn, form["id"])
    weakness_rows = repo.get_enemy_weaknesses(conn, form["id"])
    embed = _new_enemy_header_embed(enemy, RANK_LABELS.get(rank, rank))
    for name, value, inline in _build_member_stat_fields(stats_rows):
        embed.add_field(name=name, value=value, inline=inline)
    _attach_footer(embed, repo.latest_sync_run(conn))
    return embed, stats_rows, weakness_rows


def build_enemy_embed(
    conn: sqlite3.Connection, enemy_id: int, rank: Rank,
) -> discord.Embed | None:
    """Build the full embed for an enemy at a specific rank."""
    parts = _build_enemy_parts(conn, enemy_id, rank)
    if parts is None:
        return None
    embed, stats_rows, weakness_rows = parts
    rendered = enemy_images.render_weakness_panel(
        filename=_weakness_filename(enemy_id, rank),
        stats_rows=stats_rows,
        weakness_rows=weakness_rows,
    )
    if rendered is not None:
        embed.set_image(url=f"attachment://{rendered.filename}")
    return embed


def build_enemy_message(
    conn: sqlite3.Connection, enemy_id: int, rank: Rank,
) -> EnemyMessage | None:
    """Build the `/enemy` embed plus any local image attachment it references."""
    parts = _build_enemy_parts(conn, enemy_id, rank)
    if parts is None:
        return None
    embed, stats_rows, weakness_rows = parts
    rendered = enemy_images.render_weakness_panel(
        filename=_weakness_filename(enemy_id, rank),
        stats_rows=stats_rows,
        weakness_rows=weakness_rows,
    )
    if rendered is None:
        return EnemyMessage(embed=embed)

    embed.set_image(url=f"attachment://{rendered.filename}")
    return EnemyMessage(
        embed=embed,
        file=discord.File(BytesIO(rendered.data), filename=rendered.filename),
    )


def has_fight_notes(conn: sqlite3.Connection, enemy_id: int) -> bool:
    return repo.get_arena_fight_note_for_enemy(conn, enemy_id) is not None


def build_enemy_fight_notes_message(
    conn: sqlite3.Connection, enemy_id: int,
) -> EnemyMessage | None:
    enemy = repo.get_enemy(conn, enemy_id)
    if enemy is None:
        return None
    note = repo.get_arena_fight_note_for_enemy(conn, enemy_id)
    if note is None:
        return None

    first = _new_fight_notes_embed(enemy, note)
    first.add_field(
        name="Mechanics",
        value=_truncate(note["mechanics"], FIELD_VALUE_LIMIT),
        inline=False,
    )
    first.add_field(
        name="Strategy",
        value=_truncate(note["strategy"], FIELD_VALUE_LIMIT),
        inline=False,
    )

    embeds = [first]
    actions = json.loads(note["actions_json"] or "[]")
    chunks = _chunk_lines(_action_lines(actions), FIELD_VALUE_LIMIT)
    for idx, chunk in enumerate(chunks, start=1):
        target = first if idx == 1 and len(first.fields) < 25 else discord.Embed(
            title=_truncate(f"{note['display_name']} - Fight notes ({idx})", TITLE_LIMIT),
            url=_safe_url(note["source_url"]),
            color=_color_from_hex(enemy["name_color_hex"]),
        )
        target.add_field(
            name="Action List" if len(chunks) == 1 else f"Action List ({idx}/{len(chunks)})",
            value=chunk,
            inline=False,
        )
        if target is not first:
            target.set_footer(text=first.footer.text or "Notes paraphrased from Game8")
            embeds.append(target)
        if len(embeds) >= 10:
            break
    return EnemyMessage(embed=embeds[0], embeds=tuple(embeds))


def search_results_to_embed(rows: list[sqlite3.Row], query_summary: str) -> discord.Embed:
    embed = discord.Embed(
        title=_truncate(f"Enemies matching: {query_summary}", TITLE_LIMIT),
        color=discord.Color.dark_grey(),
    )
    if not rows:
        embed.description = "No matches."
        return embed
    lines: list[str] = []
    for row in rows[:10]:
        url = _safe_enemy_url(row["hyperlink_url"])
        name = row["canonical_name"]
        category = row["category"]
        if url:
            lines.append(f"- [{name}]({url}) - *{category}*")
        else:
            lines.append(f"- {name} - *{category}*")
    embed.description = _truncate("\n".join(lines), EMBED_DESCRIPTION_LIMIT)
    return embed
