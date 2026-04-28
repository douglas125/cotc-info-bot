"""Pure embed-building functions.

These take a sqlite3 connection plus a form_id (or a list of search rows)
and return a `discord.Embed`. They have no Discord runtime dependency
beyond the `Embed` data class — fully unit-testable.

The `/character` command surfaces five sections through a dropdown:
"actives" (default), "passives", "ultimate", "a4", and "info". Each
section is a self-contained embed; the dropdown swaps which one is shown.
"""
from __future__ import annotations

import sqlite3
import textwrap
from typing import Any, Literal

import discord

from config import SPREADSHEET_ID
from db import repo

_SHEET_BASE_URL = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit"

EMBED_DESCRIPTION_LIMIT = 4096
FIELD_VALUE_LIMIT = 1024
FIELD_NAME_LIMIT = 256
TITLE_LIMIT = 256
MAX_FIELDS = 25

Section = Literal["actives", "passives", "ultimate", "a4", "info"]
SECTIONS: tuple[Section, ...] = ("actives", "passives", "ultimate", "a4", "info")
SECTION_LABELS: dict[Section, str] = {
    "actives": "Active Skills",
    "passives": "Passive Skills",
    "ultimate": "Ultimate",
    "a4": "A4 Accessory",
    "info": "Info",
}
SECTION_DESCRIPTIONS: dict[Section, str] = {
    "actives": "Active skills, TP, and EX",
    "passives": "Passive skills and Latent Power",
    "ultimate": "Ultimate (Special) tiers",
    "a4": "A4 accessory and effect",
    "info": "Affinities, release info, source link",
}
DEFAULT_SECTION: Section = "actives"


def _safe_url(url: str | None, base_edit_url: str = _SHEET_BASE_URL) -> str | None:
    """Return a Discord-acceptable URL.

    The Sheets API returns in-doc anchors (`#rangeid=...`, `#gid=...`) as
    fragment-only strings — Discord rejects those for `Embed.url`. Prefix
    fragments with `base_edit_url` so they resolve to the intended cell
    when opened. The `bot.enemy_embeds` module passes the enemy
    spreadsheet's edit URL.
    """
    if not url:
        return None
    if url.startswith(("http://", "https://")):
        return url
    if url.startswith("#"):
        return base_edit_url + url
    return None


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
    """Star glyphs for the embed title. ⭐ instead of ``*`` so Discord doesn't
    parse it as italic markdown."""
    if rarity == "5*":
        return "⭐⭐⭐⭐⭐"
    if rarity == "4*":
        return "⭐⭐⭐⭐"
    if rarity == "3*":
        return "⭐⭐⭐"
    if rarity == "free35":
        return "⭐⭐⭐→⭐⭐⭐⭐⭐"
    return ""


def _rarity_label(rarity: str | None) -> str:
    """Compact rarity label (``5⭐``) for description and search lines."""
    if rarity in ("5*", "4*", "3*"):
        return f"{rarity[0]}⭐"
    if rarity == "free35":
        return "3⭐→5⭐"
    return rarity or "?"


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _format_skill_line(s: sqlite3.Row) -> str:
    """One-line markdown bullet for a skill row."""
    bits: list[str] = []
    if s["sp_cost"] is not None:
        bits.append(f"`{s['sp_cost']} SP`")
    if s["learn_board"]:
        bits.append(f"`{s['learn_board']}⭐`")
    elif s["kind"] == "tp_passive":
        bits.append("`TP`")
    if s["tier_level"]:
        bits.append(f"`Lv{s['tier_level']}`")
    name = s["name"] or ""
    if name:
        bits.append(f"**{name}**")
    desc = " ".join((s["description"] or "").split())
    if s["kind"] == "latent" and (s["initial_use"] or s["cooldown"]):
        prefix = []
        if s["initial_use"]:
            prefix.append(f"init {s['initial_use']}t")
        if s["cooldown"]:
            prefix.append(f"cd {s['cooldown']}t")
        desc = f"[{' / '.join(prefix)}] {desc}"
    head = " ".join(bits).strip()
    if desc:
        return f"• {head} — {desc}" if head else f"• {desc}"
    return f"• {head}" if head else "—"


def _split_oversize_bullet(bullet: str) -> list[str]:
    """Word-boundary split for a single bullet that itself exceeds the cap.

    `textwrap.wrap` with `break_long_words=False` guarantees we never cut
    mid-word; it returns the next-best fit at whitespace. Only when even
    one token is longer than the cap (pathological) do we fall through to
    a hard truncate.
    """
    if len(bullet) <= FIELD_VALUE_LIMIT:
        return [bullet]
    pieces = textwrap.wrap(
        bullet,
        width=FIELD_VALUE_LIMIT,
        break_long_words=False,
        break_on_hyphens=False,
    )
    if not pieces:
        return [_truncate(bullet, FIELD_VALUE_LIMIT)]
    return pieces


def _split_bullets_into_field_values(bullets: list[str]) -> list[str]:
    """Pack pre-rendered bullet lines into chunks ≤ ``FIELD_VALUE_LIMIT``.

    Splits only between bullets — never inside one — so a kit's skill
    list stays readable when it overflows Discord's 1024-char field cap.
    Concatenating the returned chunks with ``\\n`` reproduces
    ``"\\n".join(bullets)`` exactly (after any oversize-bullet
    word-wrapping), which makes the round-trip property easy to assert.
    """
    if not bullets:
        return []
    chunks: list[str] = []
    current = ""
    for raw in bullets:
        for piece in _split_oversize_bullet(raw):
            if not current:
                current = piece
                continue
            if len(current) + 1 + len(piece) <= FIELD_VALUE_LIMIT:
                current = f"{current}\n{piece}"
            else:
                chunks.append(current)
                current = piece
    if current:
        chunks.append(current)
    return chunks


def _skill_field_values(skills: list[sqlite3.Row]) -> list[str]:
    return _split_bullets_into_field_values(
        [_format_skill_line(s) for s in skills],
    )


SKILL_KIND_TITLES = {
    "active": "Active",
    "passive": "Passive",
    "divine": "TP",
    "ex": "EX",
    "ultimate": "Ultimate",
    "latent": "Latent",
}
ACTIVE_KIND_ORDER = ("active", "divine", "ex")
PASSIVE_KIND_ORDER = ("passive", "latent")


def _affinity_groups(affs: list[sqlite3.Row]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for a in affs:
        out.setdefault(a["kind"], []).append(a["icon_label"] or "?")
    return out


def _collapse_ultimates(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    """Fold consecutive ultimate rows that differ only by tier_level.

    Returns a list of synthesized records, each with:
      "headline" — the lowest-tier (or only) row's description
      "tiers"    — list of (tier_level, description) for each level present
    Adjacency-based — `name` is NULL across the live DB so we can't group
    by name. Untiered rows form their own single-tier group.
    """
    out: list[dict[str, Any]] = []
    bucket: list[sqlite3.Row] = []

    def _flush() -> None:
        if not bucket:
            return
        ordered = sorted(
            bucket,
            key=lambda r: (r["tier_level"] is None, r["tier_level"] or 0),
        )
        tiers = [
            (r["tier_level"], (r["description"] or "").strip())
            for r in ordered
        ]
        out.append({"headline": tiers[0][1], "tiers": tiers})

    for r in rows:
        if r["tier_level"] is not None:
            bucket.append(r)
            continue
        _flush()
        bucket = []
        desc = (r["description"] or "").strip()
        out.append({"headline": desc, "tiers": [(None, desc)]})
    _flush()
    return out


def _ultimate_field_values(rows: list[sqlite3.Row]) -> list[str]:
    groups = _collapse_ultimates(rows)
    if not groups:
        return []
    parts: list[str] = []
    for g in groups:
        tiers = g["tiers"]
        if len(tiers) <= 1:
            parts.append(g["headline"] or "—")
            continue
        for tl, desc in tiers:
            tag = f"Lv{tl}" if tl else "—"
            parts.append(f"**{tag}** — {desc}")
    return _split_bullets_into_field_values(parts)


def _new_header_embed(form: sqlite3.Row) -> discord.Embed:
    rarity = form["rarity"]
    title_raw = f"{_rarity_prefix(rarity)} {form['display_name']}".strip()
    embed = discord.Embed(
        title=_truncate(title_raw, TITLE_LIMIT),
        url=_safe_url(form["hyperlink_url"]),
        color=_color_from_hex(form["name_color_hex"]),
    )
    role = (form["base_role"] or "?").title()
    weapon = (form["base_weapon"] or "?").title()
    primary = f"**{role}** · **{weapon}** · {_rarity_label(rarity)}"

    tags: list[str] = []
    variant = form["variant_kind"] or "base"
    if variant == "ex":
        tags.append("EX form")
    elif variant == "ex2":
        tags.append("EX2 form")
    elif variant == "alt":
        tags.append("alt form")
    if (form["server"] or "global") == "sea":
        tags.append("SEA only")

    if tags:
        embed.description = _truncate(
            f"{primary}\n*{' · '.join(tags)}*", EMBED_DESCRIPTION_LIMIT,
        )
    else:
        embed.description = _truncate(primary, EMBED_DESCRIPTION_LIMIT)
    return embed


def _attach_footer(embed: discord.Embed, last_sync: sqlite3.Row | None) -> None:
    if not last_sync:
        return
    ts = last_sync["finished_at"] or last_sync["started_at"]
    embed.set_footer(text=f"synced {ts} · status: {last_sync['status']}")


def _group_by_kind(skills: list[sqlite3.Row]) -> dict[str, list[sqlite3.Row]]:
    by_kind: dict[str, list[sqlite3.Row]] = {}
    for s in skills:
        by_kind.setdefault(s["kind"] or "active", []).append(s)
    return by_kind


def _add_chunked_fields(
    embed: discord.Embed, base_title: str, chunks: list[str],
) -> None:
    """Add one field per chunk; suffix names with ``(n/N)`` when N > 1.

    Stops early at ``MAX_FIELDS`` so callers don't overflow Discord's 25-
    fields-per-embed cap.
    """
    total = len(chunks)
    for idx, value in enumerate(chunks, start=1):
        if len(embed.fields) >= MAX_FIELDS:
            return
        name = base_title if total == 1 else f"{base_title} ({idx}/{total})"
        embed.add_field(name=name, value=value, inline=False)


def _build_skill_kinds_section(
    form: sqlite3.Row,
    skills: list[sqlite3.Row],
    kind_order: tuple[str, ...],
    empty_field_name: str,
    empty_message: str,
) -> discord.Embed:
    embed = _new_header_embed(form)
    by_kind = _group_by_kind(skills)
    # Fold tp_passive into the passive group so it renders as a `TP`
    # badge inside the Passive field, not a separate kind.
    if (
        "passive" in kind_order
        and "tp_passive" not in kind_order
        and "tp_passive" in by_kind
    ):
        merged = by_kind.get("passive", []) + by_kind.pop("tp_passive")
        merged.sort(key=lambda s: s["slot_order"])
        by_kind["passive"] = merged
    for kind in kind_order:
        if kind not in by_kind or len(embed.fields) >= MAX_FIELDS:
            continue
        chunks = _skill_field_values(by_kind[kind])
        if not chunks:
            continue
        _add_chunked_fields(embed, SKILL_KIND_TITLES[kind], chunks)
    if not embed.fields:
        embed.add_field(name=empty_field_name, value=empty_message, inline=False)
    return embed


def _build_ultimate_section(form: sqlite3.Row, skills: list[sqlite3.Row]) -> discord.Embed:
    embed = _new_header_embed(form)
    ult = [s for s in skills if (s["kind"] or "") == "ultimate"]
    chunks = _ultimate_field_values(ult) if ult else []
    if not chunks:
        embed.add_field(
            name="Ultimate",
            value="_No ultimate recorded for this form (may be unreleased)._",
            inline=False,
        )
        return embed
    _add_chunked_fields(embed, "Ultimate", chunks)
    return embed


def _build_a4_section(form: sqlite3.Row, equipment: list[sqlite3.Row]) -> discord.Embed:
    embed = _new_header_embed(form)
    if not equipment:
        embed.add_field(
            name="A4 Accessory",
            value="_No A4 accessory recorded for this form._",
            inline=False,
        )
        return embed
    lines = []
    for e in equipment:
        badge = " *(exclusive)*" if e["is_exclusive"] else ""
        line = f"• **{e['name']}**{badge}"
        if e["description"]:
            line += f" — {e['description']}"
        lines.append(line)
    embed.add_field(
        name="A4 Accessory",
        value=_truncate("\n".join(lines), FIELD_VALUE_LIMIT),
        inline=False,
    )
    return embed


def _build_info_section(
    form: sqlite3.Row,
    profile: sqlite3.Row | None,
    affs: list[sqlite3.Row],
) -> discord.Embed:
    embed = _new_header_embed(form)

    groups = _affinity_groups(affs)
    for kind in ("weapon", "element", "weakness", "trait"):
        if kind in groups:
            embed.add_field(
                name=kind.title(),
                value=_truncate(", ".join(groups[kind]), FIELD_VALUE_LIMIT),
                inline=True,
            )

    if profile and profile["self_buffs_text"]:
        embed.add_field(
            name="Profile",
            value=_truncate(profile["self_buffs_text"], FIELD_VALUE_LIMIT),
            inline=False,
        )

    # Click-through hint: the title is already a hyperlink to the role-tab
    # cell, where the inserted-image artwork (pixel + splash) is visible.
    if _safe_url(form["hyperlink_url"]):
        embed.add_field(
            name="Character Art",
            value="Click the title above to open the spreadsheet at this character's row — pixel art and splash art are visible there.",
            inline=False,
        )
    return embed


def build_section_embed(
    conn: sqlite3.Connection, form_id: int, section: Section,
) -> discord.Embed | None:
    """Build the embed for one dropdown section.

    Returns None if the form_id no longer exists (caller should fall back
    to a "not found" message). Each section reuses the common header
    (title / URL / color / role-weapon-rarity description) so the user
    never loses context when switching options.
    """
    form = repo.get_form(conn, form_id)
    if not form:
        return None

    last_sync = repo.latest_sync_run(conn)

    if section == "actives":
        embed = _build_skill_kinds_section(
            form, repo.get_skills(conn, form_id), ACTIVE_KIND_ORDER,
            "Active Skills", "_No active skills recorded for this form._",
        )
    elif section == "passives":
        embed = _build_skill_kinds_section(
            form, repo.get_skills(conn, form_id), PASSIVE_KIND_ORDER,
            "Passive Skills", "_No passive skills recorded for this form._",
        )
    elif section == "ultimate":
        embed = _build_ultimate_section(form, repo.get_skills(conn, form_id))
    elif section == "a4":
        embed = _build_a4_section(form, repo.get_equipment(conn, form_id))
    elif section == "info":
        embed = _build_info_section(
            form,
            repo.get_profile(conn, form_id),
            repo.get_affinities(conn, form_id),
        )
    else:
        return None

    _attach_footer(embed, last_sync)
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
        role = (r["base_role"] or "?").title()
        weapon = (r["base_weapon"] or "?").title()
        lines.append(
            f"• **{r['display_name']}** — "
            f"{role} · {weapon} · {_rarity_label(r['rarity'])}"
        )
    embed.add_field(
        name="Top results",
        value=_truncate("\n".join(lines), FIELD_VALUE_LIMIT),
        inline=False,
    )
    if len(rows) > len(shown):
        embed.set_footer(text=f"showing {len(shown)} of {len(rows)} — narrow filters to see more")
    return embed


def feedback_results_to_embed(rows: list[Any]) -> discord.Embed:
    """Render the admin /feedback_list result. `rows` are sqlite3.Rows from list_feedback."""
    embed = discord.Embed(
        title=f"Latest {len(rows)} feedback submission(s)",
        color=discord.Color.blurple(),
    )
    for r in rows:
        body = r["feedback_text"] or "—"
        embed.add_field(
            name=_truncate(
                f"#{r['id']} · {r['username']} · {r['submitted_at']}",
                FIELD_NAME_LIMIT,
            ),
            value=_truncate(body, FIELD_VALUE_LIMIT),
            inline=False,
        )
    return embed
