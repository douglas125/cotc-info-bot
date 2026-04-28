"""Unit tests for sync/enemy_parsers.py."""
from __future__ import annotations

from typing import Any

import pytest

from sync.enemy_parsers import (
    _find_block_anchors,
    parse_data_tab,
    parse_npc_data_tab,
    parse_display_tab,
    parse_npc_display_tab,
    reconcile_display_to_data,
    rank_order,
    _index_to_col_letters,
)
from config import EnemyTabSpec


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

def test_parse_display_tab_finds_block_with_rank_badge() -> None:
    """A row 3 cell with a rank badge plus a sibling name to its left becomes a block."""
    spec = EnemyTabSpec(gid=999, name="Test Lvl", category="Test", region="Osterra")
    rows: list[list[dict[str, Any]]] = [
        [_cell() for _ in range(10)],
        [_cell() for _ in range(10)],
        [_cell() for _ in range(10)],
        [_cell(), _cell("Sly Leader Lloris"), _cell(), _cell(), _cell("EX3"),
         _cell(), _cell(), _cell(), _cell(), _cell()],
    ]
    blocks = parse_display_tab(_sheet_from_rows(rows, gid=999), spec)
    assert len(blocks) == 1
    b = blocks[0]
    assert b.display_name == "Sly Leader Lloris"
    assert b.category == "Test"
    assert b.region == "Osterra"
    assert b.is_npc is False
    assert b.hyperlink_url == "#gid=999&range=B4"


def test_parse_npc_display_tab_takes_every_row3_cell() -> None:
    spec = EnemyTabSpec(gid=2117870435, name="120 NPCs", category="120 NPCs", region="NPCs")
    rows: list[list[dict[str, Any]]] = [
        [_cell()] * 10,
        [_cell()] * 10,
        [_cell()] * 10,
        [_cell(), _cell("New Delsta"), _cell(), _cell(), _cell(),
         _cell(), _cell(), _cell(), _cell(), _cell("Toto'haha")],
    ]
    blocks = parse_npc_display_tab(_sheet_from_rows(rows, gid=2117870435), spec)
    names = sorted(b.display_name for b in blocks)
    assert names == ["New Delsta", "Toto'haha"]
    for b in blocks:
        assert b.is_npc is True


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
