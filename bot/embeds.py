"""Pure embed-building functions.

These take a sqlite3 connection plus a form_id (or a list of search rows)
and return a `discord.Embed`. They have no Discord runtime dependency
beyond the `Embed` data class — fully unit-testable.

The `/character` command surfaces five sections through a dropdown:
"actives" (default), "passives", "ultimate", "a4", and "info". Each
section is a self-contained embed; the dropdown swaps which one is shown.
"""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import sqlite3
from typing import Any, Literal

import discord

from bot import character_images
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


@dataclass(frozen=True)
class CharacterMessage:
    embed: discord.Embed
    file: discord.File | None = None


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


def _skill_field_value(skills: list[sqlite3.Row]) -> str:
    return _truncate(
        "\n".join(_format_skill_line(s) for s in skills),
        FIELD_VALUE_LIMIT,
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


def _format_ultimate_block(rows: list[sqlite3.Row]) -> str:
    groups = _collapse_ultimates(rows)
    if not groups:
        return ""
    parts: list[str] = []
    for g in groups:
        tiers = g["tiers"]
        if len(tiers) <= 1:
            parts.append(g["headline"] or "—")
            continue
        for tl, desc in tiers:
            tag = f"Lv{tl}" if tl else "—"
            parts.append(f"**{tag}** — {desc}")
    return _truncate("\n".join(parts), FIELD_VALUE_LIMIT)


def _new_header_embed(form: sqlite3.Row, *, include_description: bool = True) -> discord.Embed:
    rarity = form["rarity"]
    title_raw = f"{_rarity_prefix(rarity)} {form['display_name']}".strip()
    embed = discord.Embed(
        title=_truncate(title_raw, TITLE_LIMIT),
        url=_safe_url(form["hyperlink_url"]),
        color=_color_from_hex(form["name_color_hex"]),
    )
    if not include_description:
        return embed

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


def _build_skill_kinds_section(
    form: sqlite3.Row,
    skills: list[sqlite3.Row],
    kind_order: tuple[str, ...],
    empty_field_name: str,
    empty_message: str,
) -> discord.Embed:
    embed = _new_header_embed(form)
    by_kind = _group_by_kind(skills)
    for kind in kind_order:
        if kind not in by_kind or len(embed.fields) >= MAX_FIELDS:
            continue
        value = _skill_field_value(by_kind[kind])
        if not value:
            continue
        embed.add_field(name=SKILL_KIND_TITLES[kind], value=value, inline=False)
    if not embed.fields:
        embed.add_field(name=empty_field_name, value=empty_message, inline=False)
    return embed


def _build_ultimate_section(form: sqlite3.Row, skills: list[sqlite3.Row]) -> discord.Embed:
    embed = _new_header_embed(form)
    ult = [s for s in skills if (s["kind"] or "") == "ultimate"]
    value = _format_ultimate_block(ult) if ult else ""
    embed.add_field(
        name="Ultimate",
        value=value or "_No ultimate recorded for this form (may be unreleased)._",
        inline=False,
    )
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


def _plain_skill_line(s: sqlite3.Row) -> str:
    bits: list[str] = []
    if s["sp_cost"] is not None:
        bits.append(f"{s['sp_cost']} SP")
    if s["learn_board"]:
        bits.append(f"{s['learn_board']}*")
    if s["tier_level"]:
        bits.append(f"Lv{s['tier_level']}")
    if s["name"]:
        bits.append(str(s["name"]))

    desc = " ".join((s["description"] or "").split())
    if s["kind"] == "latent" and (s["initial_use"] or s["cooldown"]):
        prefix = []
        if s["initial_use"]:
            prefix.append(f"init {s['initial_use']}t")
        if s["cooldown"]:
            prefix.append(f"cd {s['cooldown']}t")
        desc = f"[{' / '.join(prefix)}] {desc}"

    head = " | ".join(bits)
    if desc and head:
        return f"- {head} - {desc}"
    if desc:
        return f"- {desc}"
    if head:
        return f"- {head}"
    return "-"


def _plain_skill_sections(
    skills: list[sqlite3.Row],
    kind_order: tuple[str, ...],
    empty_title: str,
    empty_message: str,
) -> list[character_images.PanelSection]:
    by_kind = _group_by_kind(skills)
    sections: list[character_images.PanelSection] = []
    for kind in kind_order:
        rows = by_kind.get(kind)
        if not rows:
            continue
        sections.append(character_images.PanelSection(
            title=SKILL_KIND_TITLES[kind],
            lines=[_plain_skill_line(row) for row in rows],
        ))
    if not sections:
        sections.append(character_images.PanelSection(empty_title, [empty_message]))
    return sections


def _plain_ultimate_lines(rows: list[sqlite3.Row]) -> list[str]:
    groups = _collapse_ultimates(rows)
    if not groups:
        return ["No ultimate recorded for this form (may be unreleased)."]
    lines: list[str] = []
    for group in groups:
        tiers = group["tiers"]
        if len(tiers) <= 1:
            lines.append(group["headline"] or "-")
            continue
        for tier_level, desc in tiers:
            tag = f"Lv{tier_level}" if tier_level else "-"
            lines.append(f"{tag} - {desc}")
    return lines


def _character_header_lines(form: sqlite3.Row) -> list[str]:
    rarity = form["rarity"]
    role = (form["base_role"] or "?").title()
    weapon = (form["base_weapon"] or "?").title()
    lines = [f"{role} - {weapon} - {_rarity_label(rarity)}"]

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
        lines.append(" - ".join(tags))
    return lines


def _character_filename(form_id: int, section: Section) -> str:
    return f"character_{form_id}_{section}.png"


def _build_character_parts(
    conn: sqlite3.Connection,
    form_id: int,
    section: Section,
) -> tuple[discord.Embed, list[str], list[character_images.PanelSection]] | None:
    form = repo.get_form(conn, form_id)
    if not form:
        return None

    embed = _new_header_embed(form, include_description=False)
    header_lines = _character_header_lines(form)

    if section == "actives":
        sections = _plain_skill_sections(
            repo.get_skills(conn, form_id), ACTIVE_KIND_ORDER,
            "Active Skills", "No active skills recorded for this form.",
        )
    elif section == "passives":
        sections = _plain_skill_sections(
            repo.get_skills(conn, form_id), PASSIVE_KIND_ORDER,
            "Passive Skills", "No passive skills recorded for this form.",
        )
    elif section == "ultimate":
        rows = [s for s in repo.get_skills(conn, form_id) if (s["kind"] or "") == "ultimate"]
        sections = [character_images.PanelSection("Ultimate", _plain_ultimate_lines(rows))]
    elif section == "a4":
        equipment = repo.get_equipment(conn, form_id)
        lines: list[str] = []
        for item in equipment:
            badge = " (exclusive)" if item["is_exclusive"] else ""
            line = f"- {item['name']}{badge}"
            if item["description"]:
                line += f" - {item['description']}"
            lines.append(line)
        if not lines:
            lines = ["No A4 accessory recorded for this form."]
        sections = [character_images.PanelSection("A4 Accessory", lines)]
    elif section == "info":
        affs = _affinity_groups(repo.get_affinities(conn, form_id))
        lines = []
        for kind in ("weapon", "element", "weakness", "trait"):
            if kind in affs:
                lines.append(f"{kind.title()}: {', '.join(affs[kind])}")
        sections = [character_images.PanelSection("Affinities", lines or ["No affinities recorded."])]

        profile = repo.get_profile(conn, form_id)
        if profile and profile["self_buffs_text"]:
            sections.append(character_images.PanelSection("Profile", [profile["self_buffs_text"]]))
        if _safe_url(form["hyperlink_url"]):
            sections.append(character_images.PanelSection(
                "Character Art",
                ["Open the spreadsheet link in the title to view pixel art and splash art."],
            ))
    else:
        return None

    _attach_footer(embed, repo.latest_sync_run(conn))
    return embed, header_lines, sections


def build_section_embed(
    conn: sqlite3.Connection, form_id: int, section: Section,
) -> discord.Embed | None:
    """Build the embed for one dropdown section.

    Returns None if the form_id no longer exists (caller should fall back
    to a "not found" message). The section body is rendered into an image
    attachment; this helper sets the attachment URL for tests and callers
    that only need to inspect the embed shape. Runtime sends should use
    `build_character_message` so the referenced file is attached.
    """
    parts = _build_character_parts(conn, form_id, section)
    if parts is None:
        return None
    embed, header_lines, sections = parts
    rendered = character_images.render_character_panel(
        filename=_character_filename(form_id, section),
        header_lines=header_lines,
        sections=sections,
    )
    embed.set_image(url=f"attachment://{rendered.filename}")
    return embed


def build_character_message(
    conn: sqlite3.Connection, form_id: int, section: Section,
) -> CharacterMessage | None:
    """Build the `/character` embed plus the rendered section attachment."""
    parts = _build_character_parts(conn, form_id, section)
    if parts is None:
        return None
    embed, header_lines, sections = parts
    rendered = character_images.render_character_panel(
        filename=_character_filename(form_id, section),
        header_lines=header_lines,
        sections=sections,
    )
    embed.set_image(url=f"attachment://{rendered.filename}")
    return CharacterMessage(
        embed=embed,
        file=discord.File(BytesIO(rendered.data), filename=rendered.filename),
    )


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
