"""Parse the Adversary Log CotC enemy spreadsheet.

Two structurally different inputs feed this parser:

1.  **Data tabs** (`Osterra Data`, `Solistia Data`, `120 NPCs Data`) are the
    canonical source of stats. Ranked enemies have Rank 1/2/3 and populated
    numbered EX ranks × N members per encounter, in vertical 12-col
    chunks separated by white spacer columns. NPCs are flat — one row per
    NPC, no rank dimension.

2.  **Display tabs** (`Lvl 1/25/50/75`, `Solistia Lvl ...`, `120 NPCs`) are
    the user-facing surface. Each shows a single rank's stats per encounter
    (the maintainer's most recently selected dropdown value), but they're
    where the canonical, full-lore enemy name lives — and they hyperlink
    to a specific cell that anchors the block.

The pipeline:
    parse_data_tabs(payload) -> dict[encounter_name -> EncounterData]
    parse_display_tabs(payload) -> list[DisplayBlock]
    match() merges the two, producing fully-populated ParsedEnemy records.

The probe at `verify/probe_enemies.py` validated the column offsets used
here against the live sheet — see verify/out/*.txt for the raw grids.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from config import (
    EnemyTabSpec,
    ENEMIES_SPREADSHEET_ID,
    ENEMIES_TABS,
    ENEMY_NAME_ALIASES,
    ENEMY_NPC_TAB_GIDS,
    ENEMY_NPC_DATA_KEYS,
)
from enemy_ranks import RANK_PATTERN, normalize_rank, rank_order
from sync.fetch import iter_rows, sheet_by_gid
from sync.parsers import (
    _cell_bg_hex, _cell_color_hex, _cell_text, _formula, _index_to_col_letters,
)


# --- constants discovered by the probe -------------------------------------

# Data-tab block layout (from verify/out/Osterra_Data.txt and Solistia_Data.txt):
# the title cell sits in the same column as the rank labels of the data rows
# below; the member-name column is one to its LEFT; stat headers extend
# rightward from the title cell.
_STAT_HEADER_LABELS = frozenset({
    "Shields", "HP", "P. Atk", "P. Def", "E. Atk", "E. Def",
    "Speed", "Crit", "CritDef", "Equip Atk",
})

# Display-tab block layout (from Template tab):
_DISPLAY_NAME_ROW = 3       # 0-based: r3 holds 'Sly Leader Lloris' / 'EX3' badge
_DISPLAY_RANK_COL_OFFSET = 3
_DISPLAY_BLOCK_HEIGHT = 13  # rows 3..15 are the block body

_RANK_BADGE_RE = RANK_PATTERN
_DISPLAY_AUX_LABEL_RE = re.compile(r"^\s*wave\s+\d+\s*$", re.IGNORECASE)

# Display-tab weakness icons are formula-named-range references like '=Sword'.
# We whitelist what counts as a weakness so we don't accidentally pick up
# stat-row labels (=HP, =Atk) or member-name lookups (=B4, =M4) sitting in
# the same column range.
_WEAKNESS_NAMES: frozenset[str] = frozenset({
    # Weapons
    "Sword", "Spear", "Polearm", "Dagger", "Axe", "Bow", "Staff", "Tome", "Fan",
    # Elements (no Earth element in CotC — Lightning fills that slot)
    "Fire", "Ice", "Wind", "Lightning", "Light", "Dark",
})

# Spreadsheet uses 'Polearm' interchangeably with 'Spear' (same weapon icon).
# Normalize to a single canonical name so the embed and any /search filter
# don't have two entries for the same weakness.
_WEAKNESS_ALIASES: dict[str, str] = {
    "Polearm": "Spear",
}

# `=VLOOKUP("MemberName", '120 NPCs Data'!...)` — captures the lookup-key
# string. NPC display widgets stitch each position together with a VLOOKUP
# against the flat per-creature catalog, so the first quoted arg is the
# member's data-tab name (e.g. 'NewDelsta', 'Canalbrine 1').
_VLOOKUP_NAME_RE = re.compile(r'^=VLOOKUP\("([^"]*)"')


def _canonical_weakness(label: str) -> str:
    return _WEAKNESS_ALIASES.get(label, label)


# --- data classes -----------------------------------------------------------

@dataclass
class MemberRanks:
    """One member of an encounter (e.g. 'Leader Lloris') and its available rank stats.

    `rank_stats[rank_key]` is `{stat_name: stat_value, ...}`.
    """
    member_name: str
    rank_stats: dict[str, dict[str, str]] = field(default_factory=dict)
    source_start_row: int = -1
    source_end_row: int = -1
    rank_col: int = -1


@dataclass
class EncounterData:
    encounter_name: str
    region: str
    members: list[MemberRanks] = field(default_factory=list)


@dataclass
class NpcStat:
    """Single-row NPC stats from `120 NPCs Data`."""
    npc_name: str
    stats: dict[str, str] = field(default_factory=dict)


@dataclass
class DisplayBlock:
    """A visible block on a Lvl-N display tab, before stats are merged in."""
    display_name: str
    category: str
    region: str | None
    sheet_gid: int
    source_row: int          # 0-based row of the name cell
    name_color_hex: str | None
    hyperlink_url: str | None
    is_npc: bool
    # weaknesses_by_position[i] = ['Sword', 'Axe', ...] in slot order.
    # Empty list = no weaknesses found for that position.
    weaknesses_by_position: list[list[str]] = field(default_factory=list)
    # NPC-only: per-position member-name references read off the display-tab
    # block. For ranked encounters the member names come from the data tab,
    # so this stays empty. Indexed parallel to `weaknesses_by_position`.
    member_names_by_position: list[str] = field(default_factory=list)
    # Single-rank stats lifted directly from the display block's grid (the
    # 9 numbers visible to the user). Populated only for ranked blocks; used
    # by `parse_all` as a fallback when the encounter has no Data-tab match,
    # so enemies whose stats live on the display tab itself still surface in
    # /enemy. List of stat-row dicts ({"position", "member_name",
    # "stat_name", "stat_value"}) and the rank label to bucket them under.
    inline_rank: str | None = None
    inline_stats: list[dict[str, str]] = field(default_factory=list)
    # HP formulas identify the underlying member rows, even after renames.
    member_formulas: list[str] = field(default_factory=list)


@dataclass
class ParsedEnemy:
    """A merged record ready for repo persistence."""
    canonical_name: str
    category: str
    region: str | None
    sheet_gid: int
    source_row: int
    name_color_hex: str | None
    hyperlink_url: str | None
    is_npc: bool
    # rank_key -> list of {position, member_name, stat_name, stat_value}
    rank_stats: dict[str, list[dict[str, Any]]]
    # weaknesses_by_position[i] = ['Sword', ...] — same for every rank since
    # the display tab encodes one weakness set per encounter, not per rank.
    weaknesses_by_position: list[list[str]] = field(default_factory=list)


# --- data tab parsing -------------------------------------------------------

def _find_block_anchors(rows: list[list[dict[str, Any]]]) -> list[tuple[int, int, str, list[str]]]:
    """Find every encounter block on a data tab.

    A block's header row has the encounter name in some column `title_col`
    and 'Shields' in `title_col + 1`. The 10 stat headers (Shields, HP,
    P. Atk, ...) span `title_col + 1 .. title_col + 10`.

    Returns: [(header_row, title_col, encounter_name, stat_labels), ...].
    """
    out: list[tuple[int, int, str, list[str]]] = []
    for r_i, row in enumerate(rows):
        for c_i, cell in enumerate(row):
            text = _cell_text(cell)
            if not text or text in _STAT_HEADER_LABELS:
                continue
            # Right neighbor must be "Shields" — that's the block-header signal.
            if c_i + 1 >= len(row):
                continue
            if _cell_text(row[c_i + 1]) != "Shields":
                continue
            stat_labels: list[str] = []
            for sc in range(c_i + 1, len(row)):
                lbl = _cell_text(row[sc])
                if lbl in _STAT_HEADER_LABELS:
                    stat_labels.append(lbl)
                else:
                    break
            if stat_labels:
                out.append((r_i, c_i, text, stat_labels))
    return out


def parse_data_tab(sheet: dict[str, Any], region: str) -> dict[str, EncounterData]:
    """Parse one *Data tab into `{encounter_name: EncounterData}`."""
    rows = iter_rows(sheet)
    out: dict[str, EncounterData] = {}
    for header_row, title_col, encounter_name, stat_labels in _find_block_anchors(rows):
        member_name_col = max(0, title_col - 1)
        rank_col = title_col
        first_stat_col = title_col + 1
        encounter = out.setdefault(
            encounter_name, EncounterData(encounter_name=encounter_name, region=region)
        )
        current_member: MemberRanks | None = None
        for r_i in range(header_row + 1, len(rows)):
            row = rows[r_i]
            if rank_col >= len(row):
                break
            rank_text = _cell_text(row[rank_col])
            if not rank_text:
                break
            rank_key = normalize_rank(rank_text)
            if rank_key is None or rank_key == "Default":
                break
            name_text = (_cell_text(row[member_name_col])
                         if member_name_col < len(row) else "")
            if name_text:
                if current_member is not None:
                    encounter.members.append(current_member)
                current_member = MemberRanks(member_name=name_text, source_start_row=r_i, rank_col=rank_col)
            elif current_member is None:
                current_member = MemberRanks(member_name=encounter_name, source_start_row=r_i, rank_col=rank_col)
            current_member.source_end_row = r_i
            stats: dict[str, str] = {}
            for s_i, label in enumerate(stat_labels):
                c = first_stat_col + s_i
                if c < len(row):
                    val = _cell_text(row[c])
                    if val:
                        stats[label] = val
            current_member.rank_stats[rank_key] = stats
        if current_member is not None:
            encounter.members.append(current_member)
        if not encounter.members:
            del out[encounter_name]
    return out


def parse_npc_data_tab(sheet: dict[str, Any]) -> dict[str, NpcStat]:
    """Parse `120 NPCs Data` — flat layout, one row per NPC.

    Header row holds 'NPC Name' in col 1 and stat headers in cols 2..11.
    Each subsequent row is one NPC.
    """
    rows = iter_rows(sheet)
    if len(rows) < 2:
        return {}
    # Locate the header row by finding 'NPC Name' anywhere on the sheet.
    header_row_idx = None
    for r_i, row in enumerate(rows):
        for c_i, cell in enumerate(row):
            if _cell_text(cell) == "NPC Name":
                header_row_idx = r_i
                break
        if header_row_idx is not None:
            break
    if header_row_idx is None:
        return {}
    header = rows[header_row_idx]
    stat_labels: list[str] = []
    for c in range(2, len(header)):
        t = _cell_text(header[c])
        if t:
            stat_labels.append(t)
        else:
            break
    if not stat_labels:
        return {}
    out: dict[str, NpcStat] = {}
    for r_i in range(header_row_idx + 1, len(rows)):
        row = rows[r_i]
        if len(row) < 2:
            continue
        name = _cell_text(row[1])
        if not name:
            continue
        stats: dict[str, str] = {}
        for s_i, label in enumerate(stat_labels):
            c = 2 + s_i
            if c < len(row):
                val = _cell_text(row[c])
                if val:
                    stats[label] = val
        if stats:
            out[name] = NpcStat(npc_name=name, stats=stats)
    return out


# --- display tab parsing ----------------------------------------------------

# Dark-grey separator color used by every display tab to gap adjacent block
# widgets. The cell immediately LEFT of a block-start name is on this color
# (unless the name is at column 0 / on the very edge of the sheet).
_BLOCK_SEPARATOR_BG = "#222222"


def _detect_display_blocks(
    rows: list[list[dict[str, Any]]],
    use_separator_fallback: bool = False,
) -> list[tuple[int, int, int | None]]:
    """Return `(name_row, name_col, rank_col)` for every block widget.

    Block-rows repeat every `_DISPLAY_BLOCK_HEIGHT` rows from
    `_DISPLAY_NAME_ROW`; iterating the whole stride is required because
    busier tabs (notably 120 NPCs and Lvl 50/75) carry more encounters
    than fit in one row of widgets. The separator-color fallback exists
    only for the NPC tab, which is the one display tab with no rank badges.
    """
    blocks: list[tuple[int, int, int | None]] = []
    for name_row_idx in range(_DISPLAY_NAME_ROW, len(rows), _DISPLAY_BLOCK_HEIGHT):
        name_row = rows[name_row_idx]
        # Skip rows that have no text at all — cheap optimization for tabs
        # whose data ends well before the nominal grid does.
        if not any(_cell_text(c) for c in name_row):
            continue

        # Pass 1: rank-badge detection (ranked tabs).
        rank_cols: list[int] = [
            c_i for c_i, cell in enumerate(name_row)
            if _RANK_BADGE_RE.match(_cell_text(cell))
        ]
        ranked_name_cols: set[int] = set()
        for rc in rank_cols:
            name_col: int | None = None
            for left in range(rc - 1, -1, -1):
                if left >= len(name_row):
                    continue
                t = _cell_text(name_row[left])
                if t and not _RANK_BADGE_RE.match(t) and not _is_display_aux_label(t):
                    name_col = left
                    break
            if name_col is None:
                continue
            blocks.append((name_row_idx, name_col, rc))
            ranked_name_cols.add(name_col)

        # Pass 2: separator-color fallback (NPC tabs).
        if not use_separator_fallback:
            continue
        for c_i, cell in enumerate(name_row):
            if c_i in ranked_name_cols:
                continue
            text = _cell_text(cell)
            if not text or _RANK_BADGE_RE.match(text) or _is_display_aux_label(text):
                continue
            # Block-start signal: cell to the LEFT is on the dark separator
            # background. Cells at column 0 are accepted unconditionally
            # (no left neighbor to check).
            if c_i > 0:
                left_bg = _cell_bg_hex(name_row[c_i - 1])
                if left_bg != _BLOCK_SEPARATOR_BG:
                    continue
            blocks.append((name_row_idx, c_i, None))
    return blocks


def _is_display_aux_label(text: str) -> bool:
    """Return True for display metadata cells that are not enemy names."""
    return bool(_DISPLAY_AUX_LABEL_RE.match(text))


def _formula_args(expression: str) -> list[str]:
    """Split IF/IFS arguments without evaluating spreadsheet expressions."""
    args, start, depth, quote = [], 0, 0, None
    for index, char in enumerate(expression):
        if quote:
            if char == quote:
                quote = None
        elif char in ('"', "'"):
            quote = char
        elif char == '(':
            depth += 1
        elif char == ')':
            depth -= 1
        elif char == ',' and depth == 0:
            args.append(expression[start:index].strip())
            start = index + 1
    args.append(expression[start:].strip())
    return args


def _selected_formula(formula: str, rows: list[list[dict[str, Any]]]) -> str:
    """Select the source's current wave in simple IF/IFS formulas.

    Only equality against a local text cell and literal TRUE are understood.
    Unknown expressions are left unresolved, never executed or guessed.
    """
    match = re.fullmatch(r'=\s*(IF|IFS)\((.*)\)', formula.strip(), re.I | re.S)
    if match is None:
        return formula
    args = _formula_args(match[2])
    if match[1].upper() == 'IFS':
        pairs = list(zip(args[::2], args[1::2]))
    elif len(args) >= 2:
        pairs = [(args[0], args[1])]
    else:
        return formula
    for condition, value in pairs:
        test = re.fullmatch(r'\$?([A-Z]+)\$?(\d+)\s*=\s*"([^"]*)"', condition, re.I)
        if condition.lower() == 'true':
            selected = True
        elif test:
            col = 0
            for char in test[1].upper():
                col = col * 26 + ord(char) - ord('A') + 1
            row = int(test[2]) - 1
            selected = (0 <= row < len(rows) and col <= len(rows[row])
                        and _cell_text(rows[row][col - 1]) == test[3])
        else:
            return formula
        if selected:
            return _selected_formula('=' + value, rows)
    if match[1].upper() == 'IF' and len(args) == 3:
        return _selected_formula('=' + args[2], rows)
    return formula


def _block_end_col(rows: list[list[dict[str, Any]]], name_row: int, name_col: int) -> int:
    row = rows[name_row]
    return next((c for c in range(name_col + 1, len(row))
                 if _cell_bg_hex(row[c]) == _BLOCK_SEPARATOR_BG), min(name_col + 12, len(row)))


def _extract_weaknesses_for_block(
    rows: list[list[dict[str, Any]]], name_row: int, name_col: int,
) -> list[list[str]]:
    """Read the per-position weakness lists from a display block.

    Block weakness cells are formula-named-range references like '=Sword'
    living in the rightward part of the block. They sit on the rows
    starting at `name_row + 3` (one row per encounter position). Stat-row
    labels (=HP, =Atk) and lookups (=B4) live in the same column range — we
    filter them by whitelist.
    """
    out: list[list[str]] = []
    # Wider multi-member panels end at the next dark block separator.
    col_window = range(name_col, _block_end_col(rows, name_row, name_col))
    # Keep empty positions aligned; NPC groups may have more than three members.
    for offset in range(max(3, len(_member_columns(rows, name_row, name_col)))):
        row_idx = name_row + 3 + offset
        if row_idx >= len(rows):
            break
        row = rows[row_idx]
        weaknesses: list[str] = []
        for c_i in col_window:
            if c_i >= len(row):
                break
            f = _selected_formula(_formula(row[c_i]), rows)
            if not f.startswith("="):
                continue
            label = f[1:].strip()
            if label in _WEAKNESS_NAMES:
                weaknesses.append(_canonical_weakness(label))
        out.append(weaknesses)
    while out and not out[-1]:
        out.pop()
    return out


# Stat order on every Lvl-N display block, top-down from the row right under
# the shield-count strip. The visible grid is exactly these 9 stats; Shields
# itself sits on the row above (alongside the weakness panel).
_DISPLAY_STAT_NAMES: tuple[str, ...] = (
    "HP", "P. Atk", "P. Def", "E. Atk", "E. Def",
    "Speed", "Crit", "CritDef", "Equip Atk",
)
# A "stat-shaped" cell is a number wide enough to be HP (>= 4 digits) or has
# at least one thousands separator. The narrower form intentionally rejects
# 1-3 digit values that appear in display blocks as weakness slot indicators
# ('1', '2'), shield counts in the wrong column ('34'), or stub values that
# would otherwise inflate the position count.
_HP_INT_RE = re.compile(r"^-?\d{1,3}(?:,\d{3})+$|^-?\d{4,}$")
_STAT_INT_RE = re.compile(r"^-?\d{1,3}(?:,\d{3})+$|^-?\d+$")


def _is_hp_value(s: str) -> bool:
    return bool(_HP_INT_RE.match(_integer_text(s)))


def _is_stat_value(s: str) -> bool:
    return bool(_STAT_INT_RE.match(_integer_text(s)))


def _integer_text(value: str) -> str:
    # Some data cells format integer HP with a .00 suffix.
    return re.sub(r'\.0+$', '', value.strip())


_DATA_REFERENCE_RE = re.compile(
    r"(?:'(?P<quoted>(?:[^']|'')+)'|(?P<plain>[A-Za-z_][A-Za-z_0-9 ]*))!"
    r"\$?(?P<col>[A-Z]+)\$?(?P<row>[1-9]\d*)", re.IGNORECASE,
)


def _data_reference(formula: str) -> tuple[str, int, int] | None:
    """Read a local A1 reference, never evaluate arbitrary formula text."""
    expression = formula.strip().removeprefix('=')
    lookup = re.fullmatch(r'VLOOKUP\((.*)\)', expression, re.I | re.S)
    if lookup:
        args = _formula_args(lookup[1])
        if len(args) < 2:
            return None
        expression = args[1]
    match = _DATA_REFERENCE_RE.match(expression)
    if match is None:
        return None
    suffix = expression[match.end():]
    if suffix and not re.fullmatch(r':\$?[A-Z]+\$?[1-9]\d*', suffix, re.I):
        return None
    col = 0
    for char in match['col'].upper():
        col = col * 26 + ord(char) - ord('A') + 1
    return ((match['quoted'] or match['plain']).replace("''", "'"),
            int(match['row']) - 1, col - 1)


def _member_columns(rows: list[list[dict[str, Any]]], name_row: int, name_col: int) -> list[int]:
    """Find consecutive HP columns, including formulas whose values are not ready."""
    if name_row + 4 >= len(rows):
        return []
    row = rows[name_row + 4]

    def is_member(col: int) -> bool:
        cell = row[col]
        formula = _selected_formula(_formula(cell), rows)
        return _is_hp_value(_cell_text(cell)) or _data_reference(formula) is not None

    first = next((c for c in range(name_col, min(name_col + 4, len(row))) if is_member(c)), None)
    if first is None:
        return []
    columns = []
    for col in range(first, min(_block_end_col(rows, name_row, name_col), len(row))):
        if not is_member(col):
            break
        columns.append(col)
    return columns


def _inline_shields(rows: list[list[dict[str, Any]]], name_row: int, name_col: int, count: int) -> dict[int, str]:
    """The SH icon labels the position/shield strip beside the stats grid."""
    if name_row + 2 >= len(rows):
        return {}
    header = rows[name_row + 2]
    col = next((c for c in range(name_col, min(_block_end_col(rows, name_row, name_col), len(header)))
                if _formula(header[c]).strip().lower() == '=sh'), None)
    if col is None:
        return {}
    shields = {}
    for position in range(count):
        ri = name_row + 3 + position
        if ri < len(rows) and col + 1 < len(rows[ri]):
            value = _integer_text(_cell_text(rows[ri][col + 1]))
            if _is_stat_value(value):
                shields[position] = value
    return shields


def _extract_inline_block_stats(
    rows: list[list[dict[str, Any]]],
    name_row: int,
    name_col: int,
    rank_col: int | None,
) -> tuple[str | None, list[dict[str, str]]]:
    """Extract single-rank stats from a display block's visible grid.

    Used when a block has no matching data entry or mixes data formulas with
    manually entered members. Values apply only to the selected rank.

    Returns (rank_key, [stat_rows]) or (None, []) if the block has no
    parseable inline stats (e.g. the formulas resolve to '#REF!').

    Layout (1-indexed cell coords, with the title at row 4 col N):
      row 4  col rank_col      → rank label, e.g. 'EX3' or 'Rank 1'
      row 8  col first_stat..  → HP for each member position (comma-int)
      row 9  ...               → P. Atk
      ...                      → through row 16 col first_stat = Equip Atk

    The first stat column slides one to the right of `name_col` for
    single-position blocks (where the title cell visually merges into
    cell N) and starts at `name_col` for multi-position ones; we detect
    it by scanning row name_row+4 (HP row) for the leftmost comma-int.
    Member position count is the number of consecutive comma-int cells.
    """
    if rank_col is None or rank_col >= len(rows[name_row]):
        return None, []
    rank_label_raw = _cell_text(rows[name_row][rank_col])
    if not rank_label_raw:
        return None, []
    rank_key = normalize_rank(rank_label_raw)
    if rank_key is None:
        return None, []

    columns = _member_columns(rows, name_row, name_col)
    if not columns:
        return None, []

    stat_rows: list[dict[str, str]] = []
    shields = _inline_shields(rows, name_row, name_col, len(columns))
    for pos, col in enumerate(columns):
        if not _positive_hp(_cell_text(rows[name_row + 4][col])):
            return None, []
        if pos in shields:
            stat_rows.append({"position": pos, "member_name": None,
                              "stat_name": "Shields", "stat_value": shields[pos]})
        for s_offset, stat_name in enumerate(_DISPLAY_STAT_NAMES):
            r = name_row + 4 + s_offset
            if r >= len(rows):
                break
            row = rows[r]
            val = _integer_text(_cell_text(row[col])) if col < len(row) else ""
            # Only keep cells that look like stat values; reject leaked
            # weakness/shields panel content like 'Notes' or single-digit
            # slot indicators that aren't real stats for this position.
            if val and _is_stat_value(val):
                stat_rows.append({
                    "position": pos,
                    "member_name": None,
                    "stat_name": stat_name,
                    "stat_value": val,
                })
    return rank_key, stat_rows


def _extract_npc_member_names(
    rows: list[list[dict[str, Any]]],
    name_row: int,
    name_col: int,
    encounter_name: str,
) -> list[str]:
    """Resolve each NPC widget position to a `120 NPCs Data` catalog key.

    Three sources, in priority: the HP-row VLOOKUP arg (works for nearly
    every multi-position widget), the member-name-row formattedValue
    (works for single-position widgets where the cell holds the literal
    name rather than a numeric index label), and finally the encounter
    name itself — the last covers `Cropdale`-style widgets whose stats
    come from a direct-cell ref the formula text doesn't expose.
    """
    name_strip_row = name_row + 3
    hp_row = name_row + 4
    name_strip = rows[name_strip_row] if name_strip_row < len(rows) else []
    hp_strip = rows[hp_row] if hp_row < len(rows) else []
    names: list[str] = []
    for c_i in range(name_col + 1, name_col + 11):
        member: str | None = None
        if c_i < len(hp_strip):
            m = _VLOOKUP_NAME_RE.match(_formula(hp_strip[c_i]))
            if m:
                member = m.group(1)
        if member is None and c_i < len(name_strip):
            text = _cell_text(name_strip[c_i])
            if text and not text.replace(",", "").isdigit():
                member = text
        if member is None:
            break
        names.append(member)
    if not names:
        names = [encounter_name]
    return names


def parse_display_tab(sheet: dict[str, Any], spec: EnemyTabSpec) -> list[DisplayBlock]:
    """Walk a display tab and emit one DisplayBlock per visible enemy widget.

    Handles both ranked tabs (rank-badge block detection) and the 120 NPCs
    tab (separator-color fallback). For NPC blocks the per-position member
    names are also harvested from row +3 — the merge step in `parse_all`
    needs them to look up each member in `120 NPCs Data` independently.
    """
    rows = iter_rows(sheet)
    out: list[DisplayBlock] = []
    is_npc = spec.gid in ENEMY_NPC_TAB_GIDS
    detected = _detect_display_blocks(rows, use_separator_fallback=is_npc)
    for name_row, name_col, rank_col in detected:
        cell = rows[name_row][name_col]
        name = _cell_text(cell)
        if not name:
            continue
        color = _cell_color_hex(cell)
        col_letter = _index_to_col_letters(name_col)
        anchor = f"#gid={spec.gid}&range={col_letter}{name_row + 1}"
        weaknesses = _extract_weaknesses_for_block(rows, name_row, name_col)
        member_names = (
            _extract_npc_member_names(rows, name_row, name_col, name)
            if is_npc else []
        )
        inline_rank, inline_stats = (
            (None, [])
            if is_npc
            else _extract_inline_block_stats(rows, name_row, name_col, rank_col)
        )
        out.append(DisplayBlock(
            display_name=name,
            category=spec.category,
            region=spec.region,
            sheet_gid=spec.gid,
            source_row=name_row,
            name_color_hex=color,
            hyperlink_url=anchor,
            is_npc=is_npc,
            inline_rank=inline_rank,
            inline_stats=inline_stats,
            weaknesses_by_position=weaknesses,
            member_names_by_position=member_names,
            member_formulas=[_selected_formula(_formula(rows[name_row + 4][c]), rows)
                             for c in _member_columns(rows, name_row, name_col)],
        ))
    return out


# --- name reconciliation ----------------------------------------------------

def _normalize(s: str) -> str:
    # NFKC collapses fullwidth Japanese letters/digits/punctuation to their
    # halfwidth equivalents (e.g. '９Ｓ？' → '9S?'), so a display tab using
    # fullwidth characters lines up with a data tab that uses ASCII.
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", s)).strip().lower()


_ARTICLES_RE = re.compile(r"\b(the|of|a|an)\b", re.IGNORECASE)
_PUNCT_RE = re.compile(r"[\s\-'`’\.]+")


def _squish(s: str) -> str:
    """Lowercase + strip articles + remove whitespace, dashes, and apostrophes.

    Catches drift like:
      'NewDelsta'              vs 'New Delsta'           → 'newdelsta'
      'Oskha the Have-not'     vs 'Oskha Have-Not'       → 'oskhahavenot'
      'Ignazio of Greed'       vs 'Ignazio Greed'        → 'ignaziogreed'
      'Ring-Sealed Beast'      vs 'RingBeast'            → drops to substring fallback
      "M'suhi the Viper"       vs 'Msushi'               → 'msuhiviper' (still no match — alias needed)
      '９Ｓ？' (fullwidth)      vs '9 S ?'                → '9s?' (NFKC collapses fullwidth)
    """
    s = unicodedata.normalize("NFKC", s)
    s = _ARTICLES_RE.sub(" ", s)
    s = _PUNCT_RE.sub("", s)
    return s.lower()


@dataclass(frozen=True)
class _NameIndex:
    """Pre-computed lookups over a region's data-tab encounter names.

    Build once per region in `parse_all` so that reconciling N display blocks
    against M data keys stays O(N + M) rather than O(N * M) re-normalizations.
    """
    by_normalized: dict[str, str]
    by_squished:   dict[str, str]


def _build_name_index(data_keys: list[str]) -> _NameIndex:
    return _NameIndex(
        by_normalized={_normalize(k): k for k in data_keys},
        by_squished=  {_squish(k):    k for k in data_keys},
    )


def reconcile_display_to_data(
    display_name: str,
    data_keys: list[str] | _NameIndex,
) -> str | None:
    """Return the data-tab encounter key matching this display name, or None.

    Strategy (in priority order):
      1. Exact normalized match.
      2. Explicit alias from `config.ENEMY_NAME_ALIASES`.
      3. Whitespace-insensitive equality ('NewDelsta' == 'New Delsta').
      4. Longest data key that's a substring of the display name.
      5. Display name is a substring of some data key — pick the shortest
         such key (closest to the display name's length).

    `data_keys` accepts either a raw list (built into a _NameIndex on the fly)
    or a pre-computed `_NameIndex` for hot-path callers.
    """
    idx = data_keys if isinstance(data_keys, _NameIndex) else _build_name_index(data_keys)
    norm_display = _normalize(display_name)
    if norm_display in idx.by_normalized:
        return idx.by_normalized[norm_display]
    aliased = ENEMY_NAME_ALIASES.get(display_name)
    if aliased and _normalize(aliased) in idx.by_normalized:
        return idx.by_normalized[_normalize(aliased)]
    squish_display = _squish(display_name)
    if squish_display in idx.by_squished:
        return idx.by_squished[squish_display]
    # Substring fallback: prefer the longest data key contained in the display.
    contained = [
        (len(sk), original)
        for sk, original in idx.by_squished.items()
        if sk and sk in squish_display
    ]
    if contained:
        return max(contained)[1]
    # Reverse substring: display contained inside a data key — prefer the
    # shortest such key (closest match to the display name's length).
    containing = [
        (len(sk), original)
        for sk, original in idx.by_squished.items()
        if squish_display and squish_display in sk
    ]
    if containing:
        return min(containing)[1]
    return None


# --- top-level orchestrator -------------------------------------------------

@dataclass
class ParseResult:
    """Output of `parse_all`: the merged enemies plus any unmatched display blocks."""
    enemies: list[ParsedEnemy] = field(default_factory=list)
    # (display_name, source_tab_name) for each block we couldn't bind to data.
    unmatched: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _try_inline_fallback(block: DisplayBlock, result: ParseResult) -> bool:
    """Append a single-rank ParsedEnemy from the block's display-grid stats.

    Returns True if a fallback enemy was added (caller should `continue`),
    False if no inline stats were available (caller falls through to the
    normal unmatched path).
    """
    if not block.inline_stats or not block.inline_rank:
        return False
    result.enemies.append(ParsedEnemy(
        canonical_name=block.display_name,
        category=block.category,
        region=block.region,
        sheet_gid=block.sheet_gid,
        source_row=block.source_row,
        name_color_hex=block.name_color_hex,
        hyperlink_url=block.hyperlink_url,
        is_npc=False,
        rank_stats={block.inline_rank: list(block.inline_stats)},
        weaknesses_by_position=block.weaknesses_by_position,
    ))
    return True


def _positive_hp(value: str) -> bool:
    return _is_stat_value(value) and int(_integer_text(value).replace(",", "")) > 0


def _member_stats(members: list[MemberRanks], name: str, result: ParseResult) -> dict[str, list[dict[str, Any]]]:
    """Only publish ranks with usable HP for every encounter member."""
    ranks = sorted({rank for member in members for rank in member.rank_stats}, key=rank_order)
    out = {}
    for rank in ranks:
        values = [member.rank_stats.get(rank, {}) for member in members]
        if not any(any(stats.values()) for stats in values):
            continue  # An entirely blank future rank is a normal placeholder.
        if not all(_positive_hp(stats.get("HP", "")) for stats in values):
            result.warnings.append(f"{name} {rank}: incomplete member HP; rank omitted")
            continue
        stat_rows = []
        for position, (member, stats) in enumerate(zip(members, values)):
            for stat, value in stats.items():
                if not value:
                    continue
                if not _is_stat_value(value):
                    result.warnings.append(f"{name} {rank} member {position + 1}: invalid {stat} {value!r}; omitted")
                    continue
                stat_rows.append(dict(position=position, member_name=member.member_name,
                                      stat_name=stat, stat_value=_integer_text(value)))
        out[rank] = stat_rows
    return out


def validate_source(payload: dict[str, Any], data_tab_gids: dict[str, int]) -> None:
    """Fail before replacing a mirror when a required source tab disappears."""
    if payload.get('spreadsheetId') != ENEMIES_SPREADSHEET_ID:
        raise ValueError('Enemy payload is not from the configured replacement source')
    required = {spec.gid for spec in ENEMIES_TABS} | set(data_tab_gids.values())
    present = {sheet['properties']['sheetId'] for sheet in payload.get('sheets', [])}
    missing = sorted(required - present)
    if missing:
        raise ValueError(f"Enemy source missing required tab gids: {missing}")


def parse_all(payload: dict[str, Any], data_tab_gids: dict[str, int]) -> ParseResult:
    """Merge display encounters with all available ranks from their data members.

    Formula references identify members independently of display names. Named
    reconciliation remains a fallback for blocks without those references.
    """
    sheets_by_title = {s['properties']['title']: s for s in payload.get('sheets', [])}
    encounters_by_region = {}
    npc_catalogs = {}
    members_by_source = {}
    npc_data_keys = set(ENEMY_NPC_DATA_KEYS.values())
    for region, gid in data_tab_gids.items():
        sheet = sheet_by_gid(payload, gid)
        if sheet is None:
            continue
        if region in npc_data_keys:
            npc_catalogs[region] = parse_npc_data_tab(sheet)
            continue
        encounters = parse_data_tab(sheet, region)
        encounters_by_region[region] = encounters
        for encounter in encounters.values():
            for member in encounter.members:
                for row in range(member.source_start_row, member.source_end_row + 1):
                    members_by_source[(gid, row, member.rank_col)] = member
    region_indexes = {region: _build_name_index(list(encounters))
                      for region, encounters in encounters_by_region.items()}
    result = ParseResult()
    for spec in ENEMIES_TABS:
        sheet = sheet_by_gid(payload, spec.gid)
        if sheet is None:
            continue
        for block in parse_display_tab(sheet, spec):
            members = []
            if block.is_npc:
                data_key = ENEMY_NPC_DATA_KEYS[spec.gid]
                catalog = npc_catalogs.get(data_key, {})
                index = _build_name_index(list(catalog))
                names = block.member_names_by_position or [block.display_name]
                # Direct cell formulas on 140 NPCs point at individual catalog rows.
                direct = [_data_reference(f) if not f.upper().startswith('=VLOOKUP(') else None
                          for f in block.member_formulas]
                if any(direct):
                    names = []
                    for pos, ref in enumerate(direct):
                        if ref is None:
                            names.append(block.member_names_by_position[pos]
                                         if pos < len(block.member_names_by_position) else "")
                            continue
                        target = sheets_by_title.get(ref[0]) if ref else None
                        if target is None or target['properties']['sheetId'] != data_tab_gids.get(data_key):
                            names.append("")
                            continue
                        rows = iter_rows(target)
                        names.append(_cell_text(rows[ref[1]][1])
                                     if ref[1] < len(rows) and len(rows[ref[1]]) > 1 else "")
                for name in names:
                    key = reconcile_display_to_data(name, index) if name else None
                    if key is None:
                        result.unmatched.append((name or block.display_name, spec.name))
                        break
                    npc = catalog[key]
                    members.append(MemberRanks(npc.npc_name, {"Default": npc.stats}))
                if len(members) != len(names):
                    continue
            else:
                refs = [_data_reference(f) for f in block.member_formulas]
                if any(refs) and not all(refs) and _try_inline_fallback(block, result):
                    result.warnings.append(f"{block.display_name}: mixed inline/data members; only displayed rank imported")
                    continue
                if any(refs):
                    # Resolve every displayed position; never silently drop a member.
                    for ref in refs:
                        target = sheets_by_title.get(ref[0]) if ref else None
                        gid = target['properties']['sheetId'] if target else None
                        member = members_by_source.get((gid, ref[1], ref[2])) if ref else None
                        if gid != data_tab_gids.get(block.region) or member is None:
                            result.unmatched.append((block.display_name, spec.name))
                            result.warnings.append(f"{block.display_name}: unresolved member data reference")
                            break
                        members.append(member)
                    if len(members) != len(refs):
                        continue
                else:
                    index = region_indexes.get(block.region)
                    key = reconcile_display_to_data(block.display_name, index) if index else None
                    if key is not None:
                        members = encounters_by_region[block.region][key].members
                    elif _try_inline_fallback(block, result):
                        continue
                    else:
                        result.unmatched.append((block.display_name, spec.name))
                        continue
            rank_stats = _member_stats(members, block.display_name, result)
            if not rank_stats:
                result.warnings.append(f"{block.display_name}: no usable ranks yet; encounter omitted")
                continue
            result.enemies.append(ParsedEnemy(
                canonical_name=block.display_name, category=block.category,
                region=block.region, sheet_gid=block.sheet_gid,
                source_row=block.source_row, name_color_hex=block.name_color_hex,
                hyperlink_url=block.hyperlink_url, is_npc=block.is_npc,
                rank_stats=rank_stats, weaknesses_by_position=block.weaknesses_by_position,
            ))
    return result
