"""Pure embed builder for `/pet`.

Pet info fits on a single screen — no rank dropdown, no kit pages — so
this module exposes one entry point: `build_pet_embed(conn, pet_id)`.
The fields render: ability text in the description, then Max Boost,
Turn Preparation (base / Lv10), Turn Cooldown (base / Lv5), an
aligned Stats block, and the "How to obtain" string.
"""
from __future__ import annotations

import sqlite3

import discord

from bot.embeds import (
    EMBED_DESCRIPTION_LIMIT,
    FIELD_VALUE_LIMIT,
    TITLE_LIMIT,
    _attach_footer,
    _color_from_hex,
    _safe_url,
    _truncate,
)
from config import PETS_SPREADSHEET_URL
from db import repo


_SHEET_EDIT_URL = f"{PETS_SPREADSHEET_URL}/edit"


def _safe_pet_url(url: str | None) -> str | None:
    return _safe_url(url, base_edit_url=_SHEET_EDIT_URL)


def _format_prep(base: int | None, lv10: int | None) -> str:
    if base is None:
        return "—"
    if lv10 is None:
        return str(base)
    return f"{base} (Lv10: {lv10})"


def _format_cooldown(base: int | None, lv5: int | None) -> str:
    if base is None:
        return "—"
    if lv5 is None:
        return str(base)
    return f"{base} (Lv5: {lv5})"


_STAT_ROWS: tuple[tuple[str, str], ...] = (
    ("HP", "hp"),     ("SP",    "sp"),
    ("Patk", "patk"), ("Pdef",  "pdef"),
    ("Matk", "matk"), ("Mdef",  "mdef"),
    ("Crit", "crit"), ("Speed", "speed"),
)


def _format_stats_block(pet: sqlite3.Row) -> str:
    """Two-column code block, mirroring the enemy stat block style.

    Each row pairs a stat label and its value, padded so digits align
    inside the code-block monospace font.
    """
    pairs: list[tuple[str, str]] = []
    for label, key in _STAT_ROWS:
        v = pet[key]
        pairs.append((label, "—" if v is None else str(v)))
    label_width = max(len(p[0]) for p in pairs)
    value_width = max(len(p[1]) for p in pairs)
    # Render two stats per visual row: HP/SP, Patk/Pdef, Matk/Mdef, Crit/Speed.
    lines: list[str] = []
    for i in range(0, len(pairs), 2):
        left_lab, left_val = pairs[i]
        right_lab, right_val = pairs[i + 1]
        lines.append(
            f"{left_lab:<{label_width}}  {left_val:>{value_width}}    "
            f"{right_lab:<{label_width}}  {right_val:>{value_width}}"
        )
    return "```\n" + "\n".join(lines) + "\n```"


def build_pet_embed(
    conn: sqlite3.Connection, pet_id: int,
) -> discord.Embed | None:
    """Return the single-screen `/pet` embed, or None if the pet was wiped
    by a refresh between autocomplete and the user pressing enter."""
    pet = repo.get_pet(conn, pet_id)
    if pet is None:
        return None

    description_parts: list[str] = []
    ability = pet["ability_text"] or ""
    if ability:
        description_parts.append(ability)
    if pet["display_name_jp"] and pet["display_name_jp"] != pet["canonical_name"]:
        description_parts.append(f"*Sheet name: {pet['display_name_jp']}*")
    description = "\n\n".join(description_parts) if description_parts else "—"

    embed = discord.Embed(
        title=_truncate(pet["canonical_name"], TITLE_LIMIT),
        url=_safe_pet_url(pet["hyperlink_url"]),
        color=_color_from_hex(pet["name_color_hex"]),
        description=_truncate(description, EMBED_DESCRIPTION_LIMIT),
    )
    embed.add_field(
        name="Max Boost",
        value=pet["max_boost"] or "—",
        inline=True,
    )
    embed.add_field(
        name="Turn Preparation",
        value=_format_prep(pet["prep_base"], pet["prep_lv10"]),
        inline=True,
    )
    embed.add_field(
        name="Turn Cooldown",
        value=_format_cooldown(pet["cooldown_base"], pet["cooldown_lv5"]),
        inline=True,
    )
    embed.add_field(
        name="Stats",
        value=_truncate(_format_stats_block(pet), FIELD_VALUE_LIMIT),
        inline=False,
    )
    embed.add_field(
        name="How to obtain",
        value=_truncate(pet["source_text"] or "—", FIELD_VALUE_LIMIT),
        inline=False,
    )
    _attach_footer(embed, repo.latest_sync_run(conn))
    return embed
