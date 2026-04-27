"""Pure embed-building functions.

These take a sqlite3 connection plus a form_id (or a list of search rows)
and return a `discord.Embed`. They have no Discord runtime dependency
beyond the `Embed` data class — fully unit-testable.

Layout mirrors app.py L168–237 so the Streamlit and Discord views stay in
sync.
"""
from __future__ import annotations

import sqlite3
from typing import Any

import discord

from db import repo

EMBED_DESCRIPTION_LIMIT = 4096
FIELD_VALUE_LIMIT = 1024
FIELD_NAME_LIMIT = 256
TITLE_LIMIT = 256
MAX_FIELDS = 25

# Discord.py rejects hyperlinks unless they look like real URLs. The Sheets
# anchor URLs we store always start with https://, so this is a guard for
# legacy rows / future schemas where the column might be empty.
def _safe_url(url: str | None) -> str | None:
    if not url:
        return None
    return url if url.startswith(("http://", "https://")) else None


def _color_from_hex(hex_color: str | None) -> discord.Color | None:
    if not hex_color:
        return None
    s = hex_color.lstrip("#")
    if len(s) != 6:
        return None
    try:
        return discord.Color(int(s, 16))
    except ValueError:
        return None


def _rarity_prefix(rarity: str | None) -> str:
    if rarity == "5*":
        return "★★★★★"
    if rarity == "4*":
        return "★★★★"
    if rarity == "3*":
        return "★★★"
    if rarity == "free35":
        return "★★★→★★★★★"
    return ""


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    # Leave room for the ellipsis.
    return text[: max(0, limit - 1)].rstrip() + "…"


def _format_skill_line(s: sqlite3.Row) -> str:
    """One line per skill, mirrors the Streamlit dataframe's flattened form."""
    bits: list[str] = []
    if s["slot_order"] is not None:
        bits.append(f"**{s['slot_order']}.**")
    if s["sp_cost"] is not None:
        bits.append(f"`{s['sp_cost']} SP`")
    if s["learn_board"]:
        bits.append(f"[{s['learn_board']}*]")
    if s["tier_level"]:
        bits.append(f"`Lv{s['tier_level']}`")
    name = s["name"] or ""
    if name:
        bits.append(f"**{name}**")
    desc = s["description"] or ""
    if s["kind"] == "latent" and (s["initial_use"] or s["cooldown"]):
        prefix = []
        if s["initial_use"]:
            prefix.append(f"init {s['initial_use']}t")
        if s["cooldown"]:
            prefix.append(f"cd {s['cooldown']}t")
        desc = f"[{' / '.join(prefix)}] {desc}"
    head = " ".join(bits).strip()
    if desc:
        return f"{head} — {desc}" if head else desc
    return head or "—"


# Order matches the in-game card layout users expect.
SKILL_KIND_ORDER = ("active", "passive", "divine", "ex", "ultimate", "latent")
SKILL_KIND_TITLES = {
    "active": "Active",
    "passive": "Passive",
    "divine": "Divine (TP)",
    "ex": "EX",
    "ultimate": "Ultimate",
    "latent": "Latent",
}


def _skill_field_value(skills: list[sqlite3.Row]) -> str:
    lines = [_format_skill_line(s) for s in skills]
    return _truncate("\n".join(lines), FIELD_VALUE_LIMIT)


def _affinity_groups(affs: list[sqlite3.Row]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for a in affs:
        out.setdefault(a["kind"], []).append(a["icon_label"] or "?")
    return out


def form_to_embed(conn: sqlite3.Connection, form_id: int) -> discord.Embed | None:
    """Render a single character form to a Discord embed.

    Returns None if the form_id no longer exists (e.g. resolved against a
    pre-refresh ID). The caller should fall back to a "not found" message.
    """
    form = repo.get_form(conn, form_id)
    if not form:
        return None

    rarity = form["rarity"]
    title_raw = f"{_rarity_prefix(rarity)} {form['display_name']}".strip()
    embed = discord.Embed(
        title=_truncate(title_raw, TITLE_LIMIT),
        url=_safe_url(form["hyperlink_url"]),
        color=_color_from_hex(form["name_color_hex"]),
    )

    role = form["base_role"] or "?"
    weapon = form["base_weapon"] or "?"
    server = form["server"] or "global"
    rarity_disp = rarity or "?"
    embed.description = _truncate(
        f"**{role.title()}** · **{weapon.title()}** · {rarity_disp} · `{server}`",
        EMBED_DESCRIPTION_LIMIT,
    )

    profile = repo.get_profile(conn, form_id)
    if profile and profile["splash_art_url"]:
        url = _safe_url(profile["splash_art_url"])
        if url:
            embed.set_thumbnail(url=url)

    affs = repo.get_affinities(conn, form_id)
    groups = _affinity_groups(affs)
    for kind in ("weapon", "element", "weakness", "trait"):
        if kind in groups:
            embed.add_field(
                name=kind.title(),
                value=_truncate(", ".join(groups[kind]), FIELD_VALUE_LIMIT),
                inline=True,
            )

    skills = repo.get_skills(conn, form_id)
    if skills:
        by_kind: dict[str, list[sqlite3.Row]] = {}
        for s in skills:
            by_kind.setdefault(s["kind"] or "active", []).append(s)
        for kind in SKILL_KIND_ORDER:
            if kind in by_kind and len(embed.fields) < MAX_FIELDS:
                embed.add_field(
                    name=SKILL_KIND_TITLES[kind],
                    value=_skill_field_value(by_kind[kind]),
                    inline=False,
                )

    equipment = repo.get_equipment(conn, form_id)
    if equipment and len(embed.fields) < MAX_FIELDS:
        lines = []
        for e in equipment:
            badge = " *(exclusive)*" if e["is_exclusive"] else ""
            line = f"• **{e['name']}**{badge}"
            if e["description"]:
                line += f" — {e['description']}"
            lines.append(line)
        embed.add_field(
            name="A4 Accessories",
            value=_truncate("\n".join(lines), FIELD_VALUE_LIMIT),
            inline=False,
        )

    if profile and profile["self_buffs_text"] and len(embed.fields) < MAX_FIELDS:
        embed.add_field(
            name="Profile",
            value=_truncate(profile["self_buffs_text"], FIELD_VALUE_LIMIT),
            inline=False,
        )

    last = repo.latest_sync_run(conn)
    if last:
        ts = last["finished_at"] or last["started_at"]
        embed.set_footer(text=f"synced {ts} · status: {last['status']}")

    return embed


def search_results_to_embed(rows: list[Any], *, query_summary: str) -> discord.Embed:
    """Render a search result list. `rows` are sqlite3.Rows from search_forms."""
    embed = discord.Embed(
        title=f"Search results — {len(rows)} match" + ("" if len(rows) == 1 else "es"),
        color=discord.Color.blurple(),
    )
    if query_summary:
        embed.description = _truncate(query_summary, EMBED_DESCRIPTION_LIMIT)

    if not rows:
        embed.add_field(
            name="No matches",
            value="Loosen the filters or try `/refresh` if data feels stale.",
            inline=False,
        )
        return embed

    shown = rows[:10]
    lines = []
    for r in shown:
        rarity = r["rarity"] or "?"
        lines.append(
            f"• **{r['display_name']}** — "
            f"{r['base_role'] or '?'}/{r['base_weapon'] or '?'} · {rarity}"
        )
    embed.add_field(
        name="Top results",
        value=_truncate("\n".join(lines), FIELD_VALUE_LIMIT),
        inline=False,
    )
    if len(rows) > len(shown):
        embed.set_footer(text=f"showing {len(shown)} of {len(rows)} — narrow filters to see more")
    return embed
