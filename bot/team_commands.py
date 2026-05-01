"""``/analyze_team`` slash command.

Slash params: ``frontrow1..frontrow4`` and ``backrow1..backrow4`` (all
optional; users can fill 1-8 slots), plus ``pet``, ``divine_beast``,
``cap_orbs``, ``dps``, ``boost_level``. All 8 members contribute equally
to the offensive bucket math and the survivability verdict — passives
fire from either row in CotC unless explicitly row-gated by the skill
text.

:func:`build_team_report` is the pure-logic entry point — used by both
the slash-command body here and the offline audit CLI in
``analysis/audit.py``. Tests in ``tests/test_team_analyze_integration.py``
exercise it directly.

:func:`register` attaches the command to a ``CommandTree``; it's called
once from ``bot/commands.py::register`` at bot startup.
"""
from __future__ import annotations

import sqlite3
from typing import Iterable

import discord
from discord import app_commands

from analysis import aggregator, coverage, damage_estimate, survivability
from analysis.types import AssumptionProfile, NameResolution, TeamReport
from bot import team_embeds
from db import repo


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
    name_resolutions: Iterable[NameResolution] = (),
) -> TeamReport:
    """Pure-logic entry point — returns a fully-built :class:`TeamReport`.

    Tests and the audit CLI exercise this directly; the dormant slash
    command body delegates to it. ``name_resolutions`` carries the
    typed-input → form_id trail so the embed can surface aliased and
    unresolved names; the audit CLI populates it via
    :func:`analysis.resolve.resolve_form_id` and the (future) slash
    command will populate it from its autocomplete callbacks.
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
        name_resolutions=tuple(name_resolutions),
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
    name_resolutions: Iterable[NameResolution] = (),
) -> None:
    """Body of the ``/analyze_team`` command.

    Pure of Discord-runtime side effects until ``interaction.response``
    is called at the very end, so unit tests can pass a stub interaction
    and assert on the embed payload.
    """
    from io import BytesIO

    import discord

    from analysis import matrix_image
    from bot.team_views import AnalyzeTeamView

    report = build_team_report(
        conn,
        frontrow_form_ids=frontrow_form_ids,
        backrow_form_ids=backrow_form_ids,
        pet_id=pet_id,
        divine_beast=divine_beast,
        cap_orbs=cap_orbs,
        boost_level=boost_level,
        highlighted_dps=highlighted_dps,
        name_resolutions=name_resolutions,
    )
    rendered = matrix_image.render(report.bucketed)
    message = team_embeds.build_matrix_message(
        conn, report, rendered_image=rendered,
    )
    view = AnalyzeTeamView(
        report=report,
        matrix_bytes=rendered.data,
        matrix_filename=rendered.filename,
    )
    await interaction.response.send_message(
        embed=message.embed,
        file=discord.File(BytesIO(rendered.data), filename=rendered.filename),
        view=view,
    )


def _resolve_slots(
    conn: sqlite3.Connection,
    typed: list[str],
    cmds_mod,
) -> tuple[list[int], list[NameResolution]]:
    """Resolve a list of typed slot inputs to form_ids + a NameResolution
    trail. Mirrors ``analysis/audit.py``'s exact-vs-alias classification.

    ``cmds_mod`` is ``bot.commands`` passed in to avoid the
    ``bot.commands`` ⇄ ``bot.team_commands`` import cycle at module load.
    """
    ids: list[int] = []
    resolutions: list[NameResolution] = []
    for raw in typed:
        fid = cmds_mod._resolve_form_id(conn, raw)
        if fid is None:
            resolutions.append(NameResolution(
                typed=raw, form_id=None,
                resolved_display_name=None, via="unresolved",
            ))
            continue
        ids.append(fid)
        row = repo.get_form(conn, fid)
        display = row["display_name"] if row else None
        via = "exact" if (display and display.lower() == raw.strip().lower()) else "alias"
        resolutions.append(NameResolution(
            typed=raw, form_id=fid,
            resolved_display_name=display, via=via,
        ))
    return ids, resolutions


def register(tree: app_commands.CommandTree) -> None:
    """Attach ``/analyze_team`` to the given command tree.

    Called from ``bot/commands.py::register`` after the other commands
    are defined. Helpers from ``bot.commands`` are imported lazily here
    to avoid the cycle (``bot.commands`` imports this module at top).
    """
    from bot import commands as _cmds
    from bot import db as bot_db

    @tree.command(
        name="analyze_team",
        description="Analyze a CotC team's offense and survivability.",
    )
    @app_commands.describe(
        frontrow1="Frontrow slot 1",
        frontrow2="Frontrow slot 2 (optional)",
        frontrow3="Frontrow slot 3 (optional)",
        frontrow4="Frontrow slot 4 (optional)",
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
    @app_commands.choices(boost_level=BOOST_CHOICES, cap_orbs=CAP_ORB_CHOICES)
    async def analyze_team_cmd(
        interaction: discord.Interaction,
        frontrow1: str,
        frontrow2: str | None = None,
        frontrow3: str | None = None,
        frontrow4: str | None = None,
        backrow1: str | None = None,
        backrow2: str | None = None,
        backrow3: str | None = None,
        backrow4: str | None = None,
        pet: str | None = None,
        divine_beast: bool = False,
        cap_orbs: app_commands.Choice[int] | None = None,
        dps: str | None = None,
        boost_level: app_commands.Choice[str] | None = None,
    ) -> None:
        conn = bot_db.conn()
        _cmds._record_command_usage(conn, "analyze_team")

        front_typed = [s for s in (frontrow1, frontrow2, frontrow3, frontrow4) if s]
        back_typed = [s for s in (backrow1, backrow2, backrow3, backrow4) if s]

        front_ids, front_res = _resolve_slots(conn, front_typed, _cmds)
        back_ids, back_res = _resolve_slots(conn, back_typed, _cmds)

        if not front_ids and not back_ids:
            await interaction.response.send_message(
                "No team members resolved. Pick a name from the autocomplete list.",
                ephemeral=True,
            )
            return

        pet_id = _cmds._resolve_pet_id(conn, pet) if pet else None
        dps_id = _cmds._resolve_form_id(conn, dps) if dps else None

        await _analyze_team_impl(
            interaction,
            conn,
            frontrow_form_ids=front_ids,
            backrow_form_ids=back_ids,
            pet_id=pet_id,
            divine_beast=divine_beast,
            cap_orbs=(cap_orbs.value if cap_orbs else 0),
            boost_level=(int(boost_level.value) if boost_level else 3),
            highlighted_dps=dps_id,
            name_resolutions=tuple(front_res + back_res),
        )

    async def _ac_form(interaction: discord.Interaction, current: str):
        return _cmds._autocomplete_forms(bot_db.conn(), current)

    async def _ac_pet(interaction: discord.Interaction, current: str):
        return _cmds._autocomplete_pets(bot_db.conn(), current)

    for slot in (
        "frontrow1", "frontrow2", "frontrow3", "frontrow4",
        "backrow1", "backrow2", "backrow3", "backrow4",
        "dps",
    ):
        analyze_team_cmd.autocomplete(slot)(_ac_form)
    analyze_team_cmd.autocomplete("pet")(_ac_pet)
