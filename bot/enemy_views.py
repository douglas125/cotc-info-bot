"""Interactive `discord.ui.View` for `/enemy`.

The view holds an enemy_id and the current rank. The Select dropdown lists
only the ranks this particular enemy actually has (Rank1..EX3 for ranked
encounters; NPCs have a single 'Default' form, in which case the view is
created without a Select at all).

On selection the callback rebuilds the embed via
`enemy_embeds.build_enemy_embed`, recreates the Select with the new rank
as the default-selected option, and edits the message in place.
"""
from __future__ import annotations

import logging

import discord

from bot import db as bot_db
from bot import enemy_embeds

logger = logging.getLogger(__name__)

ENEMY_REMOVED_MSG = (
    "That enemy entry was removed by a recent refresh — re-run `/enemy`."
)


class _RankSelect(discord.ui.Select["EnemyView"]):
    def __init__(self, available: list[enemy_embeds.Rank], current: enemy_embeds.Rank) -> None:
        options = [
            discord.SelectOption(
                label=enemy_embeds.RANK_LABELS[r],
                description=enemy_embeds.RANK_DESCRIPTIONS[r],
                value=r,
                default=(r == current),
            )
            for r in available
        ]
        super().__init__(
            placeholder="Choose difficulty…",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if view is None:
            await interaction.response.defer()
            return
        rank: enemy_embeds.Rank = self.values[0]  # type: ignore[assignment]
        conn = bot_db.conn()
        embed = enemy_embeds.build_enemy_embed(conn, view.enemy_id, rank)
        if embed is None:
            await interaction.response.send_message(
                ENEMY_REMOVED_MSG, ephemeral=True,
            )
            return
        view.clear_items()
        view.add_item(_RankSelect(available=view.available_ranks, current=rank))
        await interaction.response.edit_message(embed=embed, view=view)


class EnemyView(discord.ui.View):
    """Dropdown wrapper for `/enemy`.

    Single-rank ('Default') NPCs render without a Select — the view is
    constructed empty so Discord shows just the embed.
    """

    def __init__(
        self,
        enemy_id: int,
        available_ranks: list[enemy_embeds.Rank],
        current_rank: enemy_embeds.Rank,
    ) -> None:
        super().__init__(timeout=180)
        self.enemy_id = enemy_id
        self.available_ranks = available_ranks
        if len(available_ranks) > 1:
            self.add_item(_RankSelect(available=available_ranks, current=current_rank))
