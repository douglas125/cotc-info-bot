"""Tests for the runner: Levenshtein, block selection, alias preference."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from config import TABS
from sync import runner as runner_mod
from sync.parsers import FormBlock, IndexEntry, SEA_GID, parse_sea_kits
from sync.runner import _levenshtein, _select_block_for, _variant_kind_for


# --- Levenshtein ------------------------------------------------------------

@pytest.mark.parametrize("a,b,d", [
    ("", "", 0),
    ("abc", "abc", 0),
    ("abc", "", 3),
    ("Fior", "Fiore", 1),         # 1 insertion
    ("Krauser", "Clauser", 2),    # 2 substitutions: K→C, r→l
    ("Erika", "Elrica", 2),       # 1 insertion + 1 substitution
    ("kitten", "sitting", 3),     # textbook
])
def test_levenshtein_distances(a: str, b: str, d: int) -> None:
    assert _levenshtein(a, b) == d


def test_levenshtein_is_case_insensitive() -> None:
    assert _levenshtein("Fior", "fiore") == 1
    assert _levenshtein("FIOR", "fiore") == 1


# --- variant kind classifier ------------------------------------------------

@pytest.mark.parametrize("name,expected", [
    ("Cyrus", "base"),
    ("EX Cyrus", "ex"),
    ("EX2 Erika", "ex2"),
    ("Saint of Twin Blazes", "alt"),
    ("Black Maiden (Alt)", "alt"),
    ("Lynette EX", "ex"),
    ("Castti EX2", "ex2"),
    ("Lynette ex", "ex"),
    (" Lynette EX ", "ex"),
])
def test_variant_kind_for(name: str, expected: str) -> None:
    assert _variant_kind_for(name) == expected


# --- _select_block_for ------------------------------------------------------

def _make_entry(name: str, role: str = "warrior", rarity: str = "5*") -> IndexEntry:
    return IndexEntry(
        canonical_name=name, role=role, weapon="sword",
        rarity=rarity, color_hex="#CC0000", color_family="red",
        hyperlink_url=None, anchor=None, source_row=0,
    )


def _make_block(name: str, gid: int = 519845584) -> FormBlock:
    return FormBlock(display_name=name, sheet_gid=gid, source_row=0)


def test_select_block_exact_match_in_right_band() -> None:
    entry = _make_entry("Cyrus", role="scholar")
    cand_5star = _make_block("Cyrus", gid=284157275)  # Scholars ⭐5
    block = _select_block_for(entry, [(284157275, cand_5star)], {})
    assert block is cand_5star


def test_select_block_falls_back_to_fuzzy_when_no_candidates() -> None:
    """Fior on the role tab, Fiore in the Index — distance 1."""
    entry = _make_entry("Fiore", role="warrior", rarity="5*")
    fior_block = _make_block("Fior")
    blocks_by_tab = {519845584: [fior_block]}  # Warriors ⭐5
    block = _select_block_for(entry, [], blocks_by_tab)
    assert block is fior_block


def test_select_block_fuzzy_rejects_far_matches() -> None:
    """Distance > 2 should NOT trigger a fuzzy match (avoid false positives)."""
    entry = _make_entry("Cyrus", role="warrior", rarity="5*")
    far_block = _make_block("Aedelgard")  # very different
    blocks_by_tab = {519845584: [far_block]}
    block = _select_block_for(entry, [], blocks_by_tab)
    assert block is None


def test_select_block_fuzzy_only_searches_correct_tab() -> None:
    """A 'Cyrus' Index entry on the Scholar tab must NOT match a 'Cyrn' block
    that lives on the Warrior tab (different role)."""
    entry = _make_entry("Cyrus", role="scholar", rarity="5*")
    bad_block = _make_block("Cyrn")  # close to 'Cyrus' but on wrong tab
    blocks_by_tab = {519845584: [bad_block]}  # Warriors ⭐5, not Scholars
    block = _select_block_for(entry, [], blocks_by_tab)
    assert block is None


def test_select_block_prefers_correct_rarity_band() -> None:
    """When the same name exists on both ⭐5 and 3&4 tabs, pick the matching band."""
    entry = _make_entry("Cyrus", role="scholar", rarity="5*")
    block5 = _make_block("Cyrus", gid=284157275)
    block34 = _make_block("Cyrus", gid=203210803)
    chosen = _select_block_for(
        entry,
        [(284157275, block5), (203210803, block34)],
        {},
    )
    assert chosen is block5


# --- parse_sea_kits ---------------------------------------------------------

def _cell(text: str = "") -> dict:
    return {"formattedValue": text} if text else {}


def _sea_synthetic_sheet(name: str, skill_desc: str) -> dict:
    """Single-block SEA tab in the same column layout as a role tab."""
    rows = [
        [_cell()] * 8,  # filler
        # block start: name in col 0, 'SP' in col 6, 'Active' in col 7
        [_cell(name)] + [_cell()] * 5 + [_cell("SP"), _cell("Active")],
        # one skill row: SP=integer in col 6, description in col 7
        [_cell()] * 6 + [_cell("18"), _cell(skill_desc)],
    ]
    return {"data": [{"rowData": [{"values": r} for r in rows]}]}


def test_parse_sea_kits_returns_form_blocks_not_just_names() -> None:
    """parse_sea_kits replaces the old name-only parser; it must yield full blocks."""
    sheet = _sea_synthetic_sheet("Castti", "SEA-only kit description")
    blocks = parse_sea_kits(sheet)
    assert len(blocks) == 1
    assert isinstance(blocks[0], FormBlock)
    assert blocks[0].display_name == "Castti"
    assert blocks[0].sheet_gid == SEA_GID
    assert len(blocks[0].skills) == 1
    assert blocks[0].skills[0]["description"] == "SEA-only kit description"


# --- end-to-end SEA precedence ---------------------------------------------

INDEX_GID = 1917707422
APOTH_5_GID = 1672823319
WARRIORS_5_GID = 519845584
SCHOLARS_5_GID = 284157275


def _idx_cell(text: str = "", *, color: dict | None = None,
              hyperlink: str | None = None) -> dict:
    out: dict = {}
    if text:
        out["formattedValue"] = text
    if color:
        out["effectiveFormat"] = {"textFormat": {"foregroundColor": color}}
    if hyperlink:
        out["hyperlink"] = hyperlink
    return out


def _sheet(gid: int, title: str, rows: list[list[dict]]) -> dict:
    return {
        "properties": {"sheetId": gid, "title": title},
        "data": [{"rowData": [{"values": r} for r in rows]}],
    }


def _block_rows(name: str, skill_desc: str) -> list[list[dict]]:
    return [
        [_idx_cell()] * 8,
        [_idx_cell(name)] + [_idx_cell()] * 5
            + [_idx_cell("SP"), _idx_cell("Active")],
        [_idx_cell()] * 6 + [_idx_cell("18"), _idx_cell(skill_desc)],
    ]


def _index_sheet_with(*characters: tuple[str, str]) -> dict:
    """Build an Index sheet with given characters slotted into role columns.

    Each (name, role) tuple is placed in the role's column. Roles supported:
    'apothecary', 'warrior'. Both 5★ (red).
    """
    role_names = ["Warrior (Sword)", "Merchant (Spear)", "Thief (Dagger)",
                  "Apothecary (Axe)", "Hunter (Bow)", "Cleric (Staff)",
                  "Scholar (Tome)", "Dancer (Fan)"]
    width = len(role_names) * 11
    header = [_idx_cell()] * width
    for i, label in enumerate(role_names):
        header[i * 11 + 1] = _idx_cell(label)
    cols_by_role = {
        "warrior":    0 * 11 + 1,
        "apothecary": 3 * 11 + 1,
    }
    char_row = [_idx_cell()] * width
    for name, role in characters:
        col = cols_by_role[role]
        char_row[col] = _idx_cell(
            name, color={"red": 0.8},
            hyperlink=f"http://example/{name.lower()}",
        )
    return _sheet(INDEX_GID, "Characters Index", [header, char_row])


def _build_payload_with_overlap() -> dict:
    """Castti is in BOTH the apothecary role tab AND the SEA/GL Unique Kits tab.
    Therion is only in the warrior role tab (no SEA entry)."""
    sheets = [
        _index_sheet_with(("Castti", "apothecary"), ("Therion", "warrior")),
        _sheet(APOTH_5_GID, "Apothecaries 5",
               _block_rows("Castti", "ROLE_TAB_KIT_DESCRIPTION")),
        _sheet(SEA_GID, "SEA/GL Unique Kits",
               _block_rows("Castti", "SEA_KIT_DESCRIPTION")),
        _sheet(WARRIORS_5_GID, "Warriors 5",
               _block_rows("Therion", "WARRIOR_ROLE_TAB_KIT")),
    ]
    # Pad with empty placeholder sheets for every other tab so
    # sheet_by_gid never returns None during the run.
    used = {s["properties"]["sheetId"] for s in sheets}
    for tab in TABS:
        if tab.gid not in used:
            sheets.append(_sheet(tab.gid, tab.name, [[_idx_cell()]]))
    return {"sheets": sheets}


def test_run_sync_sea_kit_takes_precedence_and_emits_one_form(
    tmp_db_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for the 'duplicated Castti / Nephti' bug.

    Castti appears in both the Apothecary role tab and the SEA/GL Unique
    Kits tab. After sync we expect exactly one form per character and the
    SEA tab's skills (not the role tab's) to win.
    """
    payload = _build_payload_with_overlap()
    monkeypatch.setattr(runner_mod, "fetch_spreadsheet", lambda api_key, *_: payload)
    monkeypatch.setattr("db.repo.DB_PATH", tmp_db_path)

    summary = runner_mod.run_sync("dummy-key")
    assert summary["status"] == "ok"

    conn = sqlite3.connect(tmp_db_path)
    conn.row_factory = sqlite3.Row
    try:
        # 1. ONE info set per character — no duplicate forms for Castti.
        cid = conn.execute(
            "SELECT id FROM characters WHERE canonical_name = 'Castti'"
        ).fetchone()["id"]
        forms = conn.execute(
            "SELECT id, server FROM character_forms WHERE character_id = ?",
            (cid,),
        ).fetchall()
        assert len(forms) == 1, \
            f"expected 1 Castti form, got {len(forms)}: {[dict(r) for r in forms]}"
        assert forms[0]["server"] == "global"

        # 2. No 'sea' duplicates anywhere in the DB.
        n_sea = conn.execute(
            "SELECT COUNT(*) FROM character_forms WHERE server = 'sea'"
        ).fetchone()[0]
        assert n_sea == 0, f"unexpected server='sea' rows: {n_sea}"

        # 3. SEA tab takes precedence: Castti's skills come from the SEA block.
        descs = [r["description"] for r in conn.execute(
            "SELECT description FROM skills WHERE form_id = ? ORDER BY slot_order",
            (forms[0]["id"],),
        )]
        assert any("SEA_KIT_DESCRIPTION" in (d or "") for d in descs), \
            f"expected SEA_KIT_DESCRIPTION; got {descs!r}"
        assert not any("ROLE_TAB_KIT_DESCRIPTION" in (d or "") for d in descs), \
            f"role-tab kit leaked through despite SEA override: {descs!r}"

        # 4. Therion (no SEA entry) still gets the role-tab kit — fallback path
        #    is intact for characters not in the SEA tab.
        therion_descs = [r["description"] for r in conn.execute(
            "SELECT s.description FROM skills s "
            "JOIN character_forms cf ON cf.id = s.form_id "
            "JOIN characters c ON c.id = cf.character_id "
            "WHERE c.canonical_name = 'Therion'"
        )]
        assert any("WARRIOR_ROLE_TAB_KIT" in (d or "") for d in therion_descs), \
            f"non-SEA character lost role-tab kit: {therion_descs!r}"
    finally:
        conn.close()


def _build_payload_with_sea_only_ex() -> dict:
    """Lynette in the Index + warrior role tab, Lynette EX only in the SEA tab."""
    sheets = [
        _index_sheet_with(("Lynette", "warrior")),
        _sheet(WARRIORS_5_GID, "Warriors 5",
               _block_rows("Lynette", "BASE_KIT")),
        _sheet(SEA_GID, "SEA/GL Unique Kits",
               _block_rows("Lynette EX", "EX_KIT_FROM_SEA: 1x single-target Dagger")),
    ]
    used = {s["properties"]["sheetId"] for s in sheets}
    for tab in TABS:
        if tab.gid not in used:
            sheets.append(_sheet(tab.gid, tab.name, [[_idx_cell()]]))
    return {"sheets": sheets}


def test_run_sync_creates_form_for_sea_only_block(
    tmp_db_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SEA-only EX variants (e.g. Lynette EX) must surface as their own form
    even though they aren't listed in the Characters Index."""
    payload = _build_payload_with_sea_only_ex()
    monkeypatch.setattr(runner_mod, "fetch_spreadsheet", lambda api_key, *_: payload)
    monkeypatch.setattr("db.repo.DB_PATH", tmp_db_path)

    summary = runner_mod.run_sync("dummy-key")
    assert summary["status"] == "ok"

    conn = sqlite3.connect(tmp_db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT f.id, f.display_name, f.rarity, f.variant_kind, f.server, "
            "       f.sheet_gid, c.base_role, c.base_weapon "
            "FROM character_forms f "
            "JOIN characters c ON c.id = f.character_id "
            "WHERE f.display_name = 'Lynette EX'"
        ).fetchone()
        assert row is not None, "Lynette EX should have been created from the SEA tab"
        assert row["rarity"] == "5*"
        assert row["variant_kind"] == "ex"
        assert row["server"] == "sea"
        assert row["sheet_gid"] == SEA_GID
        # Role/weapon inferred from the SEA-only skill text, not inherited
        # from the base 'Lynette' character.
        assert row["base_role"] == "thief"
        assert row["base_weapon"] == "dagger"

        descs = [r["description"] for r in conn.execute(
            "SELECT description FROM skills WHERE form_id = ?", (row["id"],),
        )]
        assert any("EX_KIT_FROM_SEA" in (d or "") for d in descs), \
            f"SEA-only kit didn't make it onto the new form: {descs!r}"

        # Base 'Lynette' still exists with its role-tab kit untouched.
        base_descs = [r["description"] for r in conn.execute(
            "SELECT s.description FROM skills s "
            "JOIN character_forms cf ON cf.id = s.form_id "
            "WHERE cf.display_name = 'Lynette'"
        )]
        assert any("BASE_KIT" in (d or "") for d in base_descs)
    finally:
        conn.close()


def _build_payload_with_aliased_ex(
    index_name: str, role_tab_name: str,
) -> dict:
    """Index uses the canonical (e.g. 'EX Araune'), role tab uses an aliased
    spelling possibly with the EX marker in the opposite word order
    (e.g. 'Alaune EX'). The sync runner must merge both into one form."""
    sheets = [
        _index_sheet_with((index_name, "warrior")),
        _sheet(WARRIORS_5_GID, "Warriors 5",
               _block_rows(role_tab_name, "ALIASED_EX_KIT: 1x single-target Sword")),
    ]
    used = {s["properties"]["sheetId"] for s in sheets}
    for tab in TABS:
        if tab.gid not in used:
            sheets.append(_sheet(tab.gid, tab.name, [[_idx_cell()]]))
    return {"sheets": sheets}


@pytest.mark.parametrize("index_name,role_tab_name", [
    ("EX Araune",  "Alaune EX"),     # prefix Index ↔ suffix role-tab + alias
    ("Araune EX",  "EX Alaune"),     # suffix Index ↔ prefix role-tab + alias
    ("EX Araune",  "EX Alaune"),     # same word order, alias only
    ("Erika EX2",  "EX2 Elrica"),    # EX2 + word-order swap + alias
])
def test_run_sync_aliases_ex_role_tab_to_index_entry(
    tmp_db_path: Path, monkeypatch: pytest.MonkeyPatch,
    index_name: str, role_tab_name: str,
) -> None:
    """Regression: NAME_ALIASES must apply to EX/EX2 forms, regardless of
    whether the marker comes before or after the bare name."""
    payload = _build_payload_with_aliased_ex(index_name, role_tab_name)
    monkeypatch.setattr(runner_mod, "fetch_spreadsheet", lambda api_key, *_: payload)
    monkeypatch.setattr("db.repo.DB_PATH", tmp_db_path)

    summary = runner_mod.run_sync("dummy-key")
    assert summary["status"] == "ok"

    conn = sqlite3.connect(tmp_db_path)
    conn.row_factory = sqlite3.Row
    try:
        # Exactly one form, named after the Index canonical (NOT the role-tab spelling).
        forms = conn.execute(
            "SELECT id, display_name FROM character_forms "
            "WHERE LOWER(display_name) IN (?, ?)",
            (index_name.lower(), role_tab_name.lower()),
        ).fetchall()
        assert len(forms) == 1, \
            f"expected 1 merged form for {index_name!r} ↔ {role_tab_name!r}, " \
            f"got {[dict(r) for r in forms]}"
        assert forms[0]["display_name"] == index_name

        # The role-tab kit must be attached to that form (not silently dropped).
        descs = [r["description"] for r in conn.execute(
            "SELECT description FROM skills WHERE form_id = ?", (forms[0]["id"],),
        )]
        assert any("ALIASED_EX_KIT" in (d or "") for d in descs), \
            f"role-tab kit lost during merge: {descs!r}"
    finally:
        conn.close()


def _build_payload_with_role_tab_only_ex() -> dict:
    """EX Temenos is present as a complete role-tab block but not in Index."""
    sheets = [
        _index_sheet_with(("Lynette", "warrior")),
        _sheet(WARRIORS_5_GID, "Warriors 5",
               _block_rows("Lynette", "BASE_KIT")),
        _sheet(SCHOLARS_5_GID, "Scholars 5",
               _block_rows("EX Temenos", "ROLE_TAB_EX_KIT: 1x single-target Tome")),
    ]
    used = {s["properties"]["sheetId"] for s in sheets}
    for tab in TABS:
        if tab.gid not in used:
            sheets.append(_sheet(tab.gid, tab.name, [[_idx_cell()]]))
    return {"sheets": sheets}


def test_run_sync_creates_form_for_role_tab_only_ex_block(
    tmp_db_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _build_payload_with_role_tab_only_ex()
    monkeypatch.setattr(runner_mod, "fetch_spreadsheet", lambda api_key, *_: payload)
    monkeypatch.setattr("db.repo.DB_PATH", tmp_db_path)

    summary = runner_mod.run_sync("dummy-key")
    assert summary["status"] == "ok"

    conn = sqlite3.connect(tmp_db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT f.id, f.display_name, f.rarity, f.variant_kind, f.server, "
            "       f.sheet_gid, c.base_role, c.base_weapon "
            "FROM character_forms f "
            "JOIN characters c ON c.id = f.character_id "
            "WHERE f.display_name = 'EX Temenos'"
        ).fetchone()
        assert row is not None
        assert row["rarity"] == "5*"
        assert row["variant_kind"] == "ex"
        assert row["server"] == "global"
        assert row["sheet_gid"] == SCHOLARS_5_GID
        assert row["base_role"] == "scholar"
        assert row["base_weapon"] == "tome"

        descs = [r["description"] for r in conn.execute(
            "SELECT description FROM skills WHERE form_id = ?", (row["id"],),
        )]
        assert any("ROLE_TAB_EX_KIT" in (d or "") for d in descs)
    finally:
        conn.close()
