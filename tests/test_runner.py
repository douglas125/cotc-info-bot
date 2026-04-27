"""Tests for the runner: Levenshtein, block selection, alias preference."""
from __future__ import annotations

import pytest

from sync.runner import _levenshtein, _select_block_for, _variant_kind_for
from sync.parsers import FormBlock, IndexEntry


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
