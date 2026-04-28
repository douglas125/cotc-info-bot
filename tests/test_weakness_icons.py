from __future__ import annotations

from bot import weakness_icons


def test_canonical_labels_are_case_insensitive() -> None:
    assert weakness_icons.canonical_label("tome") == "Tome"
    assert weakness_icons.canonical_label("FIRE") == "Fire"
    assert weakness_icons.canonical_label("not-a-weakness") is None


def test_polearm_uses_spear_icon_asset() -> None:
    assert weakness_icons.ICON_FILES["Polearm"] == weakness_icons.ICON_FILES["Spear"]


def test_load_icon_returns_requested_size() -> None:
    icon = weakness_icons.load_icon("fire", size=20)
    assert icon is not None
    assert icon.size == (20, 20)
