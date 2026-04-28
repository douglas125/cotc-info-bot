"""Tests for bot/views.py — the /character dropdown wrapper."""
from __future__ import annotations

import pytest

discord = pytest.importorskip(
    "discord", reason="discord.py not installed",
)

from bot import embeds  # noqa: E402
from bot.views import CharacterView, _SectionSelect  # noqa: E402


def test_character_view_default_section() -> None:
    view = CharacterView(form_id=42)
    assert view.form_id == 42
    selects = [c for c in view.children if isinstance(c, discord.ui.Select)]
    assert len(selects) == 1
    sel = selects[0]
    values = [o.value for o in sel.options]
    assert values == list(embeds.SECTIONS)
    default_opt = next(o for o in sel.options if o.default)
    assert default_opt.value == embeds.DEFAULT_SECTION


def test_character_view_custom_section() -> None:
    view = CharacterView(form_id=42, section="info")
    sel = next(c for c in view.children if isinstance(c, discord.ui.Select))
    info_opt = next(o for o in sel.options if o.value == "info")
    assert info_opt.default is True
    actives_opt = next(o for o in sel.options if o.value == "actives")
    assert actives_opt.default is False


def test_section_select_options_match_labels() -> None:
    sel = _SectionSelect(current="actives")
    by_value = {o.value: o for o in sel.options}
    for s in embeds.SECTIONS:
        assert by_value[s].label == embeds.SECTION_LABELS[s]
        assert by_value[s].description == embeds.SECTION_DESCRIPTIONS[s]


def test_view_timeout_is_set() -> None:
    view = CharacterView(form_id=1)
    assert view.timeout == 180
