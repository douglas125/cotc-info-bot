"""Pure embed-building functions.

These take a sqlite3 connection plus a form_id (or a list of search rows)
and return a list of `discord.Embed`. They have no Discord runtime
dependency beyond the `Embed` data class — fully unit-testable.

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
MAX_EMBEDS_PER_MESSAGE = 10
TOTAL_CHARS_PER_MESSAGE = 6000

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
    """One-line markdown bullet — used for latent skills only.

    Other skill kinds render through the code-block table renderer
    (`_format_skill_table`) for tighter alignment.
    """
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


def _affinity_groups(affs: list[sqlite3.Row]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for a in affs:
        out.setdefault(a["kind"], []).append(a["icon_label"] or "?")
    return out


# --- skill table renderer ---------------------------------------------------

# Column widths in monospace cells. Description gets whatever's left after
# the fixed columns and gutters. Values are tuned to fit ~9 actives within
# a single 1024-char field; ultimates use a wider description column since
# they're rendered standalone.
_GUTTER = "  "
_COL_NUM_WIDTH = 2
_COL_SP_WIDTH = 4
_COL_BRD_WIDTH = 3
_COL_TIER_WIDTH = 4


def _pad(s: str, width: int) -> str:
    """Left-justify to `width` columns, treating each char as 1 cell.

    Discord renders code blocks in a true monospace font where the only
    visible-width surprises are CJK / wide unicode. The sheet doesn't use
    those in skill metadata, so simple ljust is sufficient and faster than
    pulling in `wcwidth`.
    """
    if len(s) >= width:
        return s
    return s + " " * (width - len(s))


def _row_str(cells: list[str]) -> str:
    return _GUTTER.join(cells).rstrip()


def _format_skill_table(
    skills: list[sqlite3.Row],
    *,
    columns: tuple[str, ...] = ("num", "sp", "brd"),
    desc_width: int = 50,
) -> str:
    """Render skills as a fenced ascii code block with aligned columns.

    `columns` is an ordered tuple of which fixed columns to include before
    the description column (which is always present and always last).
    Supported ids:
      "num"  — slot_order
      "sp"   — sp_cost
      "brd"  — learn_board (1..6 or "—")
      "tier" — tier_level (Lv1/Lv10/Lv20)

    Long descriptions are wrapped at `desc_width` and continuation lines
    indent under the description column for readability.
    """
    if not skills:
        return ""

    widths = {
        "num": _COL_NUM_WIDTH,
        "sp": _COL_SP_WIDTH,
        "brd": _COL_BRD_WIDTH,
        "tier": _COL_TIER_WIDTH,
    }
    header_labels = {"num": "#", "sp": "SP", "brd": "Brd", "tier": "Lv"}

    def _cell(col: str, s: sqlite3.Row) -> str:
        if col == "num":
            return "" if s["slot_order"] is None else str(s["slot_order"])
        if col == "sp":
            return "" if s["sp_cost"] is None else str(s["sp_cost"])
        if col == "brd":
            return str(s["learn_board"]) if s["learn_board"] else "—"
        if col == "tier":
            return f"Lv{s['tier_level']}" if s["tier_level"] else "—"
        return ""

    header = [_pad(header_labels[c], widths[c]) for c in columns] + ["Description"]
    sep = ["-" * widths[c] for c in columns] + ["-" * desc_width]

    lines: list[str] = ["```", _row_str(header), _row_str(sep)]
    fixed_prefix_width = sum(widths[c] for c in columns) + len(_GUTTER) * len(columns)
    cont_indent = " " * fixed_prefix_width

    for s in skills:
        fixed = [_pad(_cell(c, s), widths[c]) for c in columns]
        # Collapse newlines + runs of whitespace so wrapped lines don't
        # carry over the source-cell's indentation as visible gaps.
        desc = " ".join((s["description"] or "").split())
        # If the row has a skill name (rare in the live DB, common in
        # seeded tests), prepend it to the description.
        name = s["name"] or ""
        if name and desc:
            desc = f"{name} — {desc}"
        elif name:
            desc = name
        if not desc:
            lines.append(_row_str(fixed + [""]))
            continue
        wrapped = _wrap_desc(desc, desc_width)
        lines.append(_row_str(fixed + [wrapped[0]]))
        for cont in wrapped[1:]:
            lines.append(cont_indent + cont)

    lines.append("```")
    return "\n".join(lines)


def _wrap_desc(desc: str, width: int) -> list[str]:
    """Word-wrap to a max line width, never breaking mid-word."""
    if not desc:
        return [""]
    out: list[str] = []
    cur = ""
    for word in desc.split(" "):
        if not cur:
            cur = word
            continue
        if len(cur) + 1 + len(word) <= width:
            cur = f"{cur} {word}"
        else:
            out.append(cur)
            cur = word
    if cur:
        out.append(cur)
    return out or [""]


def _collapse_ultimates(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    """Fold consecutive ultimate rows that differ only by tier_level.

    Returns a list of synthesized records, each with:
      "headline"  — the Lv1 (or first) row's description, scaling tags stripped
      "tiers"     — list of (tier_level, description) for each level present
    Solo rows (no tiering) are returned with tiers = [(None, description)].
    Adjacency-based — `name` is NULL across the board in the live DB so we
    can't group by name.
    """
    out: list[dict[str, Any]] = []
    bucket: list[sqlite3.Row] = []

    def _flush() -> None:
        if not bucket:
            return
        # Sort by tier_level (Nones last) so Lv1 / Lv10 / Lv20 come out in order.
        ordered = sorted(
            bucket,
            key=lambda r: (r["tier_level"] is None, r["tier_level"] or 0),
        )
        headline = (ordered[0]["description"] or "").strip()
        tiers = [
            (r["tier_level"], (r["description"] or "").strip())
            for r in ordered
        ]
        out.append({"headline": headline, "tiers": tiers, "rows": ordered})

    for r in rows:
        # Group rows that share kind=='ultimate' and have a tier_level set.
        if r["tier_level"] is not None:
            bucket.append(r)
            continue
        # Untiered ultimate row breaks the run — flush prior bucket, emit solo.
        _flush()
        bucket = []
        out.append({
            "headline": (r["description"] or "").strip(),
            "tiers": [(None, (r["description"] or "").strip())],
            "rows": [r],
        })
    _flush()
    return out


def _format_ultimate_block(rows: list[sqlite3.Row]) -> str:
    """Render the Ultimate field value with Lv1/Lv10/Lv20 folded under one headline."""
    groups = _collapse_ultimates(rows)
    if not groups:
        return ""
    parts: list[str] = []
    for g in groups:
        tiers = g["tiers"]
        if len(tiers) <= 1:
            line = g["headline"] or "—"
            parts.append(line)
            continue
        # Multi-tier: show one headline (the Lv1 description) and per-tier
        # deltas. Each delta line is the tier description in full — the
        # sheet doesn't store deltas, only absolute values per tier.
        for tl, desc in tiers:
            tag = f"Lv{tl}" if tl else "—"
            parts.append(f"**{tag}** — {desc}")
    return _truncate("\n".join(parts), FIELD_VALUE_LIMIT)


# --- embed builders ---------------------------------------------------------


def _build_header_embed(
    form: sqlite3.Row,
    profile: sqlite3.Row | None,
    affs: list[sqlite3.Row],
) -> discord.Embed:
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

    if profile and profile["splash_art_url"]:
        url = _safe_url(profile["splash_art_url"])
        if url:
            embed.set_thumbnail(url=url)
            embed.set_image(url=url)

    groups = _affinity_groups(affs)
    for kind in ("weapon", "element", "weakness", "trait"):
        if kind in groups:
            embed.add_field(
                name=kind.title(),
                value=_truncate(", ".join(groups[kind]), FIELD_VALUE_LIMIT),
                inline=True,
            )

    return embed


def _build_skills_embed(
    form: sqlite3.Row, skills: list[sqlite3.Row]
) -> discord.Embed | None:
    if not skills:
        return None
    by_kind: dict[str, list[sqlite3.Row]] = {}
    for s in skills:
        by_kind.setdefault(s["kind"] or "active", []).append(s)

    embed = discord.Embed(
        title=f"Skills — {form['display_name']}",
        color=_color_from_hex(form["name_color_hex"]),
    )

    # Actives: render in the embed description (4096 cap) since the table
    # tends to be the longest section.
    if "active" in by_kind:
        table = _format_skill_table(
            by_kind["active"],
            columns=("num", "sp", "brd"),
            desc_width=50,
        )
        embed.description = _truncate(table, EMBED_DESCRIPTION_LIMIT)

    # Passive / Divine / EX → narrower tables in 1024-cap fields.
    # Passives have no SP cost; EX rows have neither SP nor board.
    field_specs: list[tuple[str, str, tuple[str, ...]]] = [
        ("passive", "Passive", ("num", "brd")),
        ("divine",  "Divine (TP)", ("num", "sp")),
        ("ex",      "EX", ("num",)),
    ]
    for key, title, cols in field_specs:
        if key not in by_kind or len(embed.fields) >= MAX_FIELDS:
            continue
        table = _format_skill_table(
            by_kind[key], columns=cols, desc_width=60,
        )
        if not table:
            continue
        embed.add_field(
            name=title,
            value=_truncate(table, FIELD_VALUE_LIMIT),
            inline=False,
        )

    # Ultimate: folded by tier_level via _format_ultimate_block. Markdown
    # bullet style (italics survive) since this is a short section.
    if "ultimate" in by_kind and len(embed.fields) < MAX_FIELDS:
        body = _format_ultimate_block(by_kind["ultimate"])
        if body:
            embed.add_field(name="Ultimate", value=body, inline=False)

    # Latent stays in markdown — its init/cd metadata renders better as a
    # bullet than as a table column.
    if "latent" in by_kind and len(embed.fields) < MAX_FIELDS:
        lines = [_format_skill_line(s) for s in by_kind["latent"]]
        embed.add_field(
            name="Latent",
            value=_truncate("\n".join(lines), FIELD_VALUE_LIMIT),
            inline=False,
        )

    # If no description and no fields landed, suppress the embed entirely.
    if not embed.description and not embed.fields:
        return None
    return embed


def _build_gear_embed(
    form: sqlite3.Row,
    equipment: list[sqlite3.Row],
    profile: sqlite3.Row | None,
    last_sync: sqlite3.Row | None,
) -> discord.Embed | None:
    has_equip = bool(equipment)
    has_profile = bool(profile and profile["self_buffs_text"])
    if not has_equip and not has_profile:
        return None

    embed = discord.Embed(color=_color_from_hex(form["name_color_hex"]))

    if has_equip:
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

    if has_profile:
        embed.add_field(
            name="Profile",
            value=_truncate(profile["self_buffs_text"], FIELD_VALUE_LIMIT),
            inline=False,
        )

    if last_sync:
        ts = last_sync["finished_at"] or last_sync["started_at"]
        embed.set_footer(text=f"synced {ts} · status: {last_sync['status']}")

    return embed


def form_to_embed(
    conn: sqlite3.Connection, form_id: int
) -> list[discord.Embed] | None:
    """Render a character form to a list of Discord embeds (1–3).

    Returns None if the form_id no longer exists. The caller should
    fall back to a "not found" message.

    Layout:
      [0] Header — name, role/weapon/rarity, affinities, artwork
      [1] Skills — actives in a code-block table; passive / divine / EX
                   as narrower tables; ultimate folded over tier levels;
                   latent as a markdown bullet (omitted entirely if the
                   form has no skills, which is rare but possible)
      [2] Gear & Profile — A4 accessories + self-buffs text (omitted
                   entirely if both are empty); footer with sync stamp
    """
    form = repo.get_form(conn, form_id)
    if not form:
        return None

    profile = repo.get_profile(conn, form_id)
    affs = repo.get_affinities(conn, form_id)
    skills = repo.get_skills(conn, form_id)
    equipment = repo.get_equipment(conn, form_id)
    last_sync = repo.latest_sync_run(conn)

    out: list[discord.Embed] = [_build_header_embed(form, profile, affs)]

    skills_embed = _build_skills_embed(form, skills)
    if skills_embed is not None:
        out.append(skills_embed)

    gear_embed = _build_gear_embed(form, equipment, profile, last_sync)
    if gear_embed is not None:
        out.append(gear_embed)
    elif last_sync is not None:
        # No gear/profile section but we still want the sync footer
        # somewhere — attach it to the last embed in the list.
        ts = last_sync["finished_at"] or last_sync["started_at"]
        out[-1].set_footer(text=f"synced {ts} · status: {last_sync['status']}")

    return out[:MAX_EMBEDS_PER_MESSAGE]


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
