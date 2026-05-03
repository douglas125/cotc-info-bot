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

from config import TABS_BY_GID, WEAPON_TO_ROLE, color_family, rarity_from_color


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


def _cell_bg_hex(cell: dict[str, Any]) -> str | None:
    """Best-effort cell background color as '#RRGGBB'."""
    fmt = cell.get("effectiveFormat", {})
    rgb = fmt.get("backgroundColorStyle", {}).get("rgbColor")
    if rgb:
        hx = _color_dict_to_hex(rgb)
        if hx:
            return hx
    bg = fmt.get("backgroundColor")
    if bg:
        return _color_dict_to_hex(bg)
    return None


def _cell_text(cell: dict[str, Any]) -> str:
    return (cell.get("formattedValue") or "").strip()


def _formula(cell: dict[str, Any]) -> str:
    """Raw `userEnteredValue.formulaValue` of a cell, or '' if absent."""
    return ((cell.get("userEnteredValue") or {}).get("formulaValue") or "")


_IMAGE_FORMULA_RE = re.compile(
    r"""=IMAGE\s*\(\s*["']([^"']+)["']""", re.IGNORECASE,
)


def _image_url_from_cell(cell: dict[str, Any]) -> str | None:
    """Pull an image URL out of a cell's `=IMAGE("url")` formula, if any.

    The Sheets v4 API exposes inline-image artwork only via this formula
    path; floating drawings and Insert>Image>In-cell artwork are not
    accessible through `spreadsheets.get`.
    """
    formula = _formula(cell)
    if formula:
        m = _IMAGE_FORMULA_RE.search(formula)
        if m:
            return m.group(1)
    return None


# --- hyperlink parsing ------------------------------------------------------

_RANGE_RE = re.compile(r"^([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?$")


def _col_letters_to_index(letters: str) -> int:
    """A->0, B->1, AA->26, ..."""
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n - 1


def _index_to_col_letters(c: int) -> str:
    """Inverse of `_col_letters_to_index`. 0->'A', 25->'Z', 26->'AA', ..."""
    s = ""
    n = c + 1
    while n > 0:
        n, rem = divmod(n - 1, 26)
        s = chr(ord("A") + rem) + s
    return s


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


_WEAPON_PATTERNS = {
    weapon: re.compile(rf"\b{re.escape(weapon)}\b", re.IGNORECASE)
    for weapon in WEAPON_TO_ROLE
}
_PRIMARY_WEAPON_SKILL_KINDS = frozenset({"active", "divine", "ex", "ultimate"})


def infer_weapon_from_block(block: FormBlock) -> str | None:
    """Infer the character weapon from parsed SEA skill descriptions.

    SEA-only blocks do not carry Index role headers. Skill text is the source
    of truth; active/divine/EX/ultimate rows are preferred, with passive/latent
    text used only when the primary rows do not yield a clear answer.
    """
    primary_descriptions = [
        skill.get("description") or ""
        for skill in block.skills
        if skill.get("kind") in _PRIMARY_WEAPON_SKILL_KINDS
    ]
    weapon = _infer_weapon_from_descriptions(primary_descriptions)
    if weapon is not None:
        return weapon
    return _infer_weapon_from_descriptions(
        [skill.get("description") or "" for skill in block.skills]
    )


def _infer_weapon_from_descriptions(descriptions: list[str]) -> str | None:
    counts: dict[str, int] = {}
    haystack = "\n".join(d for d in descriptions if d)
    if not haystack:
        return None
    for weapon, pattern in _WEAPON_PATTERNS.items():
        n = len(pattern.findall(haystack))
        if n:
            counts[weapon] = n
    if not counts:
        return None
    ranked = sorted(counts.items(), key=lambda item: item[1], reverse=True)
    if len(ranked) == 1:
        return ranked[0][0]
    (top_weapon, top_count), (_, runner_up_count) = ranked[:2]
    if top_count >= runner_up_count * 2:
        return top_weapon
    return None


_LV_RE = re.compile(r"Lv\s*(\d+)", re.IGNORECASE)
_POWER_RE = re.compile(r"\((\d+)x\s*(\d+)(?:\s*[~\-]\s*(\d+))?\s*Power", re.IGNORECASE)
_BOARD_RE = re.compile(r"^\s*(\d)\*\s*$")  # prestige-board indicator (1*..6*)

# Role-tab column indices. The layout is fixed across all role tabs.
_COL_KIND      = 5   # board indicator (N*) / section divider / kind label
_COL_SP        = 6   # SP cost (active rows) / desc fallback (passive rows)
_COL_DESC      = 7   # active/special/EX/TP description
_COL_LATENT_S  = 14  # latent-icon scan start (inclusive)
_COL_LATENT_E  = 22  # latent-icon scan end   (inclusive)
_COL_STAT_ICON = 20
_COL_EXCL_TAG  = 20  # "Exclusive Accessory N" / "Unique Effects"
_COL_EQUIP     = 21  # primary A4 accessory name (block header row)
_COL_STAT_VAL  = 21  # numeric stat value on stat rows (same column as the name)
_COL_EQUIP_EFF = 23  # accessory effect description (row below header)
_COL_PROFILE   = 25  # profile section header / values

_TAG_EXCLUSIVE_PREFIX = "exclusive accessory"
_TAG_UNIQUE_EFFECTS   = "unique effects"

# Restricted to stat names that the live snapshot pairs with a numeric col-21
# value. Without an allowlist, status-effect icons sharing col 20 on
# exclusive-accessory rows (`=Burning`, `=Lion`, ...) would slip through.
_KNOWN_STAT_NAMES = frozenset({
    "HP", "SP", "ATK", "MAG", "DEF", "MDEF", "SPD", "CRIT",
})


def _formula_stat_name(cell: dict[str, Any]) -> str | None:
    """Return e.g. 'ATK' for a cell whose formulaValue is '=ATK', else None.

    Restricted to ``_KNOWN_STAT_NAMES`` so non-stat formulas don't match.
    """
    formula = _formula(cell).strip()
    if len(formula) < 2 or not formula.startswith("="):
        return None
    name = formula[1:].strip().upper()
    return name if name in _KNOWN_STAT_NAMES else None


def _cell_int(cell: dict[str, Any]) -> int | None:
    """Return the integer value of a cell, or None if not a clean integer."""
    ev = cell.get("effectiveValue") or {}
    if "numberValue" in ev:
        try:
            return int(ev["numberValue"])
        except (TypeError, ValueError):
            return None
    txt = (cell.get("formattedValue") or "").strip()
    if not txt:
        return None
    try:
        return int(txt)
    except ValueError:
        return None


def _scan_accessory_stats(
    block_rows: list[list[dict[str, Any]]], start_idx: int,
) -> list[tuple[str, int]]:
    """Walk consecutive rows from ``start_idx`` collecting (stat_name, value)
    pairs. Stops at the first row where col 20 isn't a known stat formula or
    col 21 isn't a clean integer. Returns at most ~4 pairs in practice."""
    out: list[tuple[str, int]] = []
    for r in block_rows[start_idx:]:
        if _COL_STAT_VAL >= len(r):
            break
        name = _formula_stat_name(r[_COL_STAT_ICON])
        if not name:
            break
        val = _cell_int(r[_COL_STAT_VAL])
        if val is None:
            break
        out.append((name, val))
    return out


# Some accessory blocks (Rique, Cygna, Pia) interpose a blank row between the
# header row and the first stat row; tolerate up to this many blanks before
# giving up.
_ACC_MAX_BLANK_SKIP = 3

# Sub-buff overflow layouts (EX Partitio's "Unique Effects") have larger
# gaps between sub-buffs than between header and stats — verified up to
# 4 blank rows between consecutive sub-buffs in the live snapshot. Raised
# to give a margin without consuming whole sections (the scanner still
# stops on stat formulas, next-section markers, or any other non-pattern
# text in c20).
_SUBBUFF_MAX_BLANK_SKIP = 6


def _find_first_stat_row(
    block_rows: list[list[dict[str, Any]]], after_idx: int,
) -> int | None:
    """Return the index of the first row at or after ``after_idx`` whose
    col 20 is a known ``=STAT`` formula. Skips up to ``_ACC_MAX_BLANK_SKIP``
    fully-blank-c20 rows; bails on any non-blank, non-stat c20 (e.g. an
    'Exclusive Accessory N' or 'Notes' marker for the next section)."""
    skipped = 0
    for ridx in range(after_idx, len(block_rows)):
        r = block_rows[ridx]
        if _COL_STAT_VAL >= len(r):
            return None
        if _formula_stat_name(r[_COL_STAT_ICON]):
            return ridx
        if _cell_text(r[_COL_STAT_ICON]).strip():
            return None
        skipped += 1
        if skipped > _ACC_MAX_BLANK_SKIP:
            return None
    return None


def _scan_subbuff_overflow(
    block_rows: list[list[dict[str, Any]]], after_idx: int,
) -> list[tuple[str, str]]:
    """Walk forward from ``after_idx`` collecting (name, description) sub-buff
    pairs from a non-stat overflow layout (EX Partitio's "Unique Effects").

    Each sub-buff is a *name row* whose c20 holds an icon formula (e.g.
    ``=Legendary``, ``=Offensive``, ``=Preparation`` — anything that
    isn't a known stat) and whose c21 holds the buff's name as
    non-numeric text. The description sits on a *separate* overflow row,
    typically 1-2 rows below: c20 carries plain text (no formula), c21
    is empty. Tolerates up to ``_SUBBUFF_MAX_BLANK_SKIP`` blank rows
    between name and description, and between sub-buffs.

    Stops at the first known stat formula (so it cannot consume normal
    stat-bearing accessories), at the next 'Exclusive Accessory N' /
    'Unique Effects' marker, or after a long blank run.
    """
    out: list[tuple[str, str]] = []
    pending_name: str | None = None
    blank_run = 0
    for ridx in range(after_idx, len(block_rows)):
        r = block_rows[ridx]
        if _COL_EQUIP >= len(r):
            break
        if _formula_stat_name(r[_COL_STAT_ICON]):
            break
        c20_formula = _formula(r[_COL_STAT_ICON]).strip()
        c20_text    = _cell_text(r[_COL_STAT_ICON]).strip()
        c21_text    = _cell_text(r[_COL_EQUIP]).strip()
        section_label = c20_text.lower()
        if (section_label.startswith(_TAG_EXCLUSIVE_PREFIX)
                or section_label == _TAG_UNIQUE_EFFECTS):
            break
        # Name row: icon formula in c20 + non-numeric label in c21.
        if c20_formula and c21_text and not c21_text.lstrip("-").isdigit():
            if pending_name is not None:
                out.append((pending_name, ""))
            pending_name = c21_text
            blank_run = 0
            continue
        # Overflow description row: plain text in c20 (no formula),
        # c21 empty, and we have a pending sub-buff name.
        if pending_name and c20_text and not c20_formula and not c21_text:
            out.append((pending_name, c20_text))
            pending_name = None
            blank_run = 0
            continue
        # Blank row: tolerate within limit.
        if not c20_text and not c21_text and not c20_formula:
            blank_run += 1
            if blank_run > _SUBBUFF_MAX_BLANK_SKIP:
                break
            continue
        break
    if pending_name is not None:
        out.append((pending_name, ""))
    return out


def _format_subbuffs(subbuffs: list[tuple[str, str]]) -> str:
    """Render sub-buffs as multi-line markdown for the description column."""
    return "\n".join(
        f"**{name}**: {desc}" if desc else f"**{name}**"
        for name, desc in subbuffs
    )

# The sheet calls the unit's ultimate "Special"; both labels collapse to
# kind="ultimate". TP rows are the unit's divine skill (still consumes SP).
_KIND_LABEL_MAP = {
    "passive": "passive",
    "tp":      "divine",
    "ex":      "ex",
    "special": "ultimate",
    "ult":     "ultimate",
}

# Section dividers appear in col 5 with cols 6/7 empty. Tracked so we can
# disambiguate bare "N*" rows (active vs passive depending on section).
_SECTION_MARKERS = {
    "active":       "active",
    "actives":      "active",
    "special":      "special",
    "latent power": "latent",
    "passive":      "passive",
    "passives":     "passive",
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


def _classify_skill_kind(
    col5_label: str | None,
) -> tuple[str | None, int | None, int | None]:
    """Map a column-5 label to (kind, learn_board, tier_level).

    Returns a kind of None when the label alone is insufficient — currently
    only the bare board indicator "N*". The caller resolves it from row
    context (an active-section row with numeric SP becomes 'active',
    a passive-section row becomes 'passive').

    The board indicator is the prestige-board number where the unit learns
    that skill (1..6); it is NOT a skill kind. The tier level is the upgrade
    tier of the unit's single Special/Ultimate skill (Lv1/Lv10/Lv20 rows).
    """
    if not col5_label:
        return "active", None, None
    raw = col5_label.strip()
    s = raw.lower()
    bm = _BOARD_RE.match(raw)
    if bm:
        return None, int(bm.group(1)), None
    if s in _KIND_LABEL_MAP:
        return _KIND_LABEL_MAP[s], None, None
    if s.startswith("lv"):
        try:
            return "ultimate", None, int(s[2:])
        except ValueError:
            return "ultimate", None, None
    return s or "active", None, None


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

    # A4 accessories: header c21 is the primary accessory; "Exclusive
    # Accessory N" / "Unique Effects" markers in col 20 demarcate extra
    # accessory entries. Stat boosts live in the rows immediately below
    # each accessory header as `=STAT` formula icons (col 20) paired with
    # numeric values (col 21). The first stat row also carries the
    # accessory's effect description in c23. Some blocks (Rique, Cygna,
    # Pia) interpose a blank spacer between header and stats — locate the
    # first stat row dynamically rather than hardcoding offset 1.
    if _COL_EQUIP < len(header_row):
        primary_name = _cell_text(header_row[_COL_EQUIP])
        if primary_name and primary_name.lower() != "other info":
            stat_start = _find_first_stat_row(block_rows, 1)
            desc_idx = stat_start if stat_start is not None else 1
            primary_effect: str | None = None
            if desc_idx < len(block_rows) and _COL_EQUIP_EFF < len(block_rows[desc_idx]):
                primary_effect = _cell_text(block_rows[desc_idx][_COL_EQUIP_EFF]) or None
            block.equipment.append({
                "slot": None,
                "name": primary_name,
                "description": primary_effect,
                "is_exclusive": False,
                "stats": _scan_accessory_stats(block_rows, stat_start) if stat_start is not None else [],
            })
        for ridx, r in enumerate(block_rows):
            if _COL_EXCL_TAG >= len(r):
                continue
            label = _cell_text(r[_COL_EXCL_TAG]).strip()
            if not label:
                continue
            label_lower = label.lower()
            is_excl = label_lower.startswith(_TAG_EXCLUSIVE_PREFIX)
            is_unique = label_lower == _TAG_UNIQUE_EFFECTS
            if not (is_excl or is_unique):
                continue
            # Effect text: prefer c23 of the first stat row, fall back to
            # c21 of the row immediately after the marker if c21 is
            # non-numeric (Cardona's "Unique Effects" layout).
            stat_start = _find_first_stat_row(block_rows, ridx + 1)
            desc_idx = stat_start if stat_start is not None else (ridx + 1)
            eff = ""
            if desc_idx < len(block_rows):
                nr = block_rows[desc_idx]
                if _COL_EQUIP_EFF < len(nr):
                    eff = _cell_text(nr[_COL_EQUIP_EFF])
                if not eff and _COL_EQUIP < len(nr):
                    v = _cell_text(nr[_COL_EQUIP])
                    if v and not v.lstrip("-").isdigit():
                        eff = v
            # Sub-buff overflow fallback (EX Partitio's "Unique Effects").
            # Gated on `not eff and stat_start is None` so it cannot
            # regress any accessory that already parsed via the c23/c21
            # path or that has stats.
            if not eff and stat_start is None:
                subbuffs = _scan_subbuff_overflow(block_rows, ridx + 1)
                if subbuffs:
                    eff = _format_subbuffs(subbuffs)
            block.equipment.append({
                "slot": None,
                "name": label,
                "description": eff or None,
                "is_exclusive": is_excl,
                "stats": _scan_accessory_stats(block_rows, stat_start) if stat_start is not None else [],
            })

    # Profile column: take the longest non-empty text seen as self_buffs_text;
    # if a cell looks like a URL or `=IMAGE("url")` formula, treat it as
    # splash art. Scoped to the profile column range so per-sync overhead
    # stays linear in block count, not block area.
    profile_texts: list[str] = []
    for r in block_rows:
        for c in range(_COL_PROFILE, min(_COL_PROFILE + 3, len(r))):
            cell = r[c]
            txt = _cell_text(cell)
            if txt.lower().startswith("http"):
                block.splash_art_url = block.splash_art_url or txt
            elif txt:
                profile_texts.append(txt)
            if not block.splash_art_url:
                url = _image_url_from_cell(cell)
                if url:
                    block.splash_art_url = url
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

    # The role-tab layout is irregular per-section:
    #   - Active / EX / TP-as-divine:    SP in col 6 (numeric), desc in col 7
    #   - Special tiers (Lv1/Lv10/Lv20): desc in col 7, BUT some older /
    #     SEA-only entries put the desc in col 6 instead — fall back.
    #   - Passive:                       desc in col 6, col 7 empty
    #   - Latent Power:                  multi-line text in col 5,
    #                                    [init]/[cooldown] in cols 17/19
    # Some role-tab blocks omit the "Passive" divider (e.g. role-tab Yugo);
    # in that case a passive-shaped row (label in col 5, non-numeric text
    # in col 6, col 7 empty) implicitly switches sections.
    current_section = "active"
    latent_lines: list[str] = []
    latent_initial_use: int | None = None
    latent_cooldown: int | None = None
    skills: list[dict] = []
    slot = 0

    for r in block_rows:
        sp_text = _cell_text(r[_COL_SP]) if _COL_SP < len(r) else ""
        kind_label = _cell_text(r[_COL_KIND]) if _COL_KIND < len(r) else ""
        c7_text = _cell_text(r[_COL_DESC]) if _COL_DESC < len(r) else ""

        marker = _SECTION_MARKERS.get(kind_label.strip().lower())
        if marker and not sp_text and not c7_text:
            current_section = marker
            continue

        if current_section == "latent":
            latent_text = kind_label.strip()
            if latent_text:
                latent_lines.append(latent_text)
                init_use, cooldown = _scan_latent_counters(r)
                if latent_initial_use is None and init_use is not None:
                    latent_initial_use = init_use
                if latent_cooldown is None and cooldown is not None:
                    latent_cooldown = cooldown
            continue

        is_numeric_sp = sp_text.isdigit()
        is_known_label = bool(kind_label) and (
            kind_label.lower() in _KIND_LABEL_MAP
            or _BOARD_RE.match(kind_label) is not None
            or kind_label.lower().startswith("lv")
        )
        if not is_numeric_sp and not is_known_label:
            continue
        if sp_text.upper() == "SP":  # block-header marker
            continue

        # Implicit passive-section detection for blocks missing the divider.
        looks_passive = (
            is_known_label and not is_numeric_sp
            and bool(sp_text) and not c7_text
        )
        if looks_passive and current_section != "passive":
            current_section = "passive"

        if current_section == "passive":
            desc = sp_text
        elif c7_text:
            desc = c7_text
        elif sp_text and not is_numeric_sp:
            desc = sp_text
        else:
            desc = ""

        kind, learn_board, tier_level = _classify_skill_kind(kind_label or None)

        # In the passive section, force kind="passive" for the bare board
        # indicator and unknown labels. Lv\d+ rows are the unit's ultimate
        # tiers and keep kind="ultimate" even if they appear after the
        # Passive divider — historically the Special divider was sometimes
        # missing and the tier rows ended up in the passive section. A
        # passive-section row whose col-5 label is literally "TP" (e.g.
        # Esmeralda, Cyrus, Yugo) is the conditional "TP passive" — give
        # it kind="tp_passive" so the renderer can mark it with a `TP`
        # badge in the Passives field while keeping the active-section TP
        # (kind="divine") in the Actives field.
        if current_section == "passive" and tier_level is None:
            kind = "tp_passive" if kind == "divine" else "passive"
        elif kind is None:
            if is_numeric_sp:
                kind = "active"
            else:
                continue

        # Per the user: only TP/divine and active skills consume SP.
        sp: int | None
        if current_section == "passive" or kind in ("ex", "ultimate"):
            sp = None
        else:
            sp = int(sp_text) if is_numeric_sp else None

        slot += 1
        parsed = _parse_skill_description(desc)
        max_uses, unlock_condition = (None, None)
        if kind == "ex":
            max_uses, unlock_condition = _extract_ex_meta(r)
        skills.append({
            "slot_order": slot,
            "name": None,
            "sp_cost": sp,
            "kind": kind,
            "learn_board": learn_board,
            "tier_level": tier_level,
            "initial_use": None,
            "cooldown": None,
            "description": desc,
            "power_min": parsed.get("power_min"),
            "power_max": parsed.get("power_max"),
            "hits": parsed.get("hits"),
            "max_uses": max_uses,
            "unlock_condition": unlock_condition,
        })

    if latent_lines:
        slot += 1
        skills.append({
            "slot_order": slot,
            "name": None,
            "sp_cost": None,
            "kind": "latent",
            "learn_board": None,
            "tier_level": None,
            "initial_use": latent_initial_use,
            "cooldown": latent_cooldown,
            "description": "\n".join(latent_lines),
            "power_min": None,
            "power_max": None,
            "hits": None,
            "max_uses": None,
            "unlock_condition": None,
        })

    block.skills = skills
    return block


def _extract_ex_meta(
    row: list[dict[str, Any]],
) -> tuple[int | None, str | None]:
    """Return (max_uses, unlock_condition) from an EX skill row.

    The cluster is anchored by a literal '#' cell. In the canonical layout
    it sits at col 8 with the integer at col 9 and the condition at col 11
    (col 10 holds the lock icon, no text). One SEA/GL Unique Kits row
    (EX Ophilia) shifts the cluster five columns right, so we scan for the
    '#' instead of hardcoding the offset.
    """
    for c in range(8, min(16, len(row))):
        if _cell_text(row[c]) != "#":
            continue
        max_uses: int | None = None
        if c + 1 < len(row):
            uses_txt = _cell_text(row[c + 1])
            if uses_txt.isdigit():
                max_uses = int(uses_txt)
        unlock_condition: str | None = None
        if c + 3 < len(row):
            txt = _cell_text(row[c + 3])
            if txt:
                unlock_condition = " ".join(txt.split())
        return max_uses, unlock_condition
    return None, None


def _scan_latent_counters(
    row: list[dict[str, Any]],
) -> tuple[int | None, int | None]:
    """Return (initial_use, cooldown) from a latent-section row's icon strip.
    Two integer cells in cols 14..22 (between desc_col and the equipment
    column) hold those counters when present. Either may be None."""
    found: list[int] = []
    end = min(_COL_LATENT_E + 1, len(row))
    for c in range(_COL_LATENT_S, end):
        txt = _cell_text(row[c])
        if txt.isdigit():
            found.append(int(txt))
            if len(found) >= 2:
                break
    initial_use = found[0] if len(found) >= 1 else None
    cooldown = found[1] if len(found) >= 2 else None
    return initial_use, cooldown


# --- SEA/GL Unique Kits parser ---------------------------------------------

SEA_GID = 291065169


def parse_sea_kits(sheet: dict[str, Any]) -> list[FormBlock]:
    """Parse the SEA/GL Unique Kits tab into per-character FormBlocks.

    The tab uses the same layout as a role tab (name in col 0, "SP" in col 6,
    "Active" in col 7 mark a block start), so we delegate to parse_role_tab.
    Characters listed here have a kit that supersedes the role-tab kit; the
    runner uses these blocks in preference to the role-tab block.
    """
    return parse_role_tab(sheet, gid=SEA_GID)
