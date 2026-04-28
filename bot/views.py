"""Interactive `discord.ui.View` for `/character`.

The View carries a single `Select` dropdown that swaps the embed between
"skills", "a4", and "info" sections. Each callback rebuilds the embed via
`embeds.build_section_embed` and edits the message in place.

The View has the discord.py default 180s timeout — after that the dropdown
stops working and the user has to re-run `/character`. Persistent views
would lift the timeout but require encoding the form_id in a `custom_id`
and looking it up on every interaction; we accept the timeout for v1.
"""
from __future__ import annotations

import logging

import discord

from bot import db as bot_db
from bot import embeds

logger = logging.getLogger(__name__)


class _SectionSelect(discord.ui.Select["CharacterView"]):
    def __init__(self, current: embeds.Section) -> None:
        options = [
            discord.SelectOption(
                label=embeds.SECTION_LABELS[s],
                description=embeds.SECTION_DESCRIPTIONS[s],
                value=s,
                default=(s == current),
            )
            for s in embeds.SECTIONS
        ]
        super().__init__(
            placeholder="Choose section…",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if view is None:
            await interaction.response.defer()
            return
        section = self.values[0]
        if section not in embeds.SECTIONS:
            await interaction.response.defer()
            return
        view.section = section  # type: ignore[assignment]

        conn = bot_db.conn()
        embed = embeds.build_section_embed(conn, view.form_id, section)
        if embed is None:
            await interaction.response.send_message(
                "That form was removed by a recent refresh — re-run `/character`.",
                ephemeral=True,
            )
            return

        # Rebuild the select so the new section becomes the default-selected
        # option (Discord clears the visible choice after a callback otherwise).
        view.clear_items()
        view.add_item(_SectionSelect(current=section))
        await interaction.response.edit_message(embed=embed, view=view)


class CharacterView(discord.ui.View):
    """Dropdown wrapper for `/character`.

    Holds the form_id and currently-selected section. The Select's
    callback handles re-rendering and message edits.
    """

    def __init__(self, form_id: int, section: embeds.Section = embeds.DEFAULT_SECTION) -> None:
        super().__init__(timeout=180)
        self.form_id = form_id
        self.section = section
        self.add_item(_SectionSelect(current=section))
