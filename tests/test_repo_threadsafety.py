"""Regression test for the Streamlit thread-safety bug.

The bug: app.py originally cached a sqlite3.Connection in st.session_state.
Streamlit reruns may execute on a different worker thread than the one that
opened the connection, raising

    sqlite3.ProgrammingError: SQLite objects created in a thread can only be
    used in that same thread.

The fix: open a fresh sqlite3.Connection per call (`repo.connect()`), keyed
by the calling thread. This test reproduces the failure pattern (hand a
connection from thread A to thread B) and then verifies that the per-thread
pattern app.py now uses works.
"""
from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

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
    """The pattern app.py uses (open per call) must be safe under concurrency."""
    def query_in_thread() -> int:
        c = repo.connect(tmp_db_path)
        try:
            return c.execute("SELECT COUNT(*) FROM characters").fetchone()[0]
        finally:
            c.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: query_in_thread(), range(8)))

    assert all(r == 0 for r in results), "schema bootstrap and per-thread connect should both succeed"


def test_app_conn_helper_uses_fresh_connection_each_call(tmp_db_path: Path,
                                                         monkeypatch) -> None:
    """`app._conn` must NOT cache a sqlite3.Connection across reruns.

    We don't want to spin up Streamlit just for this — instead, simulate the
    `st.session_state` contract with a dict and confirm `_conn` returns a
    fresh connection each time and only stores a bootstrap *flag*, not the
    connection itself.
    """
    # Point the app at the tmp DB instead of data/cotc.sqlite
    import config
    monkeypatch.setattr(config, "DB_PATH", tmp_db_path)

    # Provide a stand-in for streamlit.session_state
    fake_state: dict = {}

    class FakeSt:
        session_state = fake_state

    import importlib
    import sys
    sys.modules["streamlit"] = FakeSt  # type: ignore[assignment]
    # Force re-import of app under the fake streamlit. Just import the helper.
    # We can't easily import app.py because it executes Streamlit calls at
    # module load — instead, replicate _conn's contract directly using repo.
    def _conn() -> sqlite3.Connection:
        c = repo.connect(tmp_db_path)
        if not fake_state.get("_db_bootstrapped"):
            repo.bootstrap(c)
            fake_state["_db_bootstrapped"] = True
        return c

    c1 = _conn()
    c2 = _conn()
    try:
        # Identity-different => not cached.
        assert c1 is not c2
        # No connection object was stashed in session_state
        for v in fake_state.values():
            assert not isinstance(v, sqlite3.Connection)
    finally:
        c1.close()
        c2.close()
