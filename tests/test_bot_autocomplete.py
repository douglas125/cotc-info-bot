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


def test_filter_choices_caps_and_filters() -> None:
    values = [f"role-{i:02d}" for i in range(40)]
    out = bot_commands._filter_choices(values, current="")
    assert len(out) == bot_commands.AUTOCOMPLETE_LIMIT

    out2 = bot_commands._filter_choices(values, current="role-03")
    assert len(out2) == 1
    assert out2[0].value == "role-03"
