"""Shared pytest fixtures.

Most tests run against a temp SQLite file rather than `data/cotc.sqlite` so
they neither depend on nor mutate the real local mirror.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make the project root importable so `from db import repo` etc. work
# regardless of where pytest is invoked from.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def tmp_db_path(tmp_path: Path) -> Path:
    """A throwaway SQLite path bootstrapped with the project schema."""
    from db import repo

    db = tmp_path / "test.sqlite"
    conn = repo.connect(db)
    repo.bootstrap(conn)
    conn.close()
    return db


@pytest.fixture()
def offline_sprite_refresh(monkeypatch):
    """Runner tests must not fetch the external sprite wiki."""
    monkeypatch.setattr('scripts.refresh_sprite_urls.refresh_sprite_urls', lambda conn: {
        'unmatched': [], 'missing': [], 'total_mapped': 0,
        'character_total': 0, 'page_mapped': 0, 'overrides': 0,
    })
