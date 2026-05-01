"""Unit tests for the pet parser.

Each test builds a tiny synthetic Sheets-API payload (only the fields
the parser actually reads) so the cases stay hermetic. The block layout
mirrors the real sheet: name in col 0, "HP"/value at cols 2/3, "SP"/value
at 5/6, ability at col 7, source at col 9; rows r₀+1..3 carry the
remaining stat pairs.
"""
from __future__ import annotations

from sync.pet_parsers import (
    _extract_canonical_name,
    _parse_ability_block,
    parse_pets,
)


def _cell(text: str = "") -> dict:
    return {"formattedValue": text} if text else {}


def _row(*texts: str) -> list[dict]:
    return [_cell(t) for t in texts]


def _block(
    name: str,
    ability: str,
    source: str,
    *,
    hp: str = "100", sp: str = "10",
    patk: str = "1", pdef: str = "2",
    matk: str = "3", mdef: str = "4",
    crit: str = "5", speed: str = "6",
) -> list[list[dict]]:
    return [
        _row(name, "", "HP", hp,   "", "SP",    sp,    ability, "", source),
        _row("",   "", "Patk", patk, "", "Pdef", pdef, "", "", ""),
        _row("",   "", "Matk", matk, "", "Mdef", mdef, "", "", ""),
        _row("",   "", "Crit", crit, "", "Speed", speed, "", "", ""),
    ]


def _payload(*blocks: list[list[dict]], gid: int = 999) -> dict:
    rows: list[dict] = []
    # Pad with a couple of header rows so source_row indexing != 0.
    rows.append({"values": _row("Header")})
    rows.append({"values": _row("")})
    for block in blocks:
        for row in block:
            rows.append({"values": row})
    return {
        "sheets": [{
            "properties": {"sheetId": gid, "title": "Pet List"},
            "data": [{"rowData": rows}],
        }],
    }


# --- name extraction --------------------------------------------------------

def test_extract_canonical_name_simple() -> None:
    assert _extract_canonical_name("赤茶 (Red Brown Cat)") == (
        "Red Brown Cat", "赤茶 (Red Brown Cat)",
    )


def test_extract_canonical_name_nested_takes_last_paren() -> None:
    canonical, _ = _extract_canonical_name("ルールー (紫) (Purple Lulu )")
    assert canonical == "Purple Lulu"


def test_extract_canonical_name_no_parens_falls_back() -> None:
    canonical, _ = _extract_canonical_name("PlainName")
    assert canonical == "PlainName"


def test_extract_canonical_name_strips_tab_separator() -> None:
    """One real-world entry uses a tab between JP and English."""
    canonical, _ = _extract_canonical_name("黒茶\t(Black Brown Dog)")
    assert canonical == "Black Brown Dog"


# --- ability-block parsing --------------------------------------------------

def test_parse_ability_block_full() -> None:
    text = (
        "Grant Self 5% - 10% Patk Up 1T\n"
        "\n"
        "Max Boost: Lv2\n"
        "Turn Preparation: 1 (Lv10: 0)\n"
        "Turn Cooldown: 6 (Lv5: 5)"
    )
    warnings: list[str] = []
    eff, mb, p, plv10, c, clv5 = _parse_ability_block(
        text, pet_label="Test Cat", warnings=warnings,
    )
    assert eff == "Grant Self 5% - 10% Patk Up 1T"
    assert mb == "Lv2"
    assert (p, plv10, c, clv5) == (1, 0, 6, 5)
    assert warnings == []


def test_parse_ability_block_no_max_boost() -> None:
    text = (
        "Grant owner 30% Patk/Matk Up 1T\n"
        "\n"
        "\n"
        "Turn Preparation: 13 (Lv10: 12)\n"
        "Turn Cooldown: 13 (Lv5: 12)"
    )
    warnings: list[str] = []
    eff, mb, p, plv10, c, clv5 = _parse_ability_block(
        text, pet_label="x", warnings=warnings,
    )
    assert mb is None
    assert (p, plv10, c, clv5) == (13, 12, 13, 12)
    assert "Patk/Matk Up 1T" in eff


def test_parse_ability_block_lv_period_typo() -> None:
    """Real-world 'Turn Preparation: 3 (Lv.10: 2)' must still parse."""
    text = "Recover SP\nTurn Preparation: 3 (Lv.10: 2)\nTurn Cooldown: 3 (Lv5: 2)"
    _, _, p, plv10, c, clv5 = _parse_ability_block(text, pet_label="x", warnings=[])
    assert (p, plv10, c, clv5) == (3, 2, 3, 2)


def test_parse_ability_block_lv_colon_typo() -> None:
    """Cooldown line missing the digit before colon: 'Lv: 6'."""
    text = "Inflict\nTurn Preparation: 4 (Lv10: 3)\nTurn Cooldown: 7 (Lv: 6)"
    _, _, p, plv10, c, clv5 = _parse_ability_block(text, pet_label="x", warnings=[])
    assert (p, plv10, c, clv5) == (4, 3, 7, 6)


def test_parse_ability_block_missing_inner_value() -> None:
    """`(Lv10:)` with no inner number → lv_* stays None."""
    text = "Heal\nTurn Preparation: 5 (Lv10:)\nTurn Cooldown: 9"
    _, _, p, plv10, c, clv5 = _parse_ability_block(text, pet_label="x", warnings=[])
    assert (p, plv10) == (5, None)
    assert (c, clv5) == (9, None)


def test_parse_ability_block_christmas_dog_double_prep() -> None:
    """Double 'Turn Preparation' with no Cooldown line → second occurrence
    becomes cooldown, AND a warning is emitted."""
    text = (
        "Grant Self 30% Pdef/Mdef Up 1T\n"
        "\n"
        "\n"
        "Turn Preparation: 4 (Lv10: 3)\n"
        "Turn Preparation: 6 (Lv5: 5)"
    )
    warnings: list[str] = []
    _, _, p, plv10, c, clv5 = _parse_ability_block(
        text, pet_label="Christmas Dog", warnings=warnings,
    )
    assert (p, plv10) == (4, 3)
    assert (c, clv5) == (6, 5)
    assert any("Christmas Dog" in w for w in warnings)


def test_parse_ability_block_empty_text() -> None:
    eff, mb, p, plv10, c, clv5 = _parse_ability_block(
        "", pet_label="x", warnings=[],
    )
    assert eff == ""
    assert mb is None and p is None and plv10 is None
    assert c is None and clv5 is None


def test_parse_ability_block_prose_mention_does_not_match() -> None:
    """A prose mention of 'Turn Preparation' inside the effect must not
    leak into the structured prep field — the regex is anchored with
    MULTILINE on `^[ \\t]*Turn Preparation`. As long as the prose isn't
    at the beginning of a line, structured fields keep their values."""
    text = (
        "Effect describing the unit's Turn Preparation flow blah blah\n"
        "Max Boost: Lv3\n"
        "Turn Preparation: 2 (Lv10: 1)\n"
        "Turn Cooldown: 5 (Lv5: 4)"
    )
    eff, mb, p, plv10, c, clv5 = _parse_ability_block(
        text, pet_label="x", warnings=[],
    )
    assert (p, plv10, c, clv5) == (2, 1, 5, 4)
    assert mb == "Lv3"
    assert "describing" in eff


# --- end-to-end parse_pets --------------------------------------------------

def test_parse_pets_one_block() -> None:
    payload = _payload(_block(
        name="赤茶 (Red Brown Cat)",
        ability=(
            "Grant owner 30% Patk/Matk Up 1T\n"
            "\n"
            "\n"
            "Turn Preparation: 13 (Lv10: 12)\n"
            "Turn Cooldown: 13 (Lv5: 12)"
        ),
        source="Quest\n\nBeat the Titan Tower F3",
        hp="300", sp="11", patk="23", pdef="20",
        matk="24", mdef="22", crit="15", speed="15",
    ))
    pets, warnings = parse_pets(payload, gid=999)
    assert warnings == []
    assert len(pets) == 1
    p = pets[0]
    assert p.canonical_name == "Red Brown Cat"
    assert p.display_name_jp.startswith("赤茶")
    assert p.hp == 300 and p.sp == 11
    assert p.patk == 23 and p.pdef == 20
    assert p.matk == 24 and p.mdef == 22
    assert p.crit == 15 and p.speed == 15
    assert (p.prep_base, p.prep_lv10) == (13, 12)
    assert (p.cooldown_base, p.cooldown_lv5) == (13, 12)
    assert p.max_boost is None
    assert "Beat the Titan Tower F3" in (p.source_text or "")
    assert p.sheet_gid == 999
    assert p.source_row == 2  # two header rows above the first block
    assert p.hyperlink_url == "#gid=999&range=A3"


def test_parse_pets_multi_block_with_blank_row_between() -> None:
    """A stray blank row between blocks still resyncs."""
    payload = _payload(
        _block(
            "白 (White Cat)",
            "Recover owner's HP (130 power)\n\nMax Boost: Lv4\n"
            "Turn Preparation: 1 (Lv10: 0)\nTurn Cooldown: 5 (Lv5: 4)",
            "Quest",
        ),
        _block(
            "黒 (Black Cat)",
            "Grant Self Evade Phys Attack (1-2 hits)\n\nMax Boost: Lv2\n"
            "Turn Preparation: 2 (Lv10: 1)\nTurn Cooldown: 5 (Lv5: 4)",
            "Quest",
        ),
    )
    # Inject a blank row between the two blocks.
    rows = payload["sheets"][0]["data"][0]["rowData"]
    rows.insert(2 + 4, {"values": [{}]})

    pets, warnings = parse_pets(payload, gid=999)
    names = [p.canonical_name for p in pets]
    assert names == ["White Cat", "Black Cat"]
    assert warnings == []


def test_parse_pets_white_rabbit_collision_distinct_rows() -> None:
    payload = _payload(
        _block(
            "ウサギ 白 (White Rabbit)",
            "AoE Taunt 3-4T\n\nMax Boost: Lv2\n"
            "Turn Preparation: 1 (Lv10: 0)\nTurn Cooldown: 9 (Lv5: 8)",
            "Quest",
            hp="140",
        ),
        _block(
            "白 (White Rabbit)",
            "Grant owner Evade Magic (1-2 hits)\n\nMax Boost: Lv2\n"
            "Turn Preparation: 2 (Lv10: 1)\nTurn Cooldown: 5 (Lv5: 4)",
            "New Year 2023 Login (JP)",
            hp="120",
        ),
    )
    pets, warnings = parse_pets(payload, gid=999)
    assert [p.canonical_name for p in pets] == ["White Rabbit", "White Rabbit"]
    # Distinct source_rows → DB will treat them as different pets.
    assert pets[0].source_row != pets[1].source_row
    assert pets[0].hp == 140 and pets[1].hp == 120
    assert "Login" in (pets[1].source_text or "")
    assert warnings == []


def test_parse_pets_christmas_dog_emits_warning() -> None:
    payload = _payload(_block(
        "祝聖犬 白 (Christmas Dog)",
        "Grant Self 30% Pdef/Mdef Up 1T\n\n\n"
        "Turn Preparation: 4 (Lv10: 3)\n"
        "Turn Preparation: 6 (Lv5: 5)",
        "Exchange Shop",
        hp="310",
    ))
    pets, warnings = parse_pets(payload, gid=999)
    assert len(pets) == 1
    p = pets[0]
    assert p.canonical_name == "Christmas Dog"
    assert (p.prep_base, p.prep_lv10) == (4, 3)
    assert (p.cooldown_base, p.cooldown_lv5) == (6, 5)
    assert any("Christmas Dog" in w for w in warnings)


def test_parse_pets_thousands_separator_in_stat() -> None:
    payload = _payload(_block(
        "賢羊 金 (Big Sheep)",
        "ability text\nTurn Preparation: 1\nTurn Cooldown: 2",
        "Quest",
        hp="4,000",
    ))
    pets, _ = parse_pets(payload, gid=999)
    assert pets[0].hp == 4000


def test_parse_pets_skips_non_anchor_rows() -> None:
    """Header rows above the first block must not be misread as a pet."""
    payload = _payload(_block(
        "白 (White Cat)",
        "ability\nTurn Preparation: 1 (Lv10: 0)\nTurn Cooldown: 5 (Lv5: 4)",
        "Quest",
    ))
    # Insert a row that has a name in col 0 but no "HP" — must be skipped.
    rows = payload["sheets"][0]["data"][0]["rowData"]
    rows.insert(2, {"values": _row("Pet Name", "", "Stats")})

    pets, _ = parse_pets(payload, gid=999)
    assert [p.canonical_name for p in pets] == ["White Cat"]


def test_parse_pets_zero_gid_returns_empty() -> None:
    """Probe-not-yet-run state is non-fatal."""
    pets, warnings = parse_pets({"sheets": []}, gid=0)
    assert pets == []
    assert warnings == []


def test_parse_pets_missing_sheet_warns() -> None:
    pets, warnings = parse_pets({"sheets": []}, gid=12345)
    assert pets == []
    assert any("12345" in w for w in warnings)


def test_parse_pets_label_lookup_handles_shuffled_columns() -> None:
    """Stat values are read by *label*, so swapping column positions still works."""
    # Shuffle: name | HP | hpval | Patk | patk | SP | spval | Pdef | pdef | …
    name_row = [
        _cell("白 (White Cat)"),
        _cell("HP"), _cell("240"),
        _cell("Patk"), _cell("10"),
        _cell("SP"), _cell("19"),
        _cell("Pdef"), _cell("21"),
        _cell(
            "Recover owner's HP\n"
            "Max Boost: Lv4\n"
            "Turn Preparation: 1 (Lv10: 0)\n"
            "Turn Cooldown: 5 (Lv5: 4)"
        ),
        _cell("Quest"),
    ]
    rows = [
        {"values": _row("Header")},
        {"values": _row("")},
        {"values": name_row},
        {"values": _row("", "", "Matk", "14", "", "Mdef", "25")},
        {"values": _row("", "", "Crit", "26", "", "Speed", "22")},
        {"values": _row("")},
    ]
    payload = {
        "sheets": [{
            "properties": {"sheetId": 999, "title": "Pet List"},
            "data": [{"rowData": rows}],
        }],
    }
    pets, _ = parse_pets(payload, gid=999)
    assert len(pets) == 1
    p = pets[0]
    assert p.hp == 240 and p.sp == 19
    assert p.patk == 10 and p.pdef == 21
    assert p.matk == 14 and p.mdef == 25
    assert p.crit == 26 and p.speed == 22
