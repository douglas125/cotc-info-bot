"""Slash command definitions: /character, /search, /enemy, /refresh.

Registers everything onto a `discord.app_commands.CommandTree` so the bot
client's `on_ready` can sync once at startup.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

import discord
from discord import app_commands

import config
from bot import db as bot_db
from bot import embeds, enemy_embeds
from bot.enemy_views import EnemyView
from bot.views import CharacterView
from db import repo

logger = logging.getLogger(__name__)

AUTOCOMPLETE_LIMIT = 25  # Discord's hard cap on choice list size.


def _ex_swap_variants(s: str) -> list[str]:
    """Return the input plus EX/EX2 prefix↔suffix swaps, deduped (case-insensitive)."""
    s = (s or "").strip()
    if not s:
        return []
    out = [s]
    low = s.lower()
    for prefix, suffix in (("ex2 ", " ex2"), ("ex ", " ex")):
        if low.startswith(prefix):
            rest = s[len(prefix):].strip()
            if rest:
                out.append(f"{rest}{suffix.upper()}")
        elif low.endswith(suffix):
            rest = s[: -len(suffix)].strip()
            if rest:
                out.append(f"{prefix.upper()}{rest}")
    seen: set[str] = set()
    deduped: list[str] = []
    for v in out:
        k = v.lower()
        if k not in seen:
            seen.add(k)
            deduped.append(v)
    return deduped

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
# Form-name autocomplete (used by /character)
# ----------------------------------------------------------------------------

def _autocomplete_forms(conn: sqlite3.Connection, current: str) -> list[app_commands.Choice[str]]:
    """Look up display_names matching `current`, returning Choices keyed by form_id.

    The Choice value is the form_id as a string — the command handler decodes
    it back to int. Two-pass: prefix matches first (most useful for typing),
    then substring matches to fill remaining slots.
    """
    current = (current or "").strip().lower()
    if current:
        # EX prefix↔suffix variants so typing "Castti EX" also matches stored
        # "EX Castti" (and vice versa). Original input ranks first.
        variants = [v.lower() for v in _ex_swap_variants(current)] or [current]
        like_clauses: list[str] = []
        params: list[Any] = []
        for v in variants:
            v_esc = v.replace("%", r"\%")
            like_clauses.append("LOWER(f.display_name) LIKE ? ESCAPE '\\'")
            params.append(v_esc + "%")
            like_clauses.append("LOWER(f.display_name) LIKE ? ESCAPE '\\'")
            params.append("%" + v_esc + "%")
        # Rank: prefix-on-original-input first.
        primary_prefix = variants[0].replace("%", r"\%") + "%"
        params.append(primary_prefix)
        params.append(AUTOCOMPLETE_LIMIT)
        rows = list(conn.execute(
            "SELECT f.id, f.display_name, f.rarity, c.base_role "
            "FROM character_forms f "
            "JOIN characters c ON c.id = f.character_id "
            f"WHERE {' OR '.join(like_clauses)} "
            "ORDER BY "
            "   CASE WHEN LOWER(f.display_name) LIKE ? ESCAPE '\\' THEN 0 ELSE 1 END, "
            "   f.rarity DESC, f.display_name "
            "LIMIT ?",
            params,
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
        rarity_disp = embeds._rarity_label(r["rarity"]) if r["rarity"] else "?"
        label = f"{r['display_name']} ({rarity_disp} · {r['base_role'] or '?'})"
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
    # leftmost prefix match if exact misses. Try EX prefix↔suffix swap
    # variants too so "Castti EX" resolves to stored "EX Castti".
    for variant in _ex_swap_variants(raw):
        row = conn.execute(
            "SELECT id FROM character_forms "
            "WHERE LOWER(display_name) = LOWER(?) "
            "ORDER BY rarity DESC LIMIT 1",
            (variant,),
        ).fetchone()
        if row:
            return row["id"]
    for variant in _ex_swap_variants(raw):
        row = conn.execute(
            "SELECT id FROM character_forms "
            "WHERE LOWER(display_name) LIKE LOWER(?) ESCAPE '\\' "
            "ORDER BY rarity DESC, display_name LIMIT 1",
            (variant.replace("%", r"\%") + "%",),
        ).fetchone()
        if row:
            return row["id"]
    return None


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
# Enemy-name autocomplete and resolution (used by /enemy)
# ----------------------------------------------------------------------------

def _autocomplete_enemies(
    conn: sqlite3.Connection, current: str,
) -> list[app_commands.Choice[str]]:
    """Return up to AUTOCOMPLETE_LIMIT choices, prefix matches first.

    Choice value is the stringified enemy_id, so the command handler skips
    free-text resolution when the user picked from the dropdown.
    """
    rows = repo.enemy_choices_by_name(conn, current, AUTOCOMPLETE_LIMIT * 2)
    if not rows:
        return []
    needle = (current or "").strip().lower()
    def rank_key(row: sqlite3.Row) -> tuple[int, str]:
        name = (row["canonical_name"] or "").lower()
        prefix = 0 if not needle or name.startswith(needle) else 1
        return (prefix, name)
    rows = sorted(rows, key=rank_key)[:AUTOCOMPLETE_LIMIT]
    out: list[app_commands.Choice[str]] = []
    for r in rows:
        category = r["category"] or ""
        label = f"{r['canonical_name']} — {category}" if category else r["canonical_name"]
        # Discord caps Choice.name at 100 chars.
        if len(label) > 100:
            label = label[:99] + "…"
        out.append(app_commands.Choice(name=label, value=str(r["enemy_id"])))
    return out


def _resolve_enemy_id(conn: sqlite3.Connection, name_or_id: str) -> int | None:
    """Resolve a /enemy `name` parameter to an enemy_id.

    Accepts either the stringified id from autocomplete or a raw name typed
    by the user. Falls back to a case-insensitive exact match, then prefix.
    """
    s = (name_or_id or "").strip()
    if not s:
        return None
    if s.isdigit():
        if conn.execute("SELECT 1 FROM enemies WHERE id = ?", (int(s),)).fetchone():
            return int(s)
    row = conn.execute(
        "SELECT id FROM enemies WHERE LOWER(canonical_name) = LOWER(?) "
        "ORDER BY id LIMIT 1",
        (s,),
    ).fetchone()
    if row:
        return row[0]
    row = conn.execute(
        "SELECT id FROM enemies WHERE LOWER(canonical_name) LIKE LOWER(?) "
        "ORDER BY id LIMIT 1",
        (f"{s}%",),
    ).fetchone()
    if row:
        return row[0]
    return None


# ----------------------------------------------------------------------------
# /feedback helpers
# ----------------------------------------------------------------------------

FEEDBACK_RATE_LIMIT = 3            # max submissions per user ...
FEEDBACK_RATE_WINDOW_SEC = 60      # ... per rolling window
FEEDBACK_MAX_LEN = 2000


def _parse_iso(ts: str) -> datetime:
    # repo._now_iso writes "%Y-%m-%dT%H:%M:%SZ" — naive UTC; attach tzinfo.
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


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
        embed = embeds.build_section_embed(conn, form_id, embeds.DEFAULT_SECTION)
        if embed is None:
            await interaction.response.send_message(
                "That form was removed by a recent refresh — try again.",
                ephemeral=True,
            )
            return
        view = CharacterView(form_id=form_id)
        await interaction.response.send_message(embed=embed, view=view)

    @character_cmd.autocomplete("name")
    async def _character_ac(interaction: discord.Interaction, current: str):
        return _autocomplete_forms(bot_db.conn(), current)

    @tree.command(name="enemy", description="Show stats and break shields for a CotC encounter at any rank.")
    @app_commands.describe(name="Start typing an enemy name to see suggestions.")
    async def enemy_cmd(interaction: discord.Interaction, name: str) -> None:
        conn = bot_db.conn()
        enemy_id = _resolve_enemy_id(conn, name)
        if enemy_id is None:
            await interaction.response.send_message(
                f"No enemy matches `{name}`. Try a shorter prefix.",
                ephemeral=True,
            )
            return
        ranks = enemy_embeds.available_ranks(conn, enemy_id)
        rank = enemy_embeds.default_rank(ranks)
        if rank is None:
            await interaction.response.send_message(
                "That enemy has no rank data yet — the maintainer hasn't filled it in.",
                ephemeral=True,
            )
            return
        embed = enemy_embeds.build_enemy_embed(conn, enemy_id, rank)
        if embed is None:
            await interaction.response.send_message(
                "That enemy was removed by a recent refresh — try again.",
                ephemeral=True,
            )
            return
        view = EnemyView(enemy_id=enemy_id, available_ranks=ranks, current_rank=rank)
        await interaction.response.send_message(embed=embed, view=view)

    @enemy_cmd.autocomplete("name")
    async def _enemy_ac(interaction: discord.Interaction, current: str):
        return _autocomplete_enemies(bot_db.conn(), current)

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

        unmatched = summary.get("unmatched_enemies") or []
        unmatched_note = ""
        if unmatched:
            unmatched_note = f" · enemies_unmatched={len(unmatched)}"
        await interaction.followup.send(
            f"Sync OK. forms={summary.get('character_forms', '?')} · "
            f"skills={summary.get('skills', '?')} · "
            f"equipment={summary.get('equipment', '?')} · "
            f"enemies={summary.get('enemies', '?')} · "
            f"enemy_forms={summary.get('enemy_forms', '?')}{unmatched_note}",
            ephemeral=True,
        )

    @tree.command(
        name="feedback",
        description="Flag a correction or inconsistency in the character data.",
    )
    @app_commands.describe(
        text=f"What's wrong or could be improved? (≤ {FEEDBACK_MAX_LEN} chars)",
    )
    async def feedback_cmd(
        interaction: discord.Interaction,
        text: app_commands.Range[str, 1, FEEDBACK_MAX_LEN],
    ) -> None:
        body = text.strip()
        if not body:
            await interaction.response.send_message(
                "Feedback can't be empty.", ephemeral=True,
            )
            return

        conn = bot_db.conn()
        now = datetime.now(timezone.utc)
        cutoff_iso = (now - timedelta(seconds=FEEDBACK_RATE_WINDOW_SEC)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        recent = repo.recent_feedback_timestamps(
            conn, interaction.user.id, cutoff_iso, limit=FEEDBACK_RATE_LIMIT,
        )
        if len(recent) >= FEEDBACK_RATE_LIMIT:
            # `recent` is ordered DESC, so the last element is the oldest in
            # the window — that's when the user can next submit.
            elapsed = (now - _parse_iso(recent[-1])).total_seconds()
            retry_in = max(1, FEEDBACK_RATE_WINDOW_SEC - int(elapsed))
            await interaction.response.send_message(
                f"Slow down — you've hit the {FEEDBACK_RATE_LIMIT}-per-"
                f"{FEEDBACK_RATE_WINDOW_SEC}s limit. Try again in {retry_in}s.",
                ephemeral=True,
            )
            return

        repo.insert_feedback(
            conn,
            user_id=interaction.user.id,
            username=str(interaction.user),
            guild_id=interaction.guild_id,
            feedback_text=body,
        )
        await interaction.response.send_message(
            "Thanks — your feedback was logged.", ephemeral=True,
        )

    @tree.command(
        name="feedback_list",
        description="(admin) Show the most recent community feedback submissions.",
    )
    @app_commands.describe(limit="How many entries to show (1–25, default 10).")
    async def feedback_list_cmd(
        interaction: discord.Interaction,
        limit: app_commands.Range[int, 1, 25] = 10,
    ) -> None:
        if not _is_admin(interaction.user.id):
            await interaction.response.send_message(
                "You're not authorised to read feedback.", ephemeral=True,
            )
            return
        rows = repo.list_feedback(bot_db.conn(), limit=limit)
        if not rows:
            await interaction.response.send_message(
                "No feedback yet.", ephemeral=True,
            )
            return
        await interaction.response.send_message(
            embed=embeds.feedback_results_to_embed(rows), ephemeral=True,
        )

    @tree.command(
        name="feedback_clear",
        description="(admin) Delete all stored feedback. Requires confirm:true.",
    )
    @app_commands.describe(
        confirm="Set to true to actually delete. Defaults to false (no-op).",
    )
    async def feedback_clear_cmd(
        interaction: discord.Interaction,
        confirm: bool = False,
    ) -> None:
        if not _is_admin(interaction.user.id):
            await interaction.response.send_message(
                "You're not authorised to clear feedback.", ephemeral=True,
            )
            return
        conn = bot_db.conn()
        if not confirm:
            await interaction.response.send_message(
                f"{repo.count_feedback(conn)} feedback entries on file. "
                f"Re-run with `confirm:true` to delete them all.",
                ephemeral=True,
            )
            return
        deleted = repo.clear_feedback(conn)
        await interaction.response.send_message(
            f"Deleted {deleted} feedback entr{'y' if deleted == 1 else 'ies'}.",
            ephemeral=True,
        )
