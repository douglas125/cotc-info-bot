"""Parse the Adversary Log CotC enemy spreadsheet.

Two structurally different inputs feed this parser:

1.  **Data tabs** (`Osterra Data`, `Solistia Data`, `120 NPCs Data`) are the
    canonical source of stats. For ranked enemies they hold all 6 ranks
    (Rank 1/2/3, EX1/2/3) × N members per encounter, in vertical 12-col
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
from dataclasses import dataclass, field
from typing import Any

from config import (
    EnemyTabSpec,
    ENEMIES_SPREADSHEET_ID,
    ENEMIES_TABS,
    ENEMY_NAME_ALIASES,
    ENEMY_NPC_TAB_GIDS,
)
from sync.fetch import iter_rows, sheet_by_gid
from sync.parsers import _cell_color_hex, _cell_text


# --- constants discovered by the probe -------------------------------------

# Data-tab block layout (from verify/out/Osterra_Data.txt and Solistia_Data.txt):
# the title cell sits in the same column as the rank labels of the data rows
# below; the member-name column is one to its LEFT; stat headers extend
# rightward from the title cell.
_STAT_HEADER_LABELS = frozenset({
    "Shields", "HP", "P. Atk", "P. Def", "E. Atk", "E. Def",
    "Speed", "Crit", "CritDef", "Equip Atk",
})
_RANKS = ("Rank 1", "Rank 2", "Rank 3", "EX1", "EX2", "EX3")
_RANK_KEYS = ("Rank1", "Rank2", "Rank3", "EX1", "EX2", "EX3")
_RANK_BY_NORMALIZED = {
    re.sub(r"\s+", "", r).lower(): k for r, k in zip(_RANKS, _RANK_KEYS)
}

# Display-tab block layout (from Template tab):
_DISPLAY_NAME_ROW = 3       # 0-based: r3 holds 'Sly Leader Lloris' / 'EX3' badge
_DISPLAY_RANK_COL_OFFSET = 3
_DISPLAY_BLOCK_HEIGHT = 13  # rows 3..15 are the block body

_RANK_BADGE_RE = re.compile(r"^\s*(rank\s*[123]|ex\s*[123])\s*$", re.IGNORECASE)

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


def _canonical_weakness(label: str) -> str:
    return _WEAKNESS_ALIASES.get(label, label)


# --- data classes -----------------------------------------------------------

@dataclass
class MemberRanks:
    """One member of an encounter (e.g. 'Leader Lloris') and its 6-rank stats.

    `rank_stats[rank_key]` is `{stat_name: stat_value, ...}`.
    """
    member_name: str
    rank_stats: dict[str, dict[str, str]] = field(default_factory=dict)


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
            rank_key = _RANK_BY_NORMALIZED.get(re.sub(r"\s+", "", rank_text).lower())
            if rank_key is None:
                break
            name_text = (_cell_text(row[member_name_col])
                         if member_name_col < len(row) else "")
            if name_text:
                if current_member is not None:
                    encounter.members.append(current_member)
                current_member = MemberRanks(member_name=name_text)
            elif current_member is None:
                current_member = MemberRanks(member_name=encounter_name)
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

def _detect_display_blocks(rows: list[list[dict[str, Any]]]) -> list[tuple[int, int]]:
    """Return [(name_col, rank_col), ...] for each block on a display tab.

    A block is a row-3 cell with a non-empty name cell and a sibling rank-badge
    cell some columns later. This works for 1-, 2-, and 3-column block layouts
    documented in the Template tab.
    """
    if len(rows) <= _DISPLAY_NAME_ROW:
        return []
    name_row = rows[_DISPLAY_NAME_ROW]
    # Locate columns that hold a rank badge (text matching Rank1/2/3 or EX1/2/3).
    rank_cols: list[int] = []
    for c_i, cell in enumerate(name_row):
        if _RANK_BADGE_RE.match(_cell_text(cell)):
            rank_cols.append(c_i)
    blocks: list[tuple[int, int]] = []
    for rc in rank_cols:
        # The block's name cell is the nearest non-empty cell to the LEFT of
        # the rank cell, on the same row.
        name_col: int | None = None
        for left in range(rc - 1, -1, -1):
            if left >= len(name_row):
                continue
            t = _cell_text(name_row[left])
            if t and not _RANK_BADGE_RE.match(t):
                name_col = left
                break
        if name_col is None:
            continue
        blocks.append((name_col, rc))
    return blocks


def _formula(cell: dict[str, Any]) -> str:
    return ((cell.get("userEnteredValue") or {}).get("formulaValue") or "")


def _extract_weaknesses_for_block(
    rows: list[list[dict[str, Any]]], name_col: int,
) -> list[list[str]]:
    """Read the per-position weakness lists from a display block.

    Block weakness cells are formula-named-range references like '=Sword'
    living in the rightward part of the block, on rows 6, 7, 8 (one row per
    encounter position). Stat-row labels (=HP, =Atk) and lookups (=B4) live
    in the same column range — we filter them by whitelist.
    """
    out: list[list[str]] = []
    # Block widths vary (10..12 cols depending on layout). 12 is a safe upper
    # bound — extra columns are dark separators with empty formulas.
    col_window = range(name_col, name_col + 12)
    # Scan three rows (rows 6, 7, 8) — that covers every observed layout (1-,
    # 2-, and 3-position encounters). Stop at the first row with no weaknesses.
    for offset in range(3):
        row_idx = _DISPLAY_NAME_ROW + 3 + offset  # rows 6, 7, 8
        if row_idx >= len(rows):
            break
        row = rows[row_idx]
        weaknesses: list[str] = []
        for c_i in col_window:
            if c_i >= len(row):
                break
            f = _formula(row[c_i])
            if not f.startswith("="):
                continue
            label = f[1:].strip()
            if label in _WEAKNESS_NAMES:
                weaknesses.append(_canonical_weakness(label))
        if weaknesses:
            out.append(weaknesses)
        else:
            break
    return out


def parse_display_tab(sheet: dict[str, Any], spec: EnemyTabSpec) -> list[DisplayBlock]:
    """Walk a Lvl-N display tab and emit one DisplayBlock per visible enemy widget."""
    rows = iter_rows(sheet)
    out: list[DisplayBlock] = []
    is_npc = spec.gid in ENEMY_NPC_TAB_GIDS
    for name_col, _rank_col in _detect_display_blocks(rows):
        cell = rows[_DISPLAY_NAME_ROW][name_col]
        name = _cell_text(cell)
        if not name:
            continue
        color = _cell_color_hex(cell)
        col_letter = _index_to_col_letters(name_col)
        anchor = f"#gid={spec.gid}&range={col_letter}{_DISPLAY_NAME_ROW + 1}"
        weaknesses = _extract_weaknesses_for_block(rows, name_col)
        out.append(DisplayBlock(
            display_name=name,
            category=spec.category,
            region=spec.region,
            sheet_gid=spec.gid,
            source_row=_DISPLAY_NAME_ROW,
            name_color_hex=color,
            hyperlink_url=anchor,
            is_npc=is_npc,
            weaknesses_by_position=weaknesses,
        ))
    return out


def parse_npc_display_tab(sheet: dict[str, Any], spec: EnemyTabSpec) -> list[DisplayBlock]:
    """`120 NPCs` doesn't have rank badges — each visible widget is just a name
    at row 3 with a separator pattern. We harvest every non-empty cell at
    `_DISPLAY_NAME_ROW` whose left neighbor is a dark-bg gap."""
    rows = iter_rows(sheet)
    if len(rows) <= _DISPLAY_NAME_ROW:
        return []
    name_row = rows[_DISPLAY_NAME_ROW]
    out: list[DisplayBlock] = []
    for c_i, cell in enumerate(name_row):
        text = _cell_text(cell)
        if not text:
            continue
        # Skip if this looks like a continuation cell (we want only block-start cells).
        # The signal: the cell to the LEFT is empty/dark. We keep this lenient
        # because the probe showed that block widths vary.
        col_letter = _index_to_col_letters(c_i)
        anchor = f"#gid={spec.gid}&range={col_letter}{_DISPLAY_NAME_ROW + 1}"
        out.append(DisplayBlock(
            display_name=text,
            category=spec.category,
            region=spec.region,
            sheet_gid=spec.gid,
            source_row=_DISPLAY_NAME_ROW,
            name_color_hex=_cell_color_hex(cell),
            hyperlink_url=anchor,
            is_npc=True,
        ))
    return out


def _index_to_col_letters(c: int) -> str:
    """0->A, 25->Z, 26->AA, ..."""
    s = ""
    n = c + 1
    while n > 0:
        n, rem = divmod(n - 1, 26)
        s = chr(ord("A") + rem) + s
    return s


# --- name reconciliation ----------------------------------------------------

def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


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
    """
    s = _ARTICLES_RE.sub(" ", s)
    s = _PUNCT_RE.sub("", s)
    return s.lower()


def reconcile_display_to_data(
    display_name: str,
    data_keys: list[str],
) -> str | None:
    """Return the data-tab encounter key matching this display name, or None.

    Strategy:
      1. Exact match on normalized strings.
      2. Explicit alias from config.ENEMY_NAME_ALIASES.
      3. Whitespace-insensitive equality ('NewDelsta' == 'New Delsta').
      4. Substring containment (data key squished is a substring of display).
      5. Reverse substring (display squished is a substring of data key).
    """
    norm_display = _normalize(display_name)
    norm_keys = {_normalize(k): k for k in data_keys}
    if norm_display in norm_keys:
        return norm_keys[norm_display]
    aliased = ENEMY_NAME_ALIASES.get(display_name) or ENEMY_NAME_ALIASES.get(norm_display)
    if aliased and _normalize(aliased) in norm_keys:
        return norm_keys[_normalize(aliased)]
    squish_display = _squish(display_name)
    squish_to_original: dict[str, str] = {_squish(k): k for k in data_keys}
    if squish_display in squish_to_original:
        return squish_to_original[squish_display]
    candidates = [
        (len(sk), original)
        for sk, original in squish_to_original.items()
        if sk and sk in squish_display
    ]
    if candidates:
        candidates.sort(reverse=True)
        return candidates[0][1]
    candidates = [
        (len(squish_display), original)
        for sk, original in squish_to_original.items()
        if squish_display and squish_display in sk
    ]
    if candidates:
        candidates.sort(reverse=True)
        return candidates[0][1]
    return None


# --- top-level orchestrator -------------------------------------------------

# Map data-tab gid → region label (used to scope name reconciliation to the
# correct data tab — Solistia displays look up Solistia data, etc.).
_DATA_TAB_GID_BY_REGION: dict[str, int] = {}


def parse_all(payload: dict[str, Any], data_tab_gids: dict[str, int]) -> list[ParsedEnemy]:
    """Top-level: produce a list of fully-merged ParsedEnemy records.

    `data_tab_gids` maps region label ('Osterra' | 'Solistia' | 'NPCs') to the
    data-tab gid in the payload. The parser uses these to look up stats.
    """
    # 1. Parse data tabs by region.
    encounters_by_region: dict[str, dict[str, EncounterData]] = {}
    for region, gid in data_tab_gids.items():
        if region == "NPCs":
            continue  # NPCs handled separately
        sheet = sheet_by_gid(payload, gid)
        if sheet is None:
            continue
        encounters_by_region[region] = parse_data_tab(sheet, region)
    npc_data: dict[str, NpcStat] = {}
    npc_gid = data_tab_gids.get("NPCs")
    if npc_gid is not None:
        sheet = sheet_by_gid(payload, npc_gid)
        if sheet is not None:
            npc_data = parse_npc_data_tab(sheet)

    # 2. Parse display tabs.
    parsed: list[ParsedEnemy] = []
    unmatched_display: list[tuple[str, str]] = []
    for spec in ENEMIES_TABS:
        sheet = sheet_by_gid(payload, spec.gid)
        if sheet is None:
            continue
        if spec.gid in ENEMY_NPC_TAB_GIDS:
            blocks = parse_npc_display_tab(sheet, spec)
        else:
            blocks = parse_display_tab(sheet, spec)
        for block in blocks:
            if block.is_npc:
                # Look up the NPC by name in the flat NPC data.
                key = reconcile_display_to_data(block.display_name, list(npc_data.keys()))
                if key is None:
                    unmatched_display.append((block.display_name, spec.name))
                    continue
                npc = npc_data[key]
                rank_stats = {
                    "Default": [
                        {"position": 0, "member_name": npc.npc_name,
                         "stat_name": stat, "stat_value": val}
                        for stat, val in npc.stats.items()
                    ]
                }
                parsed.append(ParsedEnemy(
                    canonical_name=block.display_name,
                    category=block.category,
                    region=block.region,
                    sheet_gid=block.sheet_gid,
                    source_row=block.source_row,
                    name_color_hex=block.name_color_hex,
                    hyperlink_url=block.hyperlink_url,
                    is_npc=True,
                    rank_stats=rank_stats,
                    weaknesses_by_position=block.weaknesses_by_position,
                ))
                continue
            # Ranked enemy: look up in the corresponding region's data tab.
            region_encounters = encounters_by_region.get(block.region or "")
            if region_encounters is None:
                unmatched_display.append((block.display_name, spec.name))
                continue
            key = reconcile_display_to_data(block.display_name, list(region_encounters.keys()))
            if key is None:
                unmatched_display.append((block.display_name, spec.name))
                continue
            encounter = region_encounters[key]
            rank_stats: dict[str, list[dict[str, Any]]] = {rk: [] for rk in _RANK_KEYS}
            for pos, member in enumerate(encounter.members):
                for rank_key in _RANK_KEYS:
                    stats = member.rank_stats.get(rank_key, {})
                    for stat_name, stat_value in stats.items():
                        rank_stats[rank_key].append({
                            "position": pos,
                            "member_name": member.member_name,
                            "stat_name": stat_name,
                            "stat_value": stat_value,
                        })
            # Drop ranks with no data (some encounters may be incomplete).
            rank_stats = {k: v for k, v in rank_stats.items() if v}
            if not rank_stats:
                unmatched_display.append((block.display_name, spec.name))
                continue
            parsed.append(ParsedEnemy(
                canonical_name=block.display_name,
                category=block.category,
                region=block.region,
                sheet_gid=block.sheet_gid,
                source_row=block.source_row,
                name_color_hex=block.name_color_hex,
                hyperlink_url=block.hyperlink_url,
                is_npc=False,
                rank_stats=rank_stats,
                weaknesses_by_position=block.weaknesses_by_position,
            ))
    if unmatched_display:
        # Surface as an attribute on the result for the runner/verifier to log.
        parse_all.unmatched = unmatched_display  # type: ignore[attr-defined]
    else:
        parse_all.unmatched = []  # type: ignore[attr-defined]
    return parsed


def rank_order(rank_key: str) -> int:
    if rank_key == "Default":
        return 0
    if rank_key in _RANK_KEYS:
        return _RANK_KEYS.index(rank_key) + 1
    return 99
