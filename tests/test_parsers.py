"""Parser-layer tests: color decoding, anchor parsing, role-tab block detection."""
from __future__ import annotations

import pytest

from config import color_family, rarity_from_color, canonicalize_name, NAME_ALIASES
from sync.parsers import (
    Anchor,
    _color_dict_to_hex,
    _classify_skill_kind,
    _parse_skill_description,
    parse_anchor,
    parse_index,
    parse_role_tab,
)


# --- color helpers ----------------------------------------------------------

@pytest.mark.parametrize("hex_in,family", [
    ("#CC0000", "red"),
    ("#FF0000", "red"),
    ("#FFCC00", "yellow"),
    ("#3366CC", "blue"),
    ("#00AA00", "green"),
    ("#FFFFFF", "white"),
    ("#000000", "black"),
])
def test_color_family_buckets(hex_in: str, family: str) -> None:
    assert color_family(hex_in) == family


@pytest.mark.parametrize("hex_in,rarity", [
    ("#CC0000", "5*"),
    ("#00AA00", "free35"),
    ("#FFCC00", "4*"),
    ("#3366CC", "3*"),
    ("#FFFFFF", None),
])
def test_rarity_from_color(hex_in: str, rarity: str | None) -> None:
    assert rarity_from_color(hex_in) == rarity


def test_color_family_handles_garbage() -> None:
    assert color_family(None) is None
    assert color_family("") is None
    assert color_family("not-hex") is None


def test_color_dict_to_hex_floats() -> None:
    """Sheets API returns colors as 0..1 floats; missing channels default to 0."""
    assert _color_dict_to_hex({"red": 1.0}) == "#FF0000"
    assert _color_dict_to_hex({"red": 0.8, "green": 0.0, "blue": 0.0}) == "#CC0000"
    # The Sheets API uses absent / empty color objects to mean "no explicit
    # color set" — we surface that as None so the rarity decoder doesn't
    # mis-classify default-styled cells as black-rarity entries.
    assert _color_dict_to_hex({}) is None
    assert _color_dict_to_hex(None) is None


# --- anchor parsing ---------------------------------------------------------

def test_parse_anchor_gid_range() -> None:
    a = parse_anchor("#gid=519845584&range=B5")
    assert a == Anchor(gid=519845584, row=4, col=1)


def test_parse_anchor_with_sheet_name() -> None:
    a = parse_anchor("#gid=519845584&range=Warriors!AA10")
    assert a == Anchor(gid=519845584, row=9, col=26)


def test_parse_anchor_handles_missing_or_garbage() -> None:
    assert parse_anchor(None) is None
    assert parse_anchor("") is None
    assert parse_anchor("https://example.com/") is None
    # rangeid is unsupported (intentionally — no public way to resolve)
    assert parse_anchor("#rangeid=1234567") is None


# --- skill kind classifier --------------------------------------------------

def test_classify_skill_kind_active_default() -> None:
    assert _classify_skill_kind(None) == ("active", None)
    assert _classify_skill_kind("") == ("active", None)


def test_classify_skill_kind_boost_levels() -> None:
    assert _classify_skill_kind("1*") == ("ultimate", 1)
    assert _classify_skill_kind("3*") == ("ultimate", 3)


def test_classify_skill_kind_known_labels() -> None:
    assert _classify_skill_kind("Passive") == ("passive", None)
    assert _classify_skill_kind("TP") == ("divine", None)
    assert _classify_skill_kind("EX") == ("ex", None)
    assert _classify_skill_kind("Special") == ("special", None)


def test_classify_skill_kind_special_levels() -> None:
    kind, lvl = _classify_skill_kind("Lv10")
    assert kind == "special" and lvl == 10
    kind, lvl = _classify_skill_kind("Lv1")
    assert kind == "special" and lvl == 1


# --- skill description parser ----------------------------------------------

def test_parse_skill_description_extracts_power() -> None:
    out = _parse_skill_description("1x single-target Sword (1x 170~350 Power)")
    assert out["hits"] == 1
    assert out["power_min"] == 170
    assert out["power_max"] == 350


def test_parse_skill_description_handles_singular_power() -> None:
    out = _parse_skill_description("1x single-target Fire (1x 500 Power)")
    assert out["hits"] == 1
    assert out["power_min"] == 500
    assert out["power_max"] == 500


def test_parse_skill_description_no_match_is_safe() -> None:
    out = _parse_skill_description("Self 15% Atk Up for 3 turns")
    assert "power_min" not in out  # not extracted, not a crash
    assert out["description"] == "Self 15% Atk Up for 3 turns"


# --- aliases ----------------------------------------------------------------

def test_canonicalize_name_passthrough_for_unknown() -> None:
    assert canonicalize_name("Cyrus") == "Cyrus"


def test_canonicalize_name_known_aliases() -> None:
    assert canonicalize_name("Fior") == "Fiore"
    assert canonicalize_name("Krauser") == "Clauser"
    assert canonicalize_name("Araune") == "Alaune"
    assert canonicalize_name("Elrica") == "Erika"


def test_alias_table_has_no_circular_or_chained_entries() -> None:
    """An alias's value must not itself appear as a key (no chains)."""
    keys = set(NAME_ALIASES.keys())
    values = set(NAME_ALIASES.values())
    assert not (keys & values), \
        f"alias chains detected (key→value→key): {keys & values}"


# --- role-tab block detection (synthetic payload) --------------------------

def _cell(text: str = "", color: dict | None = None, hyperlink: str | None = None) -> dict:
    out: dict = {}
    if text:
        out["formattedValue"] = text
    if color:
        out["effectiveFormat"] = {"textFormat": {"foregroundColor": color}}
    if hyperlink:
        out["hyperlink"] = hyperlink
    return out


def _make_role_sheet(rows: list[list[dict]]) -> dict:
    return {"data": [{"rowData": [{"values": r} for r in rows]}]}


def test_parse_role_tab_detects_blocks_by_anchor_pattern() -> None:
    """Block start = (col 0 has name) AND (col 6 == 'SP') AND (col 7 == 'Active')."""
    # Build a 2-character mini-sheet
    rows = []
    # row 0: section header
    rows.append([_cell()] * 5 + [_cell("Skills"), _cell(), _cell()])
    # row 1: Cyrus block start.
    # Layout (matches the live role-tab format):
    #   col 0    = name 'Cyrus'
    #   cols 1-5 = stat-cell placeholders
    #   col 6    = 'SP' marker
    #   col 7    = 'Active' marker
    #   cols 8-20 = skill region (13 empty here)
    #   col 21   = equipment name
    #   cols 22-24 = other-info filler
    #   col 25   = 'Splash Art' (profile header)
    rows.append([
        _cell("Cyrus"),
        _cell(), _cell(), _cell(), _cell(), _cell(),
        _cell("SP"), _cell("Active"),
    ] + [_cell()] * 13 + [
        _cell("Cyrus's Tome"),
        _cell(), _cell(), _cell(),
        _cell("Splash Art"),
    ])
    # rows 2-3: skills
    rows.append([_cell()] * 5 + [_cell(), _cell("18"), _cell("1x Fire")])
    rows.append([_cell()] * 5 + [_cell("1*"), _cell("30"), _cell("AoE Fire")])
    # row 4: empty separator
    rows.append([_cell()] * 8)
    # row 5: Therion block start
    rows.append([
        _cell("Therion"),
        _cell(), _cell(), _cell(), _cell(), _cell(),
        _cell("SP"), _cell("Active"),
    ])
    rows.append([_cell()] * 5 + [_cell(), _cell("20"), _cell("1x Slash")])

    sheet = _make_role_sheet(rows)
    blocks = parse_role_tab(sheet, gid=999)

    assert len(blocks) == 2
    assert blocks[0].display_name == "Cyrus"
    assert blocks[1].display_name == "Therion"
    # Cyrus has 2 skills, equipment, splash header
    assert len(blocks[0].skills) == 2
    assert any(e["name"] == "Cyrus's Tome" for e in blocks[0].equipment)
    # Therion has 1 skill, no equipment
    assert len(blocks[1].skills) == 1


def test_parse_role_tab_ignores_rows_without_sp_active_marker() -> None:
    """Stat rows or stray text must NOT be picked up as block starts."""
    rows = [
        [_cell()] * 5 + [_cell("Skills"), _cell(), _cell()],
        # spurious: col 0 has text but no SP/Active in cols 6/7
        [_cell("Resists"), _cell()] * 4,
        # spurious: SP without name
        [_cell()] * 6 + [_cell("SP"), _cell("Active")],
    ]
    sheet = _make_role_sheet(rows)
    blocks = parse_role_tab(sheet, gid=999)
    assert blocks == []


def test_parse_role_tab_handles_short_rows() -> None:
    """Some rows in the live sheet have <8 cells — must not raise."""
    rows = [[_cell("X")], [_cell()] * 3]
    sheet = _make_role_sheet(rows)
    assert parse_role_tab(sheet, gid=999) == []


# --- index parser ----------------------------------------------------------

def test_parse_index_extracts_role_columns_and_rarity() -> None:
    """Synthetic Index: role headers in row 0, character entries from row 1."""
    rows = [
        # role headers at cols 1, 12
        [_cell()] + [_cell("Warrior (Sword)")] + [_cell()] * 10
        + [_cell("Merchant (Spear)")] + [_cell()],
        # character row 1
        [_cell()] + [_cell("Cyrus", color={"red": 0.8}, hyperlink="http://example/cyrus")]
        + [_cell()] * 10
        + [_cell("Tressa", color={"green": 0.6}, hyperlink="http://example/tressa")]
        + [_cell()],
    ]
    sheet = _make_role_sheet(rows)
    # parse_index requires header to be detected by the Warrior/Merchant regex,
    # but it expects ≥4 role columns in the row. So this test only validates
    # the sub-routines, not the full pipeline.
    # Build a wider header row with all 8 roles to exercise the real path.
    role_names = ["Warrior (Sword)", "Merchant (Spear)", "Thief (Dagger)",
                  "Apothecary (Axe)", "Hunter (Bow)", "Cleric (Staff)",
                  "Scholar (Tome)", "Dancer (Fan)"]
    row_count = len(role_names) * 11
    header = [_cell()] * row_count
    for i, name in enumerate(role_names):
        header[i * 11 + 1] = _cell(name)
    char_row = [_cell()] * row_count
    char_row[1] = _cell("Cyrus", color={"red": 0.8}, hyperlink="http://example/cyrus")
    char_row[12] = _cell("Tressa", color={"green": 0.6}, hyperlink="http://example/tressa")
    sheet = _make_role_sheet([header, char_row])
    entries = parse_index(sheet)
    # both should be detected
    names = {e.canonical_name for e in entries}
    assert "Cyrus" in names
    assert "Tressa" in names
    cyrus = next(e for e in entries if e.canonical_name == "Cyrus")
    assert cyrus.role == "warrior"
    assert cyrus.weapon == "sword"
    assert cyrus.rarity == "5*"  # red → 5★
    tressa = next(e for e in entries if e.canonical_name == "Tressa")
    assert tressa.role == "merchant"
    assert tressa.rarity == "free35"  # green → free 3→5★
