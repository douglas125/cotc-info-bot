"""Interactive `discord.ui.View` for `/analyze_team`.

The view carries a single `Select` dropdown that swaps the embed
between two sections:

  - ``"matrix"`` (default) — the rendered bucket-matrix PNG.
  - ``"analysis"`` — the existing text-heavy breakdown.

State carried on the View instance:

  - ``report``: the :class:`TeamReport` produced by the slash command.
  - ``matrix_bytes`` + ``matrix_filename``: cached PNG bytes from
    :func:`analysis.matrix_image.render`. Cached so toggling
    matrix → analysis → matrix doesn't re-render — only a fresh
    ``discord.File`` is created from the cached bytes (the underlying
    `BytesIO` stream is consumed on each send / edit).

180s default timeout (matches `bot/views.py::CharacterView`).
"""
from __future__ import annotations

import logging
from io import BytesIO
from typing import Literal

import discord

from analysis.matrix_image import RenderedMatrixImage
from analysis.types import TeamReport
from bot import db as bot_db
from bot import team_embeds

logger = logging.getLogger(__name__)


TeamSection = Literal["matrix", "analysis"]
DEFAULT_SECTION: TeamSection = "matrix"

SECTION_LABELS: dict[TeamSection, str] = {
    "matrix":   "Damage matrix",
    "analysis": "Analysis breakdown",
}
SECTION_DESCRIPTIONS: dict[TeamSection, str] = {
    "matrix":   "Bucket math as a 2D image (default).",
    "analysis": "Best use, gaps, survivability, support roles.",
}
SECTION_ORDER: tuple[TeamSection, ...] = ("matrix", "analysis")


class _TeamSectionSelect(discord.ui.Select["AnalyzeTeamView"]):
    def __init__(self, current: TeamSection) -> None:
        options = [
            discord.SelectOption(
                label=SECTION_LABELS[s],
                description=SECTION_DESCRIPTIONS[s],
                value=s,
                default=(s == current),
            )
            for s in SECTION_ORDER
        ]
        super().__init__(
            placeholder="Choose view…",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if view is None:
            await interaction.response.defer()
            return
        section: TeamSection = self.values[0]  # type: ignore[assignment]

        conn = bot_db.conn()
        if section == "matrix":
            rendered = RenderedMatrixImage(
                filename=view.matrix_filename,
                data=view.matrix_bytes,
            )
            message = team_embeds.build_matrix_message(
                conn, view.report, rendered_image=rendered,
            )
            view.clear_items()
            view.add_item(_TeamSectionSelect(current=section))
            file = discord.File(
                BytesIO(view.matrix_bytes),
                filename=view.matrix_filename,
            )
            await interaction.response.edit_message(
                embed=message.embed,
                attachments=[file],
                view=view,
            )
        else:
            message = team_embeds.build_analysis_message(conn, view.report)
            view.clear_items()
            view.add_item(_TeamSectionSelect(current=section))
            await interaction.response.edit_message(
                embed=message.embed,
                attachments=[],
                view=view,
            )


class AnalyzeTeamView(discord.ui.View):
    """Dropdown wrapper for `/analyze_team`.

    Holds the team report and the rendered matrix bytes so the select
    callback can rebuild either view on demand. Default `discord.py`
    180s timeout — the dropdown stops responding after that and the
    user has to re-run `/analyze_team`.
    """

    def __init__(
        self,
        *,
        report: TeamReport,
        matrix_bytes: bytes,
        matrix_filename: str = "team_matrix.png",
        section: TeamSection = DEFAULT_SECTION,
    ) -> None:
        super().__init__(timeout=180)
        self.report = report
        self.matrix_bytes = matrix_bytes
        self.matrix_filename = matrix_filename
        self.add_item(_TeamSectionSelect(current=section))
