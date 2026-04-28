"""Autocomplete-helper tests: prefix matching, ≤25 cap, fallback resolution."""
from __future__ import annotations

from pathlib import Path

import pytest

from db import repo

pytest.importorskip(
    "discord",
    reason="discord.py not installed (run conda env update -f environment.yml --prune)",
)

from bot import commands as bot_commands  # noqa: E402


def _seed_many(conn, n: int = 30) -> None:
    for i in range(n):
        ch = repo.upsert_character(conn, canonical_name=f"Char{i:02d}",
                                    base_role="scholar", base_weapon="tome")
        repo.insert_form(conn, character_id=ch, display_name=f"Char{i:02d}",
                          rarity="5*" if i % 2 else "4*")
    # Add a couple of distinctive names for substring testing.
    ch_a = repo.upsert_character(conn, canonical_name="Cyrus",
                                  base_role="scholar", base_weapon="tome")
    repo.insert_form(conn, character_id=ch_a, display_name="Cyrus", rarity="5*")
    ch_b = repo.upsert_character(conn, canonical_name="Cynthia",
                                  base_role="cleric", base_weapon="staff")
    repo.insert_form(conn, character_id=ch_b, display_name="Cynthia", rarity="5*")


def test_autocomplete_caps_at_25(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    _seed_many(conn, n=40)
    choices = bot_commands._autocomplete_forms(conn, current="")
    conn.close()
    assert len(choices) <= bot_commands.AUTOCOMPLETE_LIMIT
    assert len(choices) == bot_commands.AUTOCOMPLETE_LIMIT  # plenty seeded


def test_autocomplete_prefix_match_first(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    _seed_many(conn)
    choices = bot_commands._autocomplete_forms(conn, current="Cy")
    conn.close()
    # Both Cyrus and Cynthia are prefix matches; both should be in the list.
    names = [c.name for c in choices]
    assert any("Cyrus" in n for n in names)
    assert any("Cynthia" in n for n in names)


def test_autocomplete_substring_fallback(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    _seed_many(conn)
    # 'rus' isn't a prefix of any name, but is a substring of 'Cyrus'.
    choices = bot_commands._autocomplete_forms(conn, current="rus")
    conn.close()
    assert any("Cyrus" in c.name for c in choices)


def test_autocomplete_choice_value_is_form_id(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    _seed_many(conn)
    choices = bot_commands._autocomplete_forms(conn, current="Cyrus")
    conn.close()
    assert choices, "expected at least one match for 'Cyrus'"
    for c in choices:
        # value must be a stringified int (form_id)
        assert int(c.value) > 0


def test_resolve_form_id_from_picked_choice(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    _seed_many(conn, n=5)
    choices = bot_commands._autocomplete_forms(conn, current="Char")
    picked = choices[0].value
    resolved = bot_commands._resolve_form_id(conn, picked)
    conn.close()
    assert resolved == int(picked)


def test_resolve_form_id_from_typed_name(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    _seed_many(conn)
    # User types the name and presses enter without picking from the list.
    resolved = bot_commands._resolve_form_id(conn, "cyrus")
    assert resolved is not None
    form = repo.get_form(conn, resolved)
    assert form is not None and form["display_name"] == "Cyrus"
    conn.close()


def test_resolve_form_id_returns_none_for_unknown(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    _seed_many(conn)
    assert bot_commands._resolve_form_id(conn, "Definitely Not A Character") is None
    assert bot_commands._resolve_form_id(conn, "") is None
    conn.close()


def _seed_ex_pair(conn) -> tuple[int, int]:
    """Seed an Index-style 'EX Castti' (prefix) and SEA-style 'Lynette EX' (suffix)."""
    ch_a = repo.upsert_character(conn, canonical_name="EX Castti",
                                  base_role="apothecary", base_weapon="axe")
    fid_a = repo.insert_form(conn, character_id=ch_a, display_name="EX Castti",
                              rarity="5*", variant_kind="ex")
    ch_b = repo.upsert_character(conn, canonical_name="Lynette EX",
                                  base_role="dancer", base_weapon="fan")
    fid_b = repo.insert_form(conn, character_id=ch_b, display_name="Lynette EX",
                              rarity="5*", variant_kind="ex", server="sea")
    return fid_a, fid_b


def test_ex_swap_variants() -> None:
    assert bot_commands._ex_swap_variants("Castti EX") == ["Castti EX", "EX Castti"]
    assert bot_commands._ex_swap_variants("EX Castti") == ["EX Castti", "Castti EX"]
    assert bot_commands._ex_swap_variants("Erika EX2") == ["Erika EX2", "EX2 Erika"]
    assert bot_commands._ex_swap_variants("EX2 Erika") == ["EX2 Erika", "Erika EX2"]
    # No EX token → no swap.
    assert bot_commands._ex_swap_variants("Cyrus") == ["Cyrus"]
    assert bot_commands._ex_swap_variants("") == []
    assert bot_commands._ex_swap_variants("   ") == []


def test_autocomplete_swaps_ex_position(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    fid_castti, fid_lynette = _seed_ex_pair(conn)

    # Typed suffix → matches stored prefix-form 'EX Castti'.
    by_castti_suffix = bot_commands._autocomplete_forms(conn, current="Castti EX")
    assert any(int(c.value) == fid_castti for c in by_castti_suffix), \
        f"'Castti EX' should match stored 'EX Castti': {[c.name for c in by_castti_suffix]}"

    # Typed prefix → matches stored suffix-form 'Lynette EX'.
    by_lynette_prefix = bot_commands._autocomplete_forms(conn, current="EX Lynette")
    assert any(int(c.value) == fid_lynette for c in by_lynette_prefix), \
        f"'EX Lynette' should match stored 'Lynette EX': {[c.name for c in by_lynette_prefix]}"

    conn.close()


def test_resolve_form_id_swaps_ex_position(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    fid_castti, fid_lynette = _seed_ex_pair(conn)

    assert bot_commands._resolve_form_id(conn, "Castti EX") == fid_castti
    assert bot_commands._resolve_form_id(conn, "EX Lynette") == fid_lynette
    # Original spellings still resolve directly.
    assert bot_commands._resolve_form_id(conn, "EX Castti") == fid_castti
    assert bot_commands._resolve_form_id(conn, "Lynette EX") == fid_lynette
    conn.close()


def _seed_alias_target(conn) -> int:
    ch = repo.upsert_character(conn, canonical_name="Clauser",
                                base_role="merchant", base_weapon="spear")
    return repo.insert_form(conn, character_id=ch, display_name="Clauser", rarity="5*")


def test_resolve_form_id_via_alias(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    fid = _seed_alias_target(conn)
    assert bot_commands._resolve_form_id(conn, "Krauser") == fid
    conn.close()


def test_resolve_form_id_via_alias_case_insensitive(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    fid = _seed_alias_target(conn)
    assert bot_commands._resolve_form_id(conn, "krauser") == fid
    assert bot_commands._resolve_form_id(conn, "KRAUSER") == fid
    conn.close()


def test_autocomplete_surfaces_alias(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    fid = _seed_alias_target(conn)
    choices = bot_commands._autocomplete_forms(conn, current="Krau")
    conn.close()
    matches = [c for c in choices if int(c.value) == fid]
    assert matches, f"expected Clauser via alias 'Krauser' for prefix 'Krau': {[c.name for c in choices]}"
    assert "Clauser" in matches[0].name
    assert "Krauser" in matches[0].name


def test_filter_choices_caps_and_filters() -> None:
    values = [f"role-{i:02d}" for i in range(40)]
    out = bot_commands._filter_choices(values, current="")
    assert len(out) == bot_commands.AUTOCOMPLETE_LIMIT

    out2 = bot_commands._filter_choices(values, current="role-03")
    assert len(out2) == 1
    assert out2[0].value == "role-03"
