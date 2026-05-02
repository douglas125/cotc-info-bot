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
import unicodedata
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
    # NPC-only: per-position member-name references read off the display-tab
    # block. For ranked encounters the member names come from the data tab,
    # so this stays empty. Indexed parallel to `weaknesses_by_position`.
    member_names_by_position: list[str] = field(default_factory=list)


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


def _extract_weaknesses_for_block(
    rows: list[list[dict[str, Any]]], name_row: int, name_col: int,
) -> list[list[str]]:
    """Read the per-position weakness lists from a display block.

    Block weakness cells are formula-named-range references like '=Sword'
    living in the rightward part of the block. They sit on the three rows
    starting at `name_row + 3` (one row per encounter position). Stat-row
    labels (=HP, =Atk) and lookups (=B4) live in the same column range — we
    filter them by whitelist.
    """
    out: list[list[str]] = []
    # Block widths vary (10..12 cols depending on layout). 12 is a safe upper
    # bound — extra columns are dark separators with empty formulas.
    col_window = range(name_col, name_col + 12)
    # Scan three rows — that covers every observed layout (1-, 2-, and
    # 3-position encounters; the 5-position 120 NPCs widgets only ever fill
    # three rows on the display surface). Stop at the first row with no
    # weaknesses.
    for offset in range(3):
        row_idx = name_row + 3 + offset
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
    for name_row, name_col, _rank_col in detected:
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
        out.append(DisplayBlock(
            display_name=name,
            category=spec.category,
            region=spec.region,
            sheet_gid=spec.gid,
            source_row=name_row,
            name_color_hex=color,
            hyperlink_url=anchor,
            is_npc=is_npc,
            weaknesses_by_position=weaknesses,
            member_names_by_position=member_names,
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


def parse_all(payload: dict[str, Any], data_tab_gids: dict[str, int]) -> ParseResult:
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

    # 2. Pre-compute name lookups once per region so reconcile is O(1) per
    #    display block instead of O(N) per call.
    region_indexes = {
        region: _build_name_index(list(encounters.keys()))
        for region, encounters in encounters_by_region.items()
    }
    npc_index = _build_name_index(list(npc_data.keys()))

    # 3. Walk display tabs and merge each block with its data-tab match.
    result = ParseResult()
    for spec in ENEMIES_TABS:
        sheet = sheet_by_gid(payload, spec.gid)
        if sheet is None:
            continue
        blocks = parse_display_tab(sheet, spec)
        for block in blocks:
            if block.is_npc:
                # NPC encounters are multi-position: the display block lists
                # each member by name, and `120 NPCs Data` is a flat catalog
                # of individual creatures keyed by name. Look up each member
                # independently and stitch the result into one ParsedEnemy.
                member_names = block.member_names_by_position or [block.display_name]
                stats_rows: list[dict[str, Any]] = []
                for pos, member_display in enumerate(member_names):
                    key = reconcile_display_to_data(member_display, npc_index)
                    if key is None:
                        result.unmatched.append((member_display, spec.name))
                        continue
                    npc = npc_data[key]
                    for stat, val in npc.stats.items():
                        stats_rows.append({
                            "position": pos,
                            "member_name": npc.npc_name,
                            "stat_name": stat,
                            "stat_value": val,
                        })
                if not stats_rows:
                    # No member resolved — drop the encounter entirely so the
                    # bot doesn't surface a stats-less /enemy entry.
                    continue
                result.enemies.append(ParsedEnemy(
                    canonical_name=block.display_name,
                    category=block.category,
                    region=block.region,
                    sheet_gid=block.sheet_gid,
                    source_row=block.source_row,
                    name_color_hex=block.name_color_hex,
                    hyperlink_url=block.hyperlink_url,
                    is_npc=True,
                    rank_stats={"Default": stats_rows},
                    weaknesses_by_position=block.weaknesses_by_position,
                ))
                continue
            # Ranked enemy: look up in the corresponding region's data tab.
            region_index = region_indexes.get(block.region or "")
            if region_index is None:
                result.unmatched.append((block.display_name, spec.name))
                continue
            key = reconcile_display_to_data(block.display_name, region_index)
            if key is None:
                result.unmatched.append((block.display_name, spec.name))
                continue
            encounter = encounters_by_region[block.region][key]
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
            rank_stats = {k: v for k, v in rank_stats.items() if v}
            if not rank_stats:
                result.unmatched.append((block.display_name, spec.name))
                continue
            result.enemies.append(ParsedEnemy(
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
    return result


def rank_order(rank_key: str) -> int:
    if rank_key == "Default":
        return 0
    if rank_key in _RANK_KEYS:
        return _RANK_KEYS.index(rank_key) + 1
    return 99
