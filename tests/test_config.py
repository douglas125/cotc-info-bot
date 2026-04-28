"""Sanity-check the static config tables don't drift out of sync."""
from __future__ import annotations

from config import ROLE_TABS, TABS, TABS_BY_GID, WEAPON_TO_ROLE


def test_tabs_inventory_count() -> None:
    """Sheet has exactly 19 tabs as of this writing."""
    assert len(TABS) == 19


def test_role_tabs_count_and_kinds() -> None:
    """Eight roles × two rarity bands = 16 role tabs."""
    assert len(ROLE_TABS) == 16
    bands = {t.rarity_band for t in ROLE_TABS}
    assert bands == {"5*", "34"}
    roles = {t.role for t in ROLE_TABS}
    assert roles == {"warrior", "merchant", "thief", "apothecary",
                     "hunter", "cleric", "scholar", "dancer"}


def test_gids_are_unique() -> None:
    gids = [t.gid for t in TABS]
    assert len(gids) == len(set(gids)), "duplicate gid in TABS"


def test_role_tabs_have_role_and_weapon() -> None:
    for t in ROLE_TABS:
        assert t.role is not None
        assert t.weapon is not None
        assert t.rarity_band in ("5*", "34")


def test_weapon_to_role_mapping() -> None:
    assert WEAPON_TO_ROLE == {
        "sword": "warrior",
        "spear": "merchant",
        "dagger": "thief",
        "axe": "apothecary",
        "bow": "hunter",
        "staff": "cleric",
        "tome": "scholar",
        "fan": "dancer",
    }


def test_tabs_by_gid_is_complete() -> None:
    for t in TABS:
        assert TABS_BY_GID[t.gid] is t
