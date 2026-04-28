"""Regression test for the SQLite cross-thread bug.

The bug: an earlier version cached a sqlite3.Connection across reruns /
handlers. Reruns may execute on a different worker thread than the one
that opened the connection, raising

    sqlite3.ProgrammingError: SQLite objects created in a thread can only be
    used in that same thread.

The fix: open a fresh sqlite3.Connection per call (`repo.connect()`), keyed
by the calling thread. `bot/db.py::conn` follows this pattern. This test
reproduces the failure pattern (hand a connection from thread A to thread
B) and then verifies that the per-thread pattern the bot uses works.
"""
from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from db import repo


def test_sharing_connection_across_threads_raises(tmp_db_path: Path) -> None:
    """Document the original failure mode so we never accidentally reintroduce it."""
    conn = repo.connect(tmp_db_path)

    errors: list[Exception] = []

    def use_in_other_thread() -> None:
        try:
            conn.execute("SELECT COUNT(*) FROM characters").fetchone()
        except sqlite3.ProgrammingError as exc:  # the exact error we're guarding against
            errors.append(exc)

    t = threading.Thread(target=use_in_other_thread)
    t.start()
    t.join()
    conn.close()

    assert len(errors) == 1, "expected sqlite3.ProgrammingError when sharing a connection across threads"
    assert "thread" in str(errors[0]).lower()


def test_per_thread_connection_pattern_works(tmp_db_path: Path) -> None:
    """The pattern bot/db.py uses (open per call) must be safe under concurrency."""
    def query_in_thread() -> int:
        c = repo.connect(tmp_db_path)
        try:
            return c.execute("SELECT COUNT(*) FROM characters").fetchone()[0]
        finally:
            c.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: query_in_thread(), range(8)))

    assert all(r == 0 for r in results), "schema bootstrap and per-thread connect should both succeed"
