"""DB-layer tests: schema bootstrap, CRUD, FTS5, search filters."""
from __future__ import annotations

from pathlib import Path

import pytest

from db import repo


def test_bootstrap_creates_all_tables(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
    )}
    expected = {
        "characters", "character_forms", "character_affinities",
        "skills", "equipment", "character_profile",
        "sync_runs", "raw_snapshots",
    }
    missing = expected - names
    conn.close()
    assert not missing, f"missing tables after bootstrap: {missing}"


def test_fts5_virtual_table_exists(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    n = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE name='characters_fts' AND type='table'"
    ).fetchone()[0]
    conn.close()
    assert n == 1, "characters_fts virtual table missing — FTS5 not compiled in?"


def _seed(conn) -> int:
    """Insert one canonical character + one form + a couple of skills."""
    ch_id = repo.upsert_character(conn, canonical_name="Cyrus",
                                  base_role="scholar", base_weapon="tome")
    form_id = repo.insert_form(
        conn, character_id=ch_id, display_name="Cyrus", rarity="5*",
        sheet_gid=519845584, source_row=10, name_color_hex="#CC0000",
    )
    repo.insert_skills(conn, form_id, [
        {"slot_order": 1, "name": None, "sp_cost": 18, "kind": "active",
         "boost_level": None, "description": "1x single-target Fire (1x 200 Power)",
         "power_min": 200, "power_max": 200, "hits": 1},
        {"slot_order": 2, "name": None, "sp_cost": 30, "kind": "ultimate",
         "boost_level": 1, "description": "AoE Fire damage",
         "power_min": None, "power_max": None, "hits": None},
    ])
    repo.insert_equipment(conn, form_id, [
        {"slot": None, "name": "Scholar's Tome", "description": None}
    ])
    repo.insert_affinities(conn, form_id, [
        ("weakness", "Wind", None),
        ("element", "Fire", None),
    ])
    return form_id


def test_upsert_character_idempotent(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    a = repo.upsert_character(conn, canonical_name="X", base_role="thief", base_weapon="dagger")
    b = repo.upsert_character(conn, canonical_name="X", base_role="thief", base_weapon="dagger")
    assert a == b
    n = conn.execute("SELECT COUNT(*) FROM characters").fetchone()[0]
    assert n == 1
    conn.close()


def test_clear_data_tables_keeps_sync_history(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    run_id = repo.start_sync_run(conn)
    _seed(conn)
    repo.clear_data_tables(conn)
    assert conn.execute("SELECT COUNT(*) FROM characters").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM skills").fetchone()[0] == 0
    # sync_runs should NOT be wiped
    assert conn.execute("SELECT COUNT(*) FROM sync_runs").fetchone()[0] == 1
    conn.close()


def test_search_forms_by_role(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    _seed(conn)
    repo.rebuild_fts(conn)
    res = repo.search_forms(conn, roles=["scholar"])
    assert len(res) == 1
    assert res[0]["display_name"] == "Cyrus"
    res2 = repo.search_forms(conn, roles=["warrior"])
    assert len(res2) == 0
    conn.close()


def test_search_forms_by_weakness(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    _seed(conn)
    repo.rebuild_fts(conn)
    res = repo.search_forms(conn, weaknesses=["Wind"])
    assert len(res) == 1
    res = repo.search_forms(conn, weaknesses=["Dark"])  # not seeded
    assert len(res) == 0
    conn.close()


def test_search_forms_fts_text(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    _seed(conn)
    repo.rebuild_fts(conn)
    # 'Fire' should hit on skill description AND elemental affinity description
    res = repo.search_forms(conn, text="Fire")
    assert len(res) >= 1
    # Equipment text should be searchable too
    res2 = repo.search_forms(conn, text="Tome")
    assert len(res2) == 1
    # Garbage query returns 0 without raising
    res3 = repo.search_forms(conn, text="nonexistent_xyz")
    assert len(res3) == 0
    conn.close()


def test_fts_query_handles_special_chars(tmp_db_path: Path) -> None:
    """The FTS sanitizer must not crash on punctuation or empty input."""
    conn = repo.connect(tmp_db_path)
    _seed(conn)
    repo.rebuild_fts(conn)
    # All of these previously could raise sqlite3.OperationalError if the
    # query weren't sanitized.
    repo.search_forms(conn, text="!!!")
    repo.search_forms(conn, text='"quoted"')
    repo.search_forms(conn, text="   ")
    repo.search_forms(conn, text="O'Brien & son")
    conn.close()


def test_counts(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    _seed(conn)
    c = repo.counts(conn)
    assert c["characters"] == 1
    assert c["character_forms"] == 1
    assert c["skills"] == 2
    assert c["equipment"] == 1
    assert c["character_affinities"] == 2
    conn.close()
