"""Discord client + CommandTree wiring."""
from __future__ import annotations

import logging

import discord
from discord import app_commands

from bot.commands import register

logger = logging.getLogger(__name__)


class CotCBot(discord.Client):
    def __init__(self, *, test_guild_id: int | None = None) -> None:
        # Minimal intents: we only respond to slash-command interactions.
        intents = discord.Intents.none()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self._test_guild_id = test_guild_id
        register(self.tree)

    async def setup_hook(self) -> None:
        # `setup_hook` runs once before connecting to the gateway. Sync the
        # command tree here so commands are visible from first /-press.
        if self._test_guild_id:
            guild = discord.Object(id=self._test_guild_id)
            # Mirror global commands into the test guild for instant updates.
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            logger.info("Synced commands to test guild %s", self._test_guild_id)
        else:
            await self.tree.sync()
            logger.info("Synced commands globally (propagation can take ~1h)")

    async def on_ready(self) -> None:
        logger.info("Logged in as %s (id=%s)", self.user, getattr(self.user, "id", "?"))
