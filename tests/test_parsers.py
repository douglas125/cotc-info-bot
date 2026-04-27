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
    assert _classify_skill_kind(None) == ("active", None, None)
    assert _classify_skill_kind("") == ("active", None, None)


def test_classify_skill_kind_board_markers_have_no_kind() -> None:
    """Board indicators (1*..6*) carry the prestige-board number only — they
    are NOT a skill kind. The caller resolves kind from row context."""
    assert _classify_skill_kind("1*") == (None, 1, None)
    assert _classify_skill_kind("3*") == (None, 3, None)
    assert _classify_skill_kind("6*") == (None, 6, None)


def test_classify_skill_kind_known_labels() -> None:
    assert _classify_skill_kind("Passive") == ("passive", None, None)
    assert _classify_skill_kind("TP") == ("divine", None, None)
    assert _classify_skill_kind("EX") == ("ex", None, None)
    # Sheet's "Special" === the unit's ultimate skill.
    assert _classify_skill_kind("Special") == ("ultimate", None, None)
    assert _classify_skill_kind("Ult") == ("ultimate", None, None)


def test_classify_skill_kind_special_levels() -> None:
    """Lv1/Lv10/Lv20 rows are upgrade tiers of the same single ultimate skill."""
    assert _classify_skill_kind("Lv10") == ("ultimate", None, 10)
    assert _classify_skill_kind("Lv1") == ("ultimate", None, 1)
    assert _classify_skill_kind("Lv20") == ("ultimate", None, 20)


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


def _row(*, sp: str = "", kind: str = "", desc: str = "",
         passive_desc: str = "", latent_desc: str = "",
         icon17: str = "", icon19: str = "",
         equipment: str = "") -> list[dict]:
    """Build a 26-wide synthetic role-tab row that mirrors the live layout:
    col 0=name, col 5=kind/board (or section divider, or latent text),
    col 6=SP cost OR passive description, col 7=active/special/ex/tp desc,
    cols 17/19=latent icon counters, col 21=equipment, col 25=profile."""
    cells = [_cell()] * 26
    if sp:
        cells[6] = _cell(sp)
    if kind:
        cells[5] = _cell(kind)
    if desc:
        cells[7] = _cell(desc)
    if passive_desc:
        cells[6] = _cell(passive_desc)
    if latent_desc:
        cells[5] = _cell(latent_desc)
    if icon17:
        cells[17] = _cell(icon17)
    if icon19:
        cells[19] = _cell(icon19)
    if equipment:
        cells[21] = _cell(equipment)
    return cells


def _section_divider(name: str) -> list[dict]:
    """A divider row carries the section name in col 5 with cols 6/7 empty."""
    cells = [_cell()] * 26
    cells[5] = _cell(name)
    return cells


def test_parse_role_tab_castti_shape_classifies_sections_correctly() -> None:
    """Synthetic Castti-like block: verifies the section-aware parser.

    The fix guards against a regression where every "N*" row was tagged
    `kind="ultimate"` (one per board indicator). After the fix:
    - units have at most 1 EX skill,
    - the unit's single ultimate is captured as 3 tier rows (Lv1/Lv10/Lv20),
    - "N*" rows are board indicators only (kind is active or passive
      depending on which section they sit in),
    - the latent power is one row with init/cooldown counters.

    Per-section column layout matches the live sheet:
    section dividers and Latent text live in col 5, passive descriptions
    in col 6, everything else in col 7.
    """
    rows = []
    # block header (col 0=name, col 6=SP, col 7=Active)
    header = [_cell()] * 26
    header[0] = _cell("Castti")
    header[6] = _cell("SP")
    header[7] = _cell("Active")
    header[21] = _cell("Eir Apothecary Prescription")
    header[25] = _cell("Splash Art")
    rows.append(header)
    # active rows — first two have no board marker
    rows.append(_row(sp="40", desc="Frontrow Regen for 2-5 turns"))
    rows.append(_row(sp="30", desc="1x AoE Axe (1x 150~260 Power)"))
    rows.append(_row(kind="1*", sp="15", desc="Single-Ally Remove Status"))
    rows.append(_row(kind="2*", sp="46", desc="3x single-target Axe (3x 65~120 Power)"))
    rows.append(_row(kind="2*", sp="60", desc="Single-Ally Auto-Revive"))
    rows.append(_row(kind="3*", sp="68", desc="3x AoE Axe (3x 80~130 Power)"))
    rows.append(_row(kind="4*", sp="72", desc="[Priority] 4x random-target Axe"))
    rows.append(_row(kind="5*", sp="48", desc="2x AoE Axe"))
    rows.append(_row(kind="5*", sp="70", desc="5x single-target Axe (5x 65~120 Power)"))
    # TP / divine — consumes SP
    rows.append(_row(kind="TP", sp="40", desc="1x single-target Axe (1x 260~450 Power)"))
    # EX — no SP cost (the parser must drop any SP cell value here)
    rows.append(_row(kind="EX", sp="99", desc="All Allies 15% Atk Up + 15% Axe Damage Up for 5 turns"))
    # Special section divider, then 3 ultimate-tier rows
    rows.append(_section_divider("Special"))
    rows.append(_row(kind="Lv1",  desc="All Allies Heal + Recover 50 SP for 2 turns"))
    rows.append(_row(kind="Lv10", desc="All Allies Heal + Recover 100 SP for 3 turns"))
    rows.append(_row(kind="Lv20", desc="All Allies Heal + Recover 150 SP for 3 turns"))
    # Latent Power section: divider, then content row(s) carrying multiline
    # text in col 5 and integer counters in cols 17 / 19.
    rows.append(_section_divider("Latent Power"))
    rows.append(_row(latent_desc='Gain "Every Drop Counts" for 1 turn',
                     icon17="3", icon19="6"))
    rows.append(_row(latent_desc='"Berry Panacea": Frontrow Heal and 25 SP Restore'))
    rows.append(_row(latent_desc='"Pomegranate Panacea": Frontrow Grant 1~4 BP'))
    # Passive section: divider, then 2 board-marked passives whose
    # descriptions live in col 6 (the live sheet's quirky layout).
    rows.append(_section_divider("Passive"))
    rows.append(_row(kind="1*", passive_desc="After a Frontrow Ally uses an Axe attack/ability"))
    rows.append(_row(kind="3*", passive_desc="While at Full HP, Frontrow 15% Atk/Crit Up"))

    sheet = _make_role_sheet(rows)
    blocks = parse_role_tab(sheet, gid=519845584)
    assert len(blocks) == 1
    castti = blocks[0]

    # 1. Exactly one EX skill, no SP cost.
    ex = [s for s in castti.skills if s["kind"] == "ex"]
    assert len(ex) == 1
    assert ex[0]["sp_cost"] is None

    # 2. Three ultimate rows (the 3 tiers of the one Special skill), no SP cost.
    ult = [s for s in castti.skills if s["kind"] == "ultimate"]
    assert len(ult) == 3
    assert {s["tier_level"] for s in ult} == {1, 10, 20}
    assert all(s["sp_cost"] is None for s in ult)

    # 3. TP / divine row keeps its SP cost (40 SP).
    divine = [s for s in castti.skills if s["kind"] == "divine"]
    assert len(divine) == 1
    assert divine[0]["sp_cost"] == 40

    # 4. Active rows: include the bare-SP rows AND the "N*"-marked active rows.
    active = [s for s in castti.skills if s["kind"] == "active"]
    boards_in_active = sorted(s["learn_board"] for s in active if s["learn_board"])
    assert boards_in_active == [1, 2, 2, 3, 4, 5, 5]
    # First two active rows had no board marker.
    no_board_actives = [s for s in active if s["learn_board"] is None]
    assert len(no_board_actives) == 2
    # Crucially: NO active row was misclassified as ultimate just because col 5 is "N*".
    assert all(s["tier_level"] is None for s in active)

    # 5. Passive section: "1*" / "3*" rows are passives with learn_board set, no SP.
    passive = [s for s in castti.skills if s["kind"] == "passive"]
    assert len(passive) == 2
    assert sorted(s["learn_board"] for s in passive) == [1, 3]
    assert all(s["sp_cost"] is None for s in passive)

    # 6. Latent Power: exactly one consolidated row, multi-line description,
    #    initial_use=3, cooldown=6, no SP cost.
    latent = [s for s in castti.skills if s["kind"] == "latent"]
    assert len(latent) == 1
    lp = latent[0]
    assert lp["sp_cost"] is None
    assert lp["initial_use"] == 3
    assert lp["cooldown"] == 6
    assert "Every Drop Counts" in lp["description"]
    assert "Berry Panacea" in lp["description"]
    assert "Pomegranate Panacea" in lp["description"]
    assert lp["description"].count("\n") >= 2  # 3 lines joined


def test_parse_role_tab_extracts_a4_accessories_with_exclusivity() -> None:
    """The accessory column (col 21) is the unit's A4 accessories — primary
    one in the block-header row, plus optional 'Exclusive Accessory N' rows
    flagged in col 20. Pure-numeric col-21 stat cells are not accessories
    and must be filtered out."""
    rows = []
    header = [_cell()] * 26
    header[0] = _cell("Sample")
    header[6] = _cell("SP")
    header[7] = _cell("Active")
    header[21] = _cell("Sample's Insignia")  # primary A4 accessory NAME
    header[25] = _cell("Splash Art")
    rows.append(header)
    # Row +1: stat number in c21, effect text in c23 — describes the primary.
    eff_row = [_cell()] * 26
    eff_row[6] = _cell("40")
    eff_row[7] = _cell("active skill desc")
    eff_row[21] = _cell("40")             # numeric stat — must NOT become equipment
    eff_row[23] = _cell("Self 10% Fire Damage Up")
    rows.append(eff_row)
    # Active rows with stat numbers in c21
    extra = [_cell()] * 26
    extra[6] = _cell("30")
    extra[7] = _cell("another active")
    extra[21] = _cell("60")               # numeric stat — must NOT become equipment
    rows.append(extra)
    # Exclusive accessory marker in c20
    excl_marker = [_cell()] * 26
    excl_marker[5] = _cell("1*")
    excl_marker[6] = _cell("18")
    excl_marker[7] = _cell("board-1 active skill")
    excl_marker[20] = _cell("Exclusive Accessory 1")
    rows.append(excl_marker)
    # Effect description for the exclusive — c23 of the row immediately after
    excl_effect = [_cell()] * 26
    excl_effect[5] = _cell("2*")
    excl_effect[6] = _cell("22")
    excl_effect[7] = _cell("board-2 active skill")
    excl_effect[21] = _cell("60")
    excl_effect[23] = _cell("Self gains BP at start of battle")
    rows.append(excl_effect)

    sheet = _make_role_sheet(rows)
    blocks = parse_role_tab(sheet, gid=999)
    assert len(blocks) == 1
    eq = blocks[0].equipment

    # Primary A4 accessory: name from header c21, effect from row +1 c23,
    # is_exclusive=False.
    primary = [e for e in eq if e["name"] == "Sample's Insignia"]
    assert len(primary) == 1
    assert primary[0]["description"] == "Self 10% Fire Damage Up"
    assert primary[0]["is_exclusive"] is False

    # Exclusive accessory: name from c20 marker, effect from next row's c23,
    # is_exclusive=True.
    excl = [e for e in eq if e["name"] == "Exclusive Accessory 1"]
    assert len(excl) == 1
    assert excl[0]["description"] == "Self gains BP at start of battle"
    assert excl[0]["is_exclusive"] is True

    # Pure-numeric col-21 cells (40, 60) must NOT have become equipment rows.
    assert "40" not in {e["name"] for e in eq}
    assert "60" not in {e["name"] for e in eq}
    # Total: just the two we asserted on above.
    assert len(eq) == 2


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
