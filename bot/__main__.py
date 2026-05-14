"""Entrypoint: `python -m bot`.

Loads config (env vars first, ~/.cotc-search/config.toml fallback), bootstraps
the SQLite DB, optionally runs a cold-start sync if the DB is empty, and
starts the Discord client.
"""
from __future__ import annotations

import logging
import sys

import config
from bot import db as bot_db
from bot.client import CotCBot
from db import repo


def _cold_start_sync_if_empty() -> None:
    """If the DB has no forms, run one sync so /character works on first launch.

    Useful on Railway: a fresh container with an empty volume comes online
    and is searchable without anyone having to invoke /refresh first. If
    there's no API key, we just log and continue — the bot will still serve
    /search etc. (returning 'no matches'), and an admin can run /refresh
    once a key is configured.
    """
    conn = bot_db.conn()
    counts = repo.counts(conn)
    if counts["character_forms"] > 0:
        logging.info("DB already populated (%d forms); skipping cold-start sync.",
                     counts["character_forms"])
        return
    api_key = config.get_setting("GOOGLE_API_KEY", "api_key")
    if not api_key:
        logging.warning(
            "DB is empty and no GOOGLE_API_KEY configured; skipping cold-start sync. "
            "Run /refresh once a key is set.",
        )
        return
    logging.info("DB is empty; running cold-start sync...")
    from sync.runner import run_sync
    try:
        summary = run_sync(api_key, progress=lambda m: logging.info("  sync: %s", m))
        logging.info("Cold-start sync OK: %s", summary)
    except Exception as exc:
        logging.exception("Cold-start sync failed: %s", exc)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    token = config.get_setting("DISCORD_BOT_TOKEN", "discord_token")
    if not token:
        logging.error("DISCORD_BOT_TOKEN not set (env or ~/.cotc-search/config.toml).")
        return 2

    test_guild_raw = config.get_setting("DISCORD_TEST_GUILD_ID", "discord_test_guild_id")
    test_guild_id: int | None = None
    if test_guild_raw:
        try:
            test_guild_id = int(test_guild_raw)
        except ValueError:
            logging.warning("DISCORD_TEST_GUILD_ID is not a valid integer; ignoring.")

    _cold_start_sync_if_empty()

    bot = CotCBot(test_guild_id=test_guild_id)
    bot.run(token, log_handler=None)  # we already configured logging above
    return 0


if __name__ == "__main__":
    sys.exit(main())
