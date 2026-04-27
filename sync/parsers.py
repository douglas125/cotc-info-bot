"""Parse Sheets API grid data into structured records.

The Index tab is the source of truth for the canonical roster (name, rarity,
role, hyperlink). Each Index hyperlink points at a specific cell in a role tab,
which lets us locate that character's block on the role tab without guessing.
The role-tab parser uses those anchors to extract skills and equipment.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlparse

from config import TABS_BY_GID, color_family, rarity_from_color


# --- color helpers ----------------------------------------------------------

def _color_dict_to_hex(c: dict[str, Any] | None) -> str | None:
    """Convert {red,green,blue} (0..1 floats, defaults 0) to '#RRGGBB'."""
    if not c:
        return None
    r = int(round((c.get("red") or 0.0) * 255))
    g = int(round((c.get("green") or 0.0) * 255))
    b = int(round((c.get("blue") or 0.0) * 255))
    return f"#{r:02X}{g:02X}{b:02X}"


def _cell_color_hex(cell: dict[str, Any]) -> str | None:
    """Best-effort foreground color from a cell."""
    fmt = cell.get("effectiveFormat", {}).get("textFormat", {})
    rgb = fmt.get("foregroundColorStyle", {}).get("rgbColor")
    if rgb:
        hx = _color_dict_to_hex(rgb)
        if hx:
            return hx
    fg = fmt.get("foregroundColor")
    if fg:
        return _color_dict_to_hex(fg)
    # text format runs may carry a color even if cell-level fg is default
    runs = cell.get("textFormatRuns") or []
    for run in runs:
        rfmt = run.get("format", {})
        rrgb = rfmt.get("foregroundColorStyle", {}).get("rgbColor") or rfmt.get("foregroundColor")
        if rrgb:
            return _color_dict_to_hex(rrgb)
    return None


def _cell_text(cell: dict[str, Any]) -> str:
    return (cell.get("formattedValue") or "").strip()


# --- hyperlink parsing ------------------------------------------------------

_RANGE_RE = re.compile(r"^([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?$")


def _col_letters_to_index(letters: str) -> int:
    """A->0, B->1, AA->26, ..."""
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n - 1


@dataclass
class Anchor:
    gid: int
    row: int          # 0-based
    col: int          # 0-based


def parse_anchor(url: str | None) -> Anchor | None:
    """Parse a Google-Sheets in-doc link like '#gid=519845584&range=B5' into an anchor."""
    if not url:
        return None
    try:
        # Anchors can be in the URL fragment OR in query params, depending.
        u = urlparse(url)
        params: dict[str, list[str]] = {}
        for src in (u.fragment, u.query):
            if src:
                params.update(parse_qs(src))
        gid_str = (params.get("gid") or [None])[0]
        rng = (params.get("range") or [None])[0]
        if gid_str is None or not rng:
            return None
        # range may be "Sheet Name!B5" or just "B5"
        if "!" in rng:
            rng = rng.split("!", 1)[1]
        m = _RANGE_RE.match(rng)
        if not m:
            return None
        col = _col_letters_to_index(m.group(1))
        row = int(m.group(2)) - 1
        return Anchor(gid=int(gid_str), row=row, col=col)
    except (ValueError, AttributeError):
        return None


# --- Index parser -----------------------------------------------------------

INDEX_GID = 1917707422
ROLE_HEADER_RE = re.compile(
    r"^(?P<role>Warrior|Merchant|Thief|Apothecary|Hunter|Cleric|Scholar|Dancer)\s*\("
    r"(?P<weapon>Sword|Spear|Dagger|Axe|Bow|Staff|Tome|Fan)\)",
    re.IGNORECASE,
)


@dataclass
class IndexEntry:
    canonical_name: str
    role: str
    weapon: str
    rarity: str | None
    color_hex: str | None
    color_family: str | None
    hyperlink_url: str | None
    anchor: Anchor | None
    sheet_gid: int = INDEX_GID
    source_row: int = 0  # 0-based row in Index sheet


def parse_index(sheet: dict[str, Any]) -> list[IndexEntry]:
    """Walk the Characters Index grid and produce one IndexEntry per character."""
    entries: list[IndexEntry] = []
    rows: list[list[dict[str, Any]]] = []
    for grid in sheet.get("data", []):
        for r in grid.get("rowData", []):
            rows.append(r.get("values", []) or [])
    if not rows:
        return entries

    # 1. find the role-header row: a row that contains "Warrior (Sword)" etc.
    header_row_idx: int | None = None
    role_columns: dict[int, tuple[str, str]] = {}  # col -> (role, weapon)
    for ridx, row in enumerate(rows[:30]):
        roles_in_row: dict[int, tuple[str, str]] = {}
        for cidx, cell in enumerate(row):
            txt = _cell_text(cell)
            m = ROLE_HEADER_RE.match(txt or "")
            if m:
                roles_in_row[cidx] = (m.group("role").lower(), m.group("weapon").lower())
        if len(roles_in_row) >= 4:  # we expect 8, but accept >=4 for resilience
            header_row_idx = ridx
            role_columns = roles_in_row
            break
    if header_row_idx is None:
        return entries

    # 2. for each row below the header, scan each role column for character entries
    for ridx in range(header_row_idx + 1, len(rows)):
        row = rows[ridx]
        for col, (role, weapon) in role_columns.items():
            if col >= len(row):
                continue
            cell = row[col]
            name = _cell_text(cell)
            if not name:
                continue
            # filter junk values: section labels, etc.
            if name.startswith("Color Key") or name.lower().startswith("note"):
                continue
            color_hex = _cell_color_hex(cell)
            fam = color_family(color_hex)
            rarity = rarity_from_color(color_hex)
            # Skip cells that aren't a real character entry (no rarity color and
            # no hyperlink — likely a header continuation or stray text).
            hyperlink = cell.get("hyperlink")
            if rarity is None and not hyperlink:
                continue
            entries.append(IndexEntry(
                canonical_name=name,
                role=role,
                weapon=weapon,
                rarity=rarity,
                color_hex=color_hex,
                color_family=fam,
                hyperlink_url=hyperlink,
                anchor=parse_anchor(hyperlink),
                source_row=ridx,
            ))
    return entries


# --- role-tab parser --------------------------------------------------------

@dataclass
class FormBlock:
    """All structured data for one character form on a role tab."""
    display_name: str
    sheet_gid: int
    source_row: int
    level_cap: int | None = None
    skills: list[dict] = field(default_factory=list)
    equipment: list[dict] = field(default_factory=list)
    splash_art_url: str | None = None
    self_buffs_text: str | None = None
    affinities: list[tuple[str, str | None, str | None]] = field(default_factory=list)


_LV_RE = re.compile(r"Lv\s*(\d+)", re.IGNORECASE)
_POWER_RE = re.compile(r"\((\d+)x\s*(\d+)~?(\d+)?\s*Power", re.IGNORECASE)
_BOOST_RE = re.compile(r"^\s*(\d)\*\s*$")

# Column 5 labels we recognize on role tabs and how they map to skill.kind.
_KIND_LABEL_MAP = {
    "passive": "passive",
    "tp":      "divine",        # divine skill
    "ex":      "ex",            # EX skill
    "special": "special",       # special technique (ultimate)
    "ult":     "ultimate",
}


def _parse_skill_description(desc: str) -> dict:
    """Best-effort numeric extraction from a power-formula skill description."""
    out: dict[str, Any] = {"description": desc}
    if not desc:
        return out
    m = _POWER_RE.search(desc)
    if m:
        out["hits"] = int(m.group(1))
        out["power_min"] = int(m.group(2))
        out["power_max"] = int(m.group(3)) if m.group(3) else int(m.group(2))
    return out


def _classify_skill_kind(col5_label: str | None) -> tuple[str, int | None]:
    """Map a column-5 label to (kind, boost_level)."""
    if not col5_label:
        return "active", None
    s = col5_label.strip().lower()
    bm = _BOOST_RE.match(col5_label.strip())
    if bm:
        return "ultimate", int(bm.group(1))
    if s in _KIND_LABEL_MAP:
        return _KIND_LABEL_MAP[s], None
    if s.startswith("lv"):
        # Special-technique level rows like "Lv1", "Lv10", "Lv20".
        try:
            return "special", int(s[2:])
        except ValueError:
            return "special", None
    return s or "active", None


def parse_role_tab(sheet: dict[str, Any], gid: int,
                   anchors: dict[int, str] | None = None) -> list[FormBlock]:
    """Parse a role tab into per-character FormBlocks.

    Uses an in-tab signal — character-block start rows have a name in col 0
    AND 'SP' in col 6 AND 'Active' in col 7 — rather than relying on Index
    hyperlinks (which use undocumented #rangeid IDs we can't resolve).
    """
    rows: list[list[dict[str, Any]]] = []
    for grid in sheet.get("data", []):
        for r in grid.get("rowData", []):
            rows.append(r.get("values", []) or [])
    if not rows:
        return []

    block_starts: list[tuple[int, str]] = []  # (row_index, display_name)
    for ridx, row in enumerate(rows):
        if len(row) < 8:
            continue
        name = _cell_text(row[0])
        sp = _cell_text(row[6]).upper()
        active = _cell_text(row[7]).lower()
        if name and 1 <= len(name) <= 30 and sp == "SP" and active in ("active", "actives"):
            block_starts.append((ridx, name))

    blocks: list[FormBlock] = []
    for i, (start_row, display_name) in enumerate(block_starts):
        end_row = block_starts[i + 1][0] if i + 1 < len(block_starts) else len(rows)
        block_rows = rows[start_row:end_row]
        if not block_rows:
            continue
        block = _parse_block(block_rows, gid=gid, base_row=start_row,
                             display_name=display_name)
        if block:
            blocks.append(block)
    return blocks


def _parse_block(block_rows: list[list[dict[str, Any]]], *, gid: int,
                 base_row: int, display_name: str) -> FormBlock | None:
    if not block_rows:
        return None
    block = FormBlock(display_name=display_name, sheet_gid=gid, source_row=base_row)

    # The header row often contains the level cap somewhere ("Lv100", "Lv120").
    header_row = block_rows[0]
    for cell in header_row:
        m = _LV_RE.search(_cell_text(cell))
        if m:
            try:
                block.level_cap = max(block.level_cap or 0, int(m.group(1)))
            except ValueError:
                pass

    # The role-tab layout is fixed: SP=col 6, Active/desc=col 7, kind/boost=col 5,
    # equipment=col 21, profile=col 25, profile-value=col 26.
    sp_col = 6
    desc_col = 7
    kind_col = 5
    other_col_start = 21
    profile_col_start = 25

    # Equipment lives in the "Other Info" zone of the header row.
    if other_col_start is not None and other_col_start < len(header_row):
        eq_text = _cell_text(header_row[other_col_start])
        if eq_text:
            block.equipment.append({"slot": None, "name": eq_text, "description": None})
        # additional equipment rows (sub-rows after the header within this block)
        for r in block_rows[1:]:
            if other_col_start < len(r):
                txt = _cell_text(r[other_col_start])
                if txt and txt.lower() != "other info":
                    if not any(e["name"] == txt for e in block.equipment):
                        block.equipment.append({"slot": None, "name": txt, "description": None})

    # Profile column: take the longest non-empty text seen as self_buffs_text;
    # if a cell looks like a URL, treat it as splash art.
    profile_texts: list[str] = []
    if profile_col_start is not None:
        for r in block_rows:
            for c in range(profile_col_start, min(profile_col_start + 3, len(r))):
                txt = _cell_text(r[c])
                if not txt:
                    continue
                if txt.lower().startswith("http"):
                    block.splash_art_url = block.splash_art_url or txt
                else:
                    profile_texts.append(txt)
    if profile_texts:
        # de-dup while preserving order
        seen: set[str] = set()
        kept: list[str] = []
        for t in profile_texts:
            if t not in seen and not t.lower().startswith(("character profile", "splash art")):
                seen.add(t)
                kept.append(t)
        if kept:
            block.self_buffs_text = "\n".join(kept)

    # Skills: rows where sp_col has an integer value, OR rows where col 5 has
    # a recognized non-numeric label (Passive/TP/EX/Special/Lv1/etc.) — those
    # rows describe a skill even if SP is blank.
    slot = 0
    for r in block_rows:
        sp_text = _cell_text(r[sp_col]) if sp_col < len(r) else ""
        kind_label = _cell_text(r[kind_col]) if kind_col < len(r) else ""
        is_numeric_sp = sp_text.isdigit()
        is_known_label = bool(kind_label) and (
            kind_label.lower() in _KIND_LABEL_MAP
            or _BOOST_RE.match(kind_label) is not None
            or kind_label.lower().startswith("lv")
        )
        if not is_numeric_sp and not is_known_label:
            continue
        # Header rows carry "SP"/"Active" — skip them
        if sp_text.upper() == "SP":
            continue
        sp = int(sp_text) if is_numeric_sp else None
        desc = _cell_text(r[desc_col]) if desc_col < len(r) else ""
        kind, boost_level = _classify_skill_kind(kind_label or None)
        slot += 1
        parsed = _parse_skill_description(desc)
        block.skills.append({
            "slot_order": slot,
            "name": None,
            "sp_cost": sp,
            "kind": kind,
            "boost_level": boost_level,
            "description": desc,
            "power_min": parsed.get("power_min"),
            "power_max": parsed.get("power_max"),
            "hits": parsed.get("hits"),
        })

    return block


# --- SEA/GL Unique Kits parser ---------------------------------------------

def parse_sea_unique(sheet: dict[str, Any]) -> list[str]:
    """Return a list of canonical-character names that have SEA-only kit variants.

    Best-effort: scan the first column for character-like names. We don't
    attempt to model the full SEA kit yet — Phase 1 just flags affected forms.
    """
    names: list[str] = []
    seen: set[str] = set()
    for grid in sheet.get("data", []):
        for r in grid.get("rowData", []):
            row = r.get("values", []) or []
            if not row:
                continue
            txt = _cell_text(row[0]) if row else ""
            if 2 <= len(txt) <= 40 and " " not in txt and txt[0].isalpha():
                if txt not in seen:
                    seen.add(txt)
                    names.append(txt)
    return names
