"""Smoke tests asserting that ``bot.commands.register`` actually attaches
the slash commands users expect. Catches accidental regressions where a
new command stops being wired into the tree.
"""
from __future__ import annotations

import discord
from discord import app_commands

from bot import commands


def _registered_names() -> set[str]:
    client = discord.Client(intents=discord.Intents.none())
    tree = app_commands.CommandTree(client)
    commands.register(tree)
    return {c.name for c in tree.get_commands()}


def test_analyze_team_is_registered() -> None:
    assert "analyze_team" in _registered_names()


def test_core_commands_still_registered() -> None:
    expected = {
        "character", "enemy", "pet", "search",
        "refresh", "feedback", "feedback_list", "feedback_clear",
        "analyze_team",
    }
    assert expected.issubset(_registered_names())
