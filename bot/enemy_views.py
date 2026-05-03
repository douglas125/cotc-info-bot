"""Interactive `discord.ui.View` for `/enemy`.

The view holds an enemy_id and the current selection. The Select dropdown
lists only the ranks this particular enemy actually has (Rank1..EX3 for
ranked encounters) plus a Fight notes option when seeded notes match.

On selection the callback rebuilds the stats embed/weakness image or the
fight-notes embed pages, recreates the Select with the new option as the
default-selected option, and edits the message in place.
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


NOTES_VALUE = "notes"


class _EnemySelect(discord.ui.Select["EnemyView"]):
    def __init__(
        self,
        available: list[enemy_embeds.Rank],
        current: str,
        *,
        has_fight_notes: bool = False,
    ) -> None:
        options = [
            discord.SelectOption(
                label=enemy_embeds.RANK_LABELS[r],
                description=enemy_embeds.RANK_DESCRIPTIONS[r],
                value=f"rank:{r}",
                default=(f"rank:{r}" == current),
            )
            for r in available
        ]
        if has_fight_notes:
            options.append(discord.SelectOption(
                label="Fight notes",
                description="Game8 summary, mechanics, strategy, and actions",
                value=NOTES_VALUE,
                default=(current == NOTES_VALUE),
            ))
        super().__init__(
            placeholder="Choose view...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if view is None:
            await interaction.response.defer()
            return
        conn = bot_db.conn()
        selected = self.values[0]
        if selected == NOTES_VALUE:
            message = enemy_embeds.build_enemy_fight_notes_message(conn, view.enemy_id)
            current = NOTES_VALUE
        else:
            rank: enemy_embeds.Rank = selected.removeprefix("rank:")  # type: ignore[assignment]
            message = enemy_embeds.build_enemy_message(conn, view.enemy_id, rank)
            current = f"rank:{rank}"
        if message is None:
            await interaction.response.send_message(
                ENEMY_REMOVED_MSG, ephemeral=True,
            )
            return
        view.clear_items()
        view.add_item(_EnemySelect(
            available=view.available_ranks,
            current=current,
            has_fight_notes=view.has_fight_notes,
        ))
        if message.embeds:
            await interaction.response.edit_message(
                embeds=list(message.embeds),
                attachments=[],
                view=view,
            )
        elif message.file is None:
            await interaction.response.edit_message(
                embed=message.embed,
                attachments=[],
                view=view,
            )
        else:
            await interaction.response.edit_message(
                embed=message.embed,
                attachments=[message.file],
                view=view,
            )


class EnemyView(discord.ui.View):
    """Dropdown wrapper for `/enemy`.

    Single-rank ('Default') NPCs render without a Select unless they have
    fight notes — then the selector is still shown so notes are reachable.
    """

    def __init__(
        self,
        enemy_id: int,
        available_ranks: list[enemy_embeds.Rank],
        current_rank: enemy_embeds.Rank,
        has_fight_notes: bool = False,
    ) -> None:
        super().__init__(timeout=180)
        self.enemy_id = enemy_id
        self.has_fight_notes = has_fight_notes
        self.available_ranks = sorted(
            available_ranks,
            key=lambda rank: enemy_embeds.RANK_ORDER.get(rank, 99),
        )
        if len(self.available_ranks) > 1 or self.has_fight_notes:
            self.add_item(_EnemySelect(
                available=self.available_ranks,
                current=f"rank:{current_rank}",
                has_fight_notes=self.has_fight_notes,
            ))


class _RankSelect(_EnemySelect):
    """Backward-compatible rank-only select used by older tests."""

    def __init__(self, available: list[enemy_embeds.Rank], current: enemy_embeds.Rank) -> None:
        super().__init__(
            available=available,
            current=f"rank:{current}",
            has_fight_notes=False,
        )
