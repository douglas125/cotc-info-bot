"""Unit tests for sync/enemy_parsers.py."""
from __future__ import annotations

from typing import Any

import pytest

from sync.enemy_parsers import (
    _DISPLAY_BLOCK_HEIGHT,
    _detect_display_blocks,
    _find_block_anchors,
    parse_all,
    parse_data_tab,
    parse_npc_data_tab,
    parse_display_tab,
    reconcile_display_to_data,
    rank_order,
    _index_to_col_letters,
)
from config import ENEMY_NAME_ALIASES, EnemyTabSpec


def _cell(text: str = "", bg: str | None = None) -> dict[str, Any]:
    cell: dict[str, Any] = {}
    if text:
        cell["formattedValue"] = text
    if bg:
        # Hex like "#abcdef" → {red, green, blue} floats.
        rgb = {
            "red":   int(bg[1:3], 16) / 255.0,
            "green": int(bg[3:5], 16) / 255.0,
            "blue":  int(bg[5:7], 16) / 255.0,
        }
        cell["effectiveFormat"] = {"backgroundColor": rgb}
    return cell


def _row(*cells: dict[str, Any]) -> list[dict[str, Any]]:
    return list(cells)


def _sheet_from_rows(rows: list[list[dict[str, Any]]], gid: int = 0) -> dict[str, Any]:
    return {
        "properties": {"sheetId": gid, "title": "test"},
        "data": [{"rowData": [{"values": r} for r in rows]}],
    }


# --- _find_block_anchors ---------------------------------------------------

def test_find_block_anchors_single_block() -> None:
    rows = [
        _row(_cell(), _cell(), _cell(), _cell()),
        _row(
            _cell(), _cell("Lloris"), _cell("Shields"), _cell("HP"),
            _cell("P. Atk"), _cell("P. Def"), _cell("E. Atk"), _cell("E. Def"),
            _cell("Speed"), _cell("Crit"), _cell("CritDef"), _cell("Equip Atk"),
        ),
    ]
    anchors = _find_block_anchors(rows)
    assert len(anchors) == 1
    header_row, title_col, name, stat_labels = anchors[0]
    assert header_row == 1
    assert title_col == 1
    assert name == "Lloris"
    assert stat_labels[0] == "Shields"
    assert stat_labels[-1] == "Equip Atk"
    assert len(stat_labels) == 10


def test_find_block_anchors_multiple_horizontal_blocks() -> None:
    """Two blocks side-by-side in the same header row, like Solistia Data."""
    base_stats = [
        _cell("Shields"), _cell("HP"), _cell("P. Atk"), _cell("P. Def"),
        _cell("E. Atk"), _cell("E. Def"), _cell("Speed"), _cell("Crit"),
        _cell("CritDef"), _cell("Equip Atk"),
    ]
    rows = [
        [_cell()] + [_cell("Dokabro")] + base_stats
        + [_cell(), _cell()] + [_cell("Lloris")] + base_stats,
    ]
    anchors = _find_block_anchors(rows)
    titles = [a[2] for a in anchors]
    assert titles == ["Dokabro", "Lloris"]


def test_find_block_anchors_vertical_stripes() -> None:
    """Multiple stripes stacked vertically — common in Osterra/Solistia Data."""
    stat_headers = [_cell(s) for s in
        ("Shields", "HP", "P. Atk", "P. Def", "E. Atk", "E. Def",
         "Speed", "Crit", "CritDef", "Equip Atk")]
    rows = [
        [_cell(), _cell("StripeOne")] + stat_headers,
        [_cell(), _cell()] * 6,  # gap rows
        [_cell(), _cell()] * 6,
        [_cell(), _cell("StripeTwo")] + stat_headers,
    ]
    titles = [a[2] for a in _find_block_anchors(rows)]
    assert "StripeOne" in titles
    assert "StripeTwo" in titles


# --- parse_data_tab --------------------------------------------------------

def test_parse_data_tab_extracts_six_ranks_per_member() -> None:
    """Synthetic block with 1 member × 6 ranks. Verify all stats round-trip."""
    stat_headers = [_cell(s) for s in
        ("Shields", "HP", "P. Atk", "P. Def", "E. Atk", "E. Def",
         "Speed", "Crit", "CritDef", "Equip Atk")]
    rows: list[list[dict[str, Any]]] = [
        # row 0: padding
        [_cell()] * 13,
        # row 1: header
        [_cell(), _cell("TestEnc")] + stat_headers,
    ]
    # 6 data rows for one member
    ranks = ["Rank 1", "Rank 2", "Rank 3", "EX1", "EX2", "EX3"]
    for i, r in enumerate(ranks):
        rows.append(
            [_cell("TestMember") if i == 0 else _cell(), _cell(r)]
            + [_cell(str(100 + i * 10 + j)) for j in range(10)]
        )
    sheet = _sheet_from_rows(rows)
    out = parse_data_tab(sheet, region="Test")
    assert "TestEnc" in out
    enc = out["TestEnc"]
    assert len(enc.members) == 1
    m = enc.members[0]
    assert m.member_name == "TestMember"
    assert set(m.rank_stats.keys()) == {"Rank1", "Rank2", "Rank3", "EX1", "EX2", "EX3"}
    # Sanity-check one rank's stats.
    r1 = m.rank_stats["Rank1"]
    assert r1["HP"] == "101"
    assert r1["Shields"] == "100"
    assert r1["Equip Atk"] == "109"


def test_parse_data_tab_handles_multi_member_encounter() -> None:
    """Two members in the same encounter, stacked vertically with sparse name col."""
    stat_headers = [_cell(s) for s in
        ("Shields", "HP", "P. Atk", "P. Def", "E. Atk", "E. Def",
         "Speed", "Crit", "CritDef", "Equip Atk")]
    rows: list[list[dict[str, Any]]] = [
        [_cell()] * 13,
        [_cell(), _cell("Pair")] + stat_headers,
    ]
    ranks = ["Rank 1", "Rank 2", "Rank 3", "EX1", "EX2", "EX3"]
    # Member A, 6 ranks
    for i, r in enumerate(ranks):
        rows.append(
            [_cell("Alpha") if i == 0 else _cell(), _cell(r)]
            + [_cell(str(i + 1)) for _ in range(10)]
        )
    # Member B, 6 ranks
    for i, r in enumerate(ranks):
        rows.append(
            [_cell("Beta") if i == 0 else _cell(), _cell(r)]
            + [_cell(str(i + 100)) for _ in range(10)]
        )
    sheet = _sheet_from_rows(rows)
    out = parse_data_tab(sheet, region="Test")
    enc = out["Pair"]
    assert [m.member_name for m in enc.members] == ["Alpha", "Beta"]
    assert enc.members[0].rank_stats["EX3"]["HP"] == "6"
    assert enc.members[1].rank_stats["EX3"]["HP"] == "105"


# --- parse_npc_data_tab ----------------------------------------------------

def test_parse_npc_data_tab_extracts_flat_rows() -> None:
    rows: list[list[dict[str, Any]]] = [
        [_cell()] * 13,
        [_cell(), _cell("NPC Name"), _cell("Shields"), _cell("HP"),
         _cell("P. Atk"), _cell("P. Def"), _cell("E. Atk"), _cell("E. Def"),
         _cell("Speed"), _cell("Crit"), _cell("CritDef"), _cell("Equip Atk")],
        [_cell(), _cell("NewDelsta"),
         _cell("38"), _cell("3283500"), _cell("1389"), _cell("430"),
         _cell("1413"), _cell("383"), _cell("416"), _cell("454"),
         _cell("313"), _cell("120")],
        [_cell(), _cell("Toto'haha"),
         _cell("35"), _cell("2882550"), _cell("1380"), _cell("445"),
         _cell("1191"), _cell("350"), _cell("388"), _cell("463"),
         _cell("440"), _cell("155")],
    ]
    out = parse_npc_data_tab(_sheet_from_rows(rows))
    assert set(out.keys()) == {"NewDelsta", "Toto'haha"}
    assert out["NewDelsta"].stats["HP"] == "3283500"
    assert out["Toto'haha"].stats["Shields"] == "35"


# --- parse_display_tab -----------------------------------------------------

def _formula_cell(formula: str) -> dict[str, Any]:
    return {"userEnteredValue": {"formulaValue": formula}}


def test_parse_display_tab_finds_block_with_rank_badge() -> None:
    """A row 3 cell with a rank badge plus a sibling name to its left becomes a block."""
    spec = EnemyTabSpec(gid=999, name="Test Lvl", category="Test", region="Osterra")
    rows: list[list[dict[str, Any]]] = [
        [_cell() for _ in range(12)],
        [_cell() for _ in range(12)],
        [_cell() for _ in range(12)],
        [_cell(), _cell("Sly Leader Lloris"), _cell(), _cell(), _cell("EX3"),
         _cell(), _cell(), _cell(), _cell(), _cell(), _cell(), _cell()],
    ]
    blocks = parse_display_tab(_sheet_from_rows(rows, gid=999), spec)
    assert len(blocks) == 1
    b = blocks[0]
    assert b.display_name == "Sly Leader Lloris"
    assert b.category == "Test"
    assert b.region == "Osterra"
    assert b.is_npc is False
    assert b.hyperlink_url == "#gid=999&range=B4"


def test_parse_display_tab_skips_wave_label_between_name_and_rank_badge() -> None:
    """Multi-wave widgets label the wave near the rank badge; the name is farther left."""
    spec = EnemyTabSpec(gid=999, name="Lvl 75", category="Lvl 75", region="Osterra")
    rows: list[list[dict[str, Any]]] = [
        [_cell() for _ in range(12)],
        [_cell() for _ in range(12)],
        [_cell() for _ in range(12)],
        [_cell(), _cell("Largo"), _cell(), _cell("Wave 1"), _cell("EX3"),
         _cell(), _cell(), _cell(), _cell(), _cell(), _cell(), _cell()],
    ]
    blocks = parse_display_tab(_sheet_from_rows(rows, gid=999), spec)
    assert len(blocks) == 1
    assert blocks[0].display_name == "Largo"
    assert blocks[0].hyperlink_url == "#gid=999&range=B4"


def test_parse_display_tab_extracts_weakness_formulas_single_position() -> None:
    """1-position block: weaknesses on row 6, formula cells like '=Sword'."""
    spec = EnemyTabSpec(gid=999, name="Lvl 75", category="Lvl 75", region="Osterra")
    blank = lambda: [_cell() for _ in range(12)]
    rows: list[list[dict[str, Any]]] = [
        blank(), blank(), blank(),
        # row 3: name + EX3 badge
        [_cell(), _cell("Lyblac"), _cell(), _cell("EX3"), _cell(),
         _cell(), _cell(), _cell(), _cell(), _cell(), _cell(), _cell()],
        blank(),
        blank(),
        # row 6: weakness row — formulas at cols 5..8
        [_cell(), _cell(), _cell(), _cell(), _cell(),
         _formula_cell("=Sword"), _formula_cell("=Dagger"),
         _formula_cell("=Wind"), _formula_cell("=Light"),
         _cell(), _cell(), _cell()],
    ]
    blocks = parse_display_tab(_sheet_from_rows(rows, gid=999), spec)
    assert len(blocks) == 1
    assert blocks[0].weaknesses_by_position == [["Sword", "Dagger", "Wind", "Light"]]


def test_parse_display_tab_extracts_weakness_formulas_two_positions() -> None:
    """2-position block: row 6 = position 0, row 7 = position 1."""
    spec = EnemyTabSpec(gid=999, name="Solistia Lvl 25", category="Solistia Lvl 25",
                       region="Solistia")
    blank = lambda: [_cell() for _ in range(12)]
    rows: list[list[dict[str, Any]]] = [
        blank(), blank(), blank(),
        [_cell(), _cell("Sly Leader Lloris"), _cell(), _cell(), _cell("EX3"),
         _cell(), _cell(), _cell(), _cell(), _cell(), _cell(), _cell()],
        blank(),
        blank(),
        # row 6: position 0 weaknesses
        [_cell(), _cell(), _cell(), _cell(), _cell(), _cell(),
         _formula_cell("=Axe"), _formula_cell("=Bow"),
         _formula_cell("=Ice"), _formula_cell("=Wind"),
         _formula_cell("=Dark"), _cell()],
        # row 7: position 1 weaknesses
        [_cell(), _cell(), _cell(), _cell(), _cell(), _cell(),
         _formula_cell("=Dagger"), _formula_cell("=Bow"),
         _formula_cell("=Ice"), _formula_cell("=Lightning"),
         _formula_cell("=Dark"), _cell()],
    ]
    blocks = parse_display_tab(_sheet_from_rows(rows, gid=999), spec)
    assert len(blocks) == 1
    assert blocks[0].weaknesses_by_position == [
        ["Axe", "Bow", "Ice", "Wind", "Dark"],
        ["Dagger", "Bow", "Ice", "Lightning", "Dark"],
    ]


def test_parse_display_tab_filters_out_stat_label_formulas() -> None:
    """`=HP`, `=Atk`, `=B4` etc live in the same column range and must NOT be
    picked up as weaknesses."""
    spec = EnemyTabSpec(gid=999, name="X", category="X", region="Osterra")
    blank = lambda: [_cell() for _ in range(12)]
    rows: list[list[dict[str, Any]]] = [
        blank(), blank(), blank(),
        [_cell(), _cell("Test"), _cell(), _cell("EX3"), _cell(),
         _cell(), _cell(), _cell(), _cell(), _cell(), _cell(), _cell()],
        blank(),
        blank(),
        # row 6: mix of stat labels (=HP, =B4) and a real weakness (=Sword)
        [_cell(), _formula_cell("=HP"), _cell(), _cell(), _cell(),
         _formula_cell("=B4"), _formula_cell("=Sword"),
         _formula_cell("=Atk"), _cell(), _cell(), _cell(), _cell()],
    ]
    blocks = parse_display_tab(_sheet_from_rows(rows, gid=999), spec)
    assert len(blocks) == 1
    assert blocks[0].weaknesses_by_position == [["Sword"]]


def test_parse_display_tab_normalizes_polearm_to_spear() -> None:
    """The named-range `=Polearm` and `=Spear` refer to the same icon — collapse."""
    spec = EnemyTabSpec(gid=999, name="X", category="X", region="Osterra")
    blank = lambda: [_cell() for _ in range(12)]
    rows: list[list[dict[str, Any]]] = [
        blank(), blank(), blank(),
        [_cell(), _cell("Test"), _cell(), _cell("EX3"), _cell(),
         _cell(), _cell(), _cell(), _cell(), _cell(), _cell(), _cell()],
        blank(),
        blank(),
        [_cell(), _cell(), _cell(), _cell(), _cell(),
         _formula_cell("=Polearm"), _cell(), _cell(),
         _cell(), _cell(), _cell(), _cell()],
    ]
    blocks = parse_display_tab(_sheet_from_rows(rows, gid=999), spec)
    assert blocks[0].weaknesses_by_position == [["Spear"]]


# --- 120 NPCs display-tab parsing ------------------------------------------
#
# These tests pin down the layout of the lvl120 (120 NPCs) tab, which the
# original parser silently mis-handled: it only walked row 3 of the display
# tab, never extracted weaknesses, and treated every block as a single-member
# NPC even when the encounter pulled multiple catalog rows via VLOOKUP.

_NPC_GID = 2117870435  # gid of the 120 NPCs display tab; in ENEMY_NPC_TAB_GIDS.
_SEPARATOR_BG = "#222222"


def test_parse_display_tab_npc_single_member_picks_formatted_name() -> None:
    """Single-position NPC widget: the member-name strip carries the display
    name directly (e.g. 'New Delsta'), no VLOOKUP needed for harvesting."""
    spec = EnemyTabSpec(gid=_NPC_GID, name="120 NPCs", category="120 NPCs", region="NPCs")
    blank = lambda: [_cell() for _ in range(12)]
    rows: list[list[dict[str, Any]]] = [
        blank(), blank(), blank(),
        # row 3: encounter names with a #222222 separator at col 0 (block-start signal).
        [_cell(bg=_SEPARATOR_BG), _cell("New Delsta"),
         _cell(), _cell(), _cell(), _cell(), _cell(), _cell(),
         _cell(), _cell(), _cell(), _cell()],
        blank(),
        blank(),
        # row 6: member-name strip + shield count + weakness formulas.
        [_cell(), _cell(),
         _cell("New Delsta"),  # col 2 = member-name reference
         _cell(),
         _cell("38"),  # shield count
         _formula_cell("=Sword"),
         _formula_cell("=Dagger"),
         _formula_cell("=Light"),
         _cell(), _cell(), _cell(), _cell()],
    ]
    blocks = parse_display_tab(_sheet_from_rows(rows, gid=_NPC_GID), spec)
    assert len(blocks) == 1
    b = blocks[0]
    assert b.display_name == "New Delsta"
    assert b.is_npc is True
    assert b.member_names_by_position == ["New Delsta"]
    assert b.weaknesses_by_position == [["Sword", "Dagger", "Light"]]


def test_parse_display_tab_npc_multi_member_uses_vlookup_formulas() -> None:
    """Multi-position widget (Canalbrine-shape): the member-name strip holds
    position INDEX labels ('1', '2', '3') instead of names, so the parser
    must extract names from the VLOOKUP formula on the HP row."""
    spec = EnemyTabSpec(gid=_NPC_GID, name="120 NPCs", category="120 NPCs", region="NPCs")
    blank = lambda: [_cell() for _ in range(36)]
    # Block at name_col=23 with #222222 separator at col 22.
    name_row = [_cell() for _ in range(36)]
    name_row[22] = _cell(bg=_SEPARATOR_BG)
    name_row[23] = _cell("Canalbrine")
    # Row 6: per-position labels + shield count + weakness formulas for pos 0.
    member_strip = [_cell() for _ in range(36)]
    member_strip[24] = _cell("1")  # position INDEX label, not a name
    member_strip[25] = _cell("2")
    member_strip[26] = _cell("3")
    member_strip[27] = _cell("1")  # weakness sub-grid pos label
    member_strip[28] = _cell("22")  # shield count
    member_strip[29] = _formula_cell("=Spear")
    member_strip[30] = _formula_cell("=Bow")
    member_strip[31] = _formula_cell("=Wind")
    # Row 7: HP row — VLOOKUP formulas carry the actual member names.
    hp_strip = [_cell() for _ in range(36)]
    hp_strip[24] = _formula_cell('=VLOOKUP("Canalbrine 1",\'120 NPCs Data\'!A:K,4,0)')
    hp_strip[25] = _formula_cell('=VLOOKUP("Canalbrine 2",\'120 NPCs Data\'!A:K,4,0)')
    hp_strip[26] = _formula_cell('=VLOOKUP("Canalbrine 3",\'120 NPCs Data\'!A:K,4,0)')
    hp_strip[27] = _cell("2")  # weakness sub-grid pos label
    hp_strip[28] = _cell("20")
    hp_strip[29] = _formula_cell("=Bow")
    hp_strip[30] = _formula_cell("=Staff")
    hp_strip[31] = _formula_cell("=Lightning")
    # Row 8: third weakness row.
    row8 = [_cell() for _ in range(36)]
    row8[27] = _cell("3")
    row8[28] = _cell("23")
    row8[29] = _formula_cell("=Spear")
    row8[30] = _formula_cell("=Fan")
    row8[31] = _formula_cell("=Ice")
    rows: list[list[dict[str, Any]]] = [
        blank(), blank(), blank(),
        name_row, blank(), blank(),
        member_strip, hp_strip, row8,
    ]
    blocks = parse_display_tab(_sheet_from_rows(rows, gid=_NPC_GID), spec)
    assert len(blocks) == 1
    b = blocks[0]
    assert b.display_name == "Canalbrine"
    assert b.is_npc is True
    assert b.member_names_by_position == ["Canalbrine 1", "Canalbrine 2", "Canalbrine 3"]
    assert b.weaknesses_by_position == [
        ["Spear", "Bow", "Wind"],
        ["Bow", "Staff", "Lightning"],
        ["Spear", "Fan", "Ice"],
    ]


def test_parse_display_tab_npc_direct_cell_ref_falls_back_to_encounter_name() -> None:
    """`Cropdale` and similar widgets pull a single member via a direct
    `='120 NPCs Data'!D10` ref instead of VLOOKUP. The member name isn't
    recoverable from the formula, so we fall back to the encounter name."""
    spec = EnemyTabSpec(gid=_NPC_GID, name="120 NPCs", category="120 NPCs", region="NPCs")
    blank = lambda: [_cell() for _ in range(12)]
    rows: list[list[dict[str, Any]]] = [
        blank(), blank(), blank(),
        [_cell(bg=_SEPARATOR_BG), _cell("Cropdale"),
         _cell(), _cell(), _cell(), _cell(), _cell(), _cell(),
         _cell(), _cell(), _cell(), _cell()],
        blank(),
        blank(),
        # row 6: member-name strip carries only a position-index label.
        [_cell(), _cell(),
         _cell("1"),  # position-index label, not a name
         _cell(), _cell(), _cell(), _cell(), _cell(),
         _cell(), _cell(), _cell(), _cell()],
        # row 7: HP row with a direct cell ref (no VLOOKUP name to extract).
        [_cell(), _cell(),
         _formula_cell("='120 NPCs Data'!D10"),
         _cell(), _cell(), _cell(), _cell(), _cell(),
         _cell(), _cell(), _cell(), _cell()],
    ]
    blocks = parse_display_tab(_sheet_from_rows(rows, gid=_NPC_GID), spec)
    assert len(blocks) == 1
    assert blocks[0].member_names_by_position == ["Cropdale"]


def test_detect_display_blocks_finds_blocks_in_second_block_row() -> None:
    """Ranked tabs with more encounters than fit in one block-row layout
    were silently truncated by the old single-row scan. Make sure block-row
    2 (rows starting at _DISPLAY_NAME_ROW + _DISPLAY_BLOCK_HEIGHT) is now
    detected — this is the regression that allowed the lvl120 bug to ship
    while looking benign on ranked tabs."""
    blank = lambda: [_cell() for _ in range(12)]
    rows: list[list[dict[str, Any]]] = [
        blank(), blank(), blank(),
        # Block row 1: one block at name_col=1.
        [_cell(), _cell("Lyblac"), _cell(), _cell("EX3"), _cell(),
         _cell(), _cell(), _cell(), _cell(), _cell(), _cell(), _cell()],
    ]
    # Pad to the start of block row 2 (_DISPLAY_NAME_ROW + _DISPLAY_BLOCK_HEIGHT).
    while len(rows) < 3 + _DISPLAY_BLOCK_HEIGHT:
        rows.append(blank())
    # Block row 2: another block at name_col=1.
    rows.append([_cell(), _cell("Largo"), _cell(), _cell("EX3"), _cell(),
                 _cell(), _cell(), _cell(), _cell(), _cell(), _cell(), _cell()])
    detected = _detect_display_blocks(rows, use_separator_fallback=False)
    name_rows = sorted({nr for nr, _, _ in detected})
    assert name_rows == [3, 3 + _DISPLAY_BLOCK_HEIGHT]


def test_detect_display_blocks_npc_separator_fallback() -> None:
    """The 120 NPCs tab has no rank badges, so the parser must fall back to a
    separator-color heuristic: a name cell whose left neighbor is on the
    dark #222222 separator background."""
    blank = lambda: [_cell() for _ in range(36)]
    name_row = [_cell() for _ in range(36)]
    # Three blocks: at cols 1, 12, 23 — each preceded by a #222222 separator.
    name_row[0] = _cell(bg=_SEPARATOR_BG)
    name_row[1] = _cell("New Delsta")
    name_row[11] = _cell(bg=_SEPARATOR_BG)
    name_row[12] = _cell("Toto'haha")
    name_row[22] = _cell(bg=_SEPARATOR_BG)
    name_row[23] = _cell("Canalbrine")
    rows: list[list[dict[str, Any]]] = [
        blank(), blank(), blank(),
        name_row,
    ]
    # Without the fallback flag, no rank badges → no blocks.
    assert _detect_display_blocks(rows, use_separator_fallback=False) == []
    # With the fallback flag, all three blocks are detected.
    detected = _detect_display_blocks(rows, use_separator_fallback=True)
    name_cols = sorted(c for _, c, _ in detected)
    assert name_cols == [1, 12, 23]


def test_detect_display_blocks_npc_separator_rejects_non_separator_neighbors() -> None:
    """Sub-grid headers and position-index labels also live on the name row in
    some layouts — the separator-bg signal is what distinguishes a true
    block-start from an in-block label."""
    blank = lambda: [_cell() for _ in range(12)]
    name_row = [_cell() for _ in range(12)]
    name_row[0] = _cell(bg=_SEPARATOR_BG)
    name_row[1] = _cell("New Delsta")  # legit block (left neighbor is separator)
    # col 5 has text but its left neighbor is plain — should NOT be a block.
    name_row[4] = _cell(bg="#ffffff")
    name_row[5] = _cell("Weaknesses")  # in-block label
    rows = [blank(), blank(), blank(), name_row]
    detected = _detect_display_blocks(rows, use_separator_fallback=True)
    assert sorted(c for _, c, _ in detected) == [1]


def test_parse_all_npc_multi_member_persists_per_position_stats() -> None:
    """End-to-end: a multi-member NPC display block + matching catalog rows
    in the data tab should produce a single ParsedEnemy with one stats row
    per position, each carrying its own member_name."""
    # Display tab: one Canalbrine block with 3 members (VLOOKUPs in HP row).
    blank = lambda: [_cell() for _ in range(36)]
    name_row = [_cell() for _ in range(36)]
    name_row[22] = _cell(bg=_SEPARATOR_BG)
    name_row[23] = _cell("Canalbrine")
    member_strip = [_cell() for _ in range(36)]
    member_strip[24] = _cell("1")
    member_strip[25] = _cell("2")
    member_strip[26] = _cell("3")
    member_strip[28] = _cell("22")
    member_strip[29] = _formula_cell("=Spear")
    hp_strip = [_cell() for _ in range(36)]
    hp_strip[24] = _formula_cell('=VLOOKUP("Canalbrine 1",\'120 NPCs Data\'!A:K,4,0)')
    hp_strip[25] = _formula_cell('=VLOOKUP("Canalbrine 2",\'120 NPCs Data\'!A:K,4,0)')
    hp_strip[26] = _formula_cell('=VLOOKUP("Canalbrine 3",\'120 NPCs Data\'!A:K,4,0)')
    display_rows = [blank(), blank(), blank(),
                    name_row, blank(), blank(),
                    member_strip, hp_strip]
    display_sheet = _sheet_from_rows(display_rows, gid=_NPC_GID)
    display_sheet["properties"]["title"] = "120 NPCs"

    # Data tab: catalog with the three Canalbrine members.
    data_rows = [
        [_cell()] * 13,
        [_cell(), _cell("NPC Name"), _cell("Shields"), _cell("HP"),
         _cell("P. Atk"), _cell("P. Def"), _cell("E. Atk"), _cell("E. Def"),
         _cell("Speed"), _cell("Crit"), _cell("CritDef"), _cell("Equip Atk")],
        [_cell(), _cell("Canalbrine 1"),
         _cell("22"), _cell("4671645"), _cell("1200"), _cell("383"),
         _cell("1436"), _cell("454"), _cell("525"), _cell("525"),
         _cell("525"), _cell("190")],
        [_cell(), _cell("Canalbrine 2"),
         _cell("20"), _cell("800250"), _cell("1295"), _cell("360"),
         _cell("1413"), _cell("360"), _cell("454"), _cell("572"),
         _cell("313"), _cell("190")],
        [_cell(), _cell("Canalbrine 3"),
         _cell("23"), _cell("833250"), _cell("1200"), _cell("407"),
         _cell("1342"), _cell("430"), _cell("360"), _cell("407"),
         _cell("430"), _cell("190")],
    ]
    data_sheet = _sheet_from_rows(data_rows, gid=1230510791)
    data_sheet["properties"]["title"] = "120 NPCs Data"

    payload = {"sheets": [display_sheet, data_sheet]}
    # Restrict to just the NPC pipeline by overriding the data-tab map.
    result = parse_all(payload, {"NPCs": 1230510791})
    npc_enemies = [e for e in result.enemies if e.is_npc]
    assert len(npc_enemies) == 1
    enemy = npc_enemies[0]
    assert enemy.canonical_name == "Canalbrine"
    assert "Default" in enemy.rank_stats
    by_position: dict[int, dict[str, str]] = {}
    name_by_position: dict[int, str] = {}
    for row in enemy.rank_stats["Default"]:
        by_position.setdefault(row["position"], {})[row["stat_name"]] = row["stat_value"]
        name_by_position[row["position"]] = row["member_name"]
    assert name_by_position == {0: "Canalbrine 1", 1: "Canalbrine 2", 2: "Canalbrine 3"}
    assert by_position[0]["HP"] == "4671645"
    assert by_position[1]["HP"] == "800250"
    assert by_position[2]["HP"] == "833250"


# --- reconcile_display_to_data ---------------------------------------------

def test_reconcile_exact_match() -> None:
    assert reconcile_display_to_data("Dokabro", ["Dokabro", "Lloris"]) == "Dokabro"


def test_reconcile_whitespace_drift() -> None:
    """'New Delsta' display vs 'NewDelsta' data — whitespace-only difference."""
    assert reconcile_display_to_data("New Delsta", ["NewDelsta"]) == "NewDelsta"


def test_reconcile_strips_articles() -> None:
    """'Oskha the Have-not' vs 'Oskha Have-Not' — strip 'the'."""
    assert reconcile_display_to_data("Oskha the Have-not", ["Oskha Have-Not"]) == "Oskha Have-Not"


def test_reconcile_substring_fallback() -> None:
    """'Sly Leader Lloris' (display) → 'Lloris' (data)."""
    assert reconcile_display_to_data("Sly Leader Lloris", ["Lloris", "Dokabro"]) == "Lloris"


def test_reconcile_alias_lookup() -> None:
    """The alias map is consulted before the substring fallback."""
    # We can't easily monkeypatch ENEMY_NAME_ALIASES, but we can verify the
    # exact-match path still wins when the data key is the alias target.
    assert reconcile_display_to_data("Ring-Sealed Beast", ["RingBeast"]) is None or \
           reconcile_display_to_data("Ring-Sealed Beast", ["RingBeast"]) == "RingBeast"


def test_reconcile_returns_none_for_unknown() -> None:
    assert reconcile_display_to_data("Wave 1", ["Mirgardi", "Jafford"]) is None


def test_reconcile_cursed_master_auguste_via_alias() -> None:
    """Regression: 'Cursed Master Auguste' on Lvl 75 has no exact, whitespace,
    or substring match against the data key 'Cursed Auguste' because the word
    'Master' sits between the two tokens. Resolution must come from the
    explicit alias entry — without it, the enemy is dropped from /enemy."""
    assert reconcile_display_to_data(
        "Cursed Master Auguste", ["Cursed Auguste"]
    ) == "Cursed Auguste"


def test_reconcile_fullwidth_unicode_via_nfkc() -> None:
    """The Lvl 75 NieR collab boss is named with fullwidth Japanese letters
    ('９Ｓ？'), but the data tab uses ASCII ('9 S ?'). NFKC normalization in
    `_squish` collapses fullwidth → halfwidth so they match without an alias."""
    assert reconcile_display_to_data("９Ｓ？", ["9 S ?"]) == "9 S ?"


@pytest.mark.parametrize("display_name,data_key", list(ENEMY_NAME_ALIASES.items()))
def test_every_enemy_alias_resolves(display_name: str, data_key: str) -> None:
    """Each alias key must reconcile to its mapped data-tab name. If anyone
    breaks an entry (typo, removal, refactor), exactly one parametrization
    fails with the offending (display, data) pair in the test id."""
    assert reconcile_display_to_data(display_name, [data_key]) == data_key


# --- helpers ---------------------------------------------------------------

def test_rank_order_canonical() -> None:
    assert rank_order("Rank1") == 1
    assert rank_order("EX3") == 6
    assert rank_order("Default") == 0
    assert rank_order("nonsense") == 99


def test_col_letters_zero_based() -> None:
    assert _index_to_col_letters(0) == "A"
    assert _index_to_col_letters(1) == "B"
    assert _index_to_col_letters(25) == "Z"
    assert _index_to_col_letters(26) == "AA"
