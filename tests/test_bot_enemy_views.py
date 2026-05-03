"""Tests for bot/enemy_views.py — the /enemy dropdown wrapper."""
from __future__ import annotations

import pytest

discord = pytest.importorskip("discord", reason="discord.py not installed")

from bot.enemy_views import EnemyView, _RankSelect  # noqa: E402


def test_view_with_multiple_ranks_attaches_select() -> None:
    view = EnemyView(
        enemy_id=42,
        available_ranks=["Rank1", "EX3"],
        current_rank="EX3",
    )
    selects = [c for c in view.children if isinstance(c, discord.ui.Select)]
    assert len(selects) == 1
    sel = selects[0]
    values = [o.value for o in sel.options]
    assert values == ["rank:EX3", "rank:Rank1"]
    default = next(o for o in sel.options if o.default)
    assert default.value == "rank:EX3"


def test_view_with_single_rank_npc_has_no_select() -> None:
    """NPCs (single 'Default' form) should render without a dropdown."""
    view = EnemyView(
        enemy_id=99,
        available_ranks=["Default"],
        current_rank="Default",
    )
    selects = [c for c in view.children if isinstance(c, discord.ui.Select)]
    assert selects == []


def test_select_options_use_friendly_labels() -> None:
    sel = _RankSelect(available=["EX3", "EX1", "Rank1"], current="Rank1")
    by_value = {o.value: o.label for o in sel.options}
    assert by_value["rank:Rank1"] == "Rank 1"
    assert by_value["rank:EX1"] == "EX 1"
    assert by_value["rank:EX3"] == "EX 3"


def test_view_timeout_is_set() -> None:
    view = EnemyView(enemy_id=1, available_ranks=["EX3"], current_rank="EX3")
    assert view.timeout == 180
    view = EnemyView(enemy_id=1, available_ranks=["Rank1", "EX3"], current_rank="EX3")
    assert view.timeout == 180


def test_view_remembers_enemy_id() -> None:
    view = EnemyView(enemy_id=12345, available_ranks=["EX3"], current_rank="EX3")
    assert view.enemy_id == 12345


def test_view_with_fight_notes_adds_notes_option() -> None:
    view = EnemyView(
        enemy_id=42,
        available_ranks=["EX3"],
        current_rank="EX3",
        has_fight_notes=True,
    )
    selects = [c for c in view.children if isinstance(c, discord.ui.Select)]
    assert len(selects) == 1
    values = [o.value for o in selects[0].options]
    assert values == ["rank:EX3", "notes"]
