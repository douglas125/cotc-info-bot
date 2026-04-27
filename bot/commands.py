"""Slash command definitions: /character, /search, /affinities, /refresh.

Registers everything onto a `discord.app_commands.CommandTree` so the bot
client's `on_ready` can sync once at startup.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from typing import Any

import discord
from discord import app_commands

import config
from bot import db as bot_db
from bot import embeds
from db import repo

logger = logging.getLogger(__name__)

AUTOCOMPLETE_LIMIT = 25  # Discord's hard cap on choice list size.

# Module-level lock so two admins hitting /refresh at once don't dogpile the
# Sheets API. Created lazily to avoid binding to a different event loop than
# the bot eventually runs on (tests, REPL, etc.).
_refresh_lock: asyncio.Lock | None = None


def _lock() -> asyncio.Lock:
    global _refresh_lock
    if _refresh_lock is None:
        _refresh_lock = asyncio.Lock()
    return _refresh_lock


# ----------------------------------------------------------------------------
# Form-name autocomplete (used by /character and /affinities)
# ----------------------------------------------------------------------------

def _autocomplete_forms(conn: sqlite3.Connection, current: str) -> list[app_commands.Choice[str]]:
    """Look up display_names matching `current`, returning Choices keyed by form_id.

    The Choice value is the form_id as a string — the command handler decodes
    it back to int. Two-pass: prefix matches first (most useful for typing),
    then substring matches to fill remaining slots.
    """
    current = (current or "").strip().lower()
    if current:
        prefix_like = current.replace("%", r"\%") + "%"
        sub_like = "%" + current.replace("%", r"\%") + "%"
        rows = list(conn.execute(
            "SELECT f.id, f.display_name, f.rarity, c.base_role "
            "FROM character_forms f "
            "JOIN characters c ON c.id = f.character_id "
            "WHERE LOWER(f.display_name) LIKE ? ESCAPE '\\' "
            "   OR LOWER(f.display_name) LIKE ? ESCAPE '\\' "
            "ORDER BY "
            "   CASE WHEN LOWER(f.display_name) LIKE ? ESCAPE '\\' THEN 0 ELSE 1 END, "
            "   f.rarity DESC, f.display_name "
            "LIMIT ?",
            (prefix_like, sub_like, prefix_like, AUTOCOMPLETE_LIMIT),
        ))
    else:
        rows = list(conn.execute(
            "SELECT f.id, f.display_name, f.rarity, c.base_role "
            "FROM character_forms f "
            "JOIN characters c ON c.id = f.character_id "
            "ORDER BY f.rarity DESC, f.display_name "
            "LIMIT ?",
            (AUTOCOMPLETE_LIMIT,),
        ))

    out: list[app_commands.Choice[str]] = []
    for r in rows:
        label = f"{r['display_name']} ({r['rarity'] or '?'} · {r['base_role'] or '?'})"
        # Discord caps choice name at 100 chars.
        if len(label) > 100:
            label = label[:99] + "…"
        out.append(app_commands.Choice(name=label, value=str(r["id"])))
    return out


def _resolve_form_id(conn: sqlite3.Connection, name_or_id: str) -> int | None:
    """User picked from autocomplete (form_id string) or typed free text."""
    raw = (name_or_id or "").strip()
    if not raw:
        return None
    try:
        candidate = int(raw)
    except ValueError:
        candidate = None
    if candidate is not None:
        if conn.execute(
            "SELECT 1 FROM character_forms WHERE id = ?", (candidate,)
        ).fetchone():
            return candidate

    # Free-text fallback: exact display_name match (case-insensitive), or
    # leftmost prefix match if exact misses.
    row = conn.execute(
        "SELECT id FROM character_forms "
        "WHERE LOWER(display_name) = LOWER(?) "
        "ORDER BY rarity DESC LIMIT 1",
        (raw,),
    ).fetchone()
    if row:
        return row["id"]
    row = conn.execute(
        "SELECT id FROM character_forms "
        "WHERE LOWER(display_name) LIKE LOWER(?) ESCAPE '\\' "
        "ORDER BY rarity DESC, display_name LIMIT 1",
        (raw.replace("%", r"\%") + "%",),
    ).fetchone()
    return row["id"] if row else None


# ----------------------------------------------------------------------------
# Filter autocomplete helpers (for /search)
# ----------------------------------------------------------------------------

def _filter_choices(values: list[str], current: str) -> list[app_commands.Choice[str]]:
    cur = (current or "").strip().lower()
    if cur:
        values = [v for v in values if cur in v.lower()]
    return [app_commands.Choice(name=v, value=v) for v in values[:AUTOCOMPLETE_LIMIT]]


# ----------------------------------------------------------------------------
# /refresh helpers
# ----------------------------------------------------------------------------

def _is_admin(user_id: int) -> bool:
    raw = config.get_setting("BOT_ADMIN_USER_IDS", "bot_admin_user_ids")
    return user_id in config.parse_admin_ids(raw)


# ----------------------------------------------------------------------------
# Registration
# ----------------------------------------------------------------------------

def register(tree: app_commands.CommandTree) -> None:
    """Attach all bot commands to the given tree."""

    @tree.command(name="character", description="Show full kit, affinities, and accessories for a CotC unit.")
    @app_commands.describe(name="Start typing a character name to see suggestions.")
    async def character_cmd(interaction: discord.Interaction, name: str) -> None:
        conn = bot_db.conn()
        form_id = _resolve_form_id(conn, name)
        if form_id is None:
            await interaction.response.send_message(
                f"No character matches `{name}`. Try `/search text:<keyword>`.",
                ephemeral=True,
            )
            return
        built = embeds.form_to_embed(conn, form_id)
        if built is None:
            await interaction.response.send_message(
                "That form was removed by a recent refresh — try again.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(embeds=built)

    @character_cmd.autocomplete("name")
    async def _character_ac(interaction: discord.Interaction, current: str):
        return _autocomplete_forms(bot_db.conn(), current)

    @tree.command(name="affinities", description="Quick weakness/element/weapon lookup for a CotC unit.")
    @app_commands.describe(name="Start typing a character name to see suggestions.")
    async def affinities_cmd(interaction: discord.Interaction, name: str) -> None:
        conn = bot_db.conn()
        form_id = _resolve_form_id(conn, name)
        if form_id is None:
            await interaction.response.send_message(
                f"No character matches `{name}`.", ephemeral=True,
            )
            return
        form = repo.get_form(conn, form_id)
        affs = repo.get_affinities(conn, form_id)
        if not form:
            await interaction.response.send_message("Not found.", ephemeral=True)
            return
        embed = discord.Embed(
            title=f"{embeds._rarity_prefix(form['rarity'])} {form['display_name']}".strip(),
            description=f"{(form['base_role'] or '?').title()} · {(form['base_weapon'] or '?').title()}",
        )
        groups = embeds._affinity_groups(affs)
        if not groups:
            embed.add_field(name="Affinities", value="_none recorded_", inline=False)
        else:
            for kind in ("weapon", "element", "weakness", "trait"):
                if kind in groups:
                    embed.add_field(
                        name=kind.title(), value=", ".join(groups[kind]), inline=True,
                    )
        await interaction.response.send_message(embed=embed)

    @affinities_cmd.autocomplete("name")
    async def _affinities_ac(interaction: discord.Interaction, current: str):
        return _autocomplete_forms(bot_db.conn(), current)

    @tree.command(name="search", description="Filter CotC units by role, weapon, rarity, weakness, or free text.")
    @app_commands.describe(
        role="Role (e.g. Warrior, Cleric)",
        weapon="Weapon (e.g. Sword, Tome)",
        rarity="Rarity (5*, 4*, 3*, free35)",
        weakness="Weakness label (e.g. Fire, Wind)",
        text="Free-text search across skills, equipment, and names",
    )
    async def search_cmd(
        interaction: discord.Interaction,
        role: str | None = None,
        weapon: str | None = None,
        rarity: str | None = None,
        weakness: str | None = None,
        text: str | None = None,
    ) -> None:
        conn = bot_db.conn()
        rows = repo.search_forms(
            conn,
            roles=[role] if role else None,
            weapons=[weapon] if weapon else None,
            rarities=[rarity] if rarity else None,
            weaknesses=[weakness] if weakness else None,
            text=text or None,
            limit=200,
        )
        summary_bits: list[str] = []
        if role: summary_bits.append(f"role={role}")
        if weapon: summary_bits.append(f"weapon={weapon}")
        if rarity: summary_bits.append(f"rarity={rarity}")
        if weakness: summary_bits.append(f"weakness={weakness}")
        if text: summary_bits.append(f"text=`{text}`")
        embed = embeds.search_results_to_embed(
            rows, query_summary=" · ".join(summary_bits) or "no filters",
        )
        await interaction.response.send_message(embed=embed)

    @search_cmd.autocomplete("role")
    async def _role_ac(interaction: discord.Interaction, current: str):
        return _filter_choices(repo.role_choices(bot_db.conn()), current)

    @search_cmd.autocomplete("weapon")
    async def _weapon_ac(interaction: discord.Interaction, current: str):
        return _filter_choices(repo.weapon_choices(bot_db.conn()), current)

    @search_cmd.autocomplete("rarity")
    async def _rarity_ac(interaction: discord.Interaction, current: str):
        return _filter_choices(repo.rarity_choices(bot_db.conn()), current)

    @search_cmd.autocomplete("weakness")
    async def _weakness_ac(interaction: discord.Interaction, current: str):
        return _filter_choices(
            repo.affinity_choices(bot_db.conn(), "weakness"), current,
        )

    @tree.command(name="refresh", description="(admin) Re-sync the local DB from the community spreadsheet.")
    async def refresh_cmd(interaction: discord.Interaction) -> None:
        if not _is_admin(interaction.user.id):
            await interaction.response.send_message(
                "You're not authorised to run /refresh.", ephemeral=True,
            )
            return
        api_key = config.get_setting("GOOGLE_API_KEY", "api_key")
        if not api_key:
            await interaction.response.send_message(
                "No Sheets API key configured (set `GOOGLE_API_KEY`).",
                ephemeral=True,
            )
            return
        if _lock().locked():
            await interaction.response.send_message(
                "A refresh is already running. Try again in a minute.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        async with _lock():
            loop = asyncio.get_running_loop()
            try:
                from sync.runner import run_sync  # local import: heavy deps
                summary: dict[str, Any] = await loop.run_in_executor(
                    None, run_sync, api_key,
                )
            except Exception as exc:
                logger.exception("/refresh failed")
                await interaction.followup.send(
                    f"Sync failed: `{exc}`", ephemeral=True,
                )
                return

        await interaction.followup.send(
            f"Sync OK. forms={summary.get('character_forms', '?')} · "
            f"skills={summary.get('skills', '?')} · "
            f"equipment={summary.get('equipment', '?')}",
            ephemeral=True,
        )
