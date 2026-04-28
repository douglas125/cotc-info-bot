from __future__ import annotations

from bot import weakness_icons


def _labels(text: str) -> list[str]:
    return [
        token.label
        for token in weakness_icons.inline_tokens(text)
        if isinstance(token, weakness_icons.IconToken)
    ]


def test_inline_tokens_replace_labels_case_insensitively() -> None:
    assert _labels("tome/Tome/FIRE/ice") == ["Tome", "Tome", "Fire", "Ice"]


def test_inline_tokens_treat_polearm_as_spear_icon() -> None:
    assert _labels("Polearm and spear") == ["Polearm", "Spear"]
    assert weakness_icons.ICON_FILES["Polearm"] == weakness_icons.ICON_FILES["Spear"]


def test_inline_tokens_do_not_match_inside_words() -> None:
    assert _labels("firelight Lightning") == ["Lightning"]
