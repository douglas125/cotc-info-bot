"""SQLite connection helper for the bot.

Open a fresh connection per call, bootstrap the schema once. discord.py
runs handlers on the asyncio loop's main thread, so a per-call connection
is the simplest way to avoid sqlite3's "connections are bound to their
creation thread" trap when handlers occasionally get scheduled onto
thread-pool executors (e.g. for `run_in_executor`).
"""
from __future__ import annotations

import sqlite3

from db import repo

_BOOTSTRAPPED = False


def conn() -> sqlite3.Connection:
    global _BOOTSTRAPPED
    c = repo.connect()
    if not _BOOTSTRAPPED:
        repo.bootstrap(c)
        _BOOTSTRAPPED = True
    return c


def reset_bootstrap_flag() -> None:
    """Force the next conn() call to re-run schema bootstrap.

    Useful in tests that swap the DB path; not used in production.
    """
    global _BOOTSTRAPPED
    _BOOTSTRAPPED = False
