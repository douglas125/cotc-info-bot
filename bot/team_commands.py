"""Dormant ``/analyze_team`` slash command.

Phase 1 ships the command body as a private helper but DOES NOT register
it on the bot's command tree. Tests call ``build_team_report`` directly.
When the audit clears, lift the dormancy by calling :func:`register`
from ``bot/commands.py::register`` and uncommenting the
``@tree.command`` decorator block at the bottom of this module.

Slash params (when activated): ``frontrow1..frontrow4`` and
``backrow1..backrow4`` (all optional; users can fill 1-8 slots),
plus ``pet``, ``divine_beast``, ``cap_orbs``, ``dps``, ``boost_level``.
All 8 members contribute equally to the offensive bucket math and
the survivability verdict — passives fire from either row in CotC
unless explicitly row-gated by the skill text.
"""
from __future__ import annotations

import sqlite3
from typing import Iterable

import discord
from discord import app_commands

from analysis import aggregator, coverage, damage_estimate, survivability
from analysis.types import AssumptionProfile, TeamReport
from bot import team_embeds


# Slash-command surface as it WILL be exposed once dormancy is lifted.
BOOST_CHOICES: list[app_commands.Choice[str]] = [
    app_commands.Choice(name="0", value="0"),
    app_commands.Choice(name="1", value="1"),
    app_commands.Choice(name="2", value="2"),
    app_commands.Choice(name="MAX", value="3"),
]
CAP_ORB_CHOICES: list[app_commands.Choice[int]] = [
    app_commands.Choice(name="0", value=0),
    app_commands.Choice(name="1", value=1),
    app_commands.Choice(name="2", value=2),
    app_commands.Choice(name="3", value=3),
]


def build_team_report(
    conn: sqlite3.Connection,
    *,
    frontrow_form_ids: Iterable[int],
    backrow_form_ids: Iterable[int] = (),
    pet_id: int | None = None,
    divine_beast: bool = False,
    cap_orbs: int = 0,
    boost_level: int = 3,
    highlighted_dps: int | None = None,
) -> TeamReport:
    """Pure-logic entry point — returns a fully-built :class:`TeamReport`.

    Tests and the audit CLI exercise this directly; the dormant slash
    command body delegates to it.
    """
    profile = AssumptionProfile(boost_level=boost_level)
    bucketed = aggregator.aggregate_team(
        conn,
        frontrow_form_ids=frontrow_form_ids,
        backrow_form_ids=backrow_form_ids,
        pet_id=pet_id,
        divine_beast=divine_beast,
        cap_orbs=cap_orbs,
        profile=profile,
    )
    verdict = survivability.assess(bucketed, conn)
    matrix = coverage.build(bucketed)
    damage = damage_estimate.build(
        bucketed, conn, highlighted_dps=highlighted_dps,
    )
    return TeamReport(
        bucketed=bucketed,
        survivability=verdict,
        coverage=matrix,
        damage=damage,
    )


async def _analyze_team_impl(
    interaction: discord.Interaction,
    conn: sqlite3.Connection,
    *,
    frontrow_form_ids: list[int],
    backrow_form_ids: list[int],
    pet_id: int | None,
    divine_beast: bool,
    cap_orbs: int,
    boost_level: int,
    highlighted_dps: int | None,
) -> None:
    """Body of the dormant ``/analyze_team`` command.

    Pure of Discord-runtime side effects until ``interaction.response``
    is called at the very end, so unit tests can pass a stub interaction
    and assert on the embed payload.
    """
    report = build_team_report(
        conn,
        frontrow_form_ids=frontrow_form_ids,
        backrow_form_ids=backrow_form_ids,
        pet_id=pet_id,
        divine_beast=divine_beast,
        cap_orbs=cap_orbs,
        boost_level=boost_level,
        highlighted_dps=highlighted_dps,
    )
    embed = team_embeds.build(conn, report)
    await interaction.response.send_message(embed=embed)


def register(tree: app_commands.CommandTree) -> None:  # noqa: ARG001
    """Intentional no-op until the audit clears.

    The dormancy lives here: ``bot/commands.py::register`` does NOT
    call this function. To activate, replace the body with the
    slash-command block sketched in this module's docstring and
    invoke ``team_commands.register(tree)`` from ``bot/commands.py``.

    Sketch (uncomment + import :func:`build_team_report` /
    :func:`_analyze_team_impl` when activating)::

        @tree.command(name="analyze_team", description="Analyze a CotC team's offense and survivability.")
        @app_commands.describe(
            frontrow1="Frontrow slot 1", frontrow2="Frontrow slot 2",
            frontrow3="Frontrow slot 3", frontrow4="Frontrow slot 4",
            backrow1="Backrow slot 1 (optional)",
            backrow2="Backrow slot 2 (optional)",
            backrow3="Backrow slot 3 (optional)",
            backrow4="Backrow slot 4 (optional)",
            pet="Pet (optional, for G5 buffs)",
            divine_beast="Divine Beast active (G6 +10%)",
            cap_orbs="Free damage-cap orbs equipped on the team (each = +100k cap)",
            dps="Highlight this team member's damage estimate (optional)",
            boost_level="Assumed boost level (0/1/2/MAX). Default MAX.",
        )
        async def analyze_team_cmd(
            interaction: discord.Interaction,
            frontrow1: str, frontrow2: str | None = None,
            frontrow3: str | None = None, frontrow4: str | None = None,
            backrow1: str | None = None, backrow2: str | None = None,
            backrow3: str | None = None, backrow4: str | None = None,
            pet: str | None = None,
            divine_beast: bool = False,
            cap_orbs: app_commands.Choice[int] | None = None,
            dps: str | None = None,
            boost_level: app_commands.Choice[str] | None = None,
        ) -> None:
            ...
    """
    return None
