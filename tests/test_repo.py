"""DB-layer tests: schema bootstrap, CRUD, FTS5, search filters."""
from __future__ import annotations

import json
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
        "skills", "equipment", "equipment_stats", "character_profile",
        "sync_runs", "raw_snapshots",
        "feedback_submissions",
        "command_usage_daily",
        "arena_fight_notes",
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
         "learn_board": None, "tier_level": None,
         "initial_use": None, "cooldown": None,
         "description": "1x single-target Fire (1x 200 Power)",
         "power_min": 200, "power_max": 200, "hits": 1},
        {"slot_order": 2, "name": None, "sp_cost": 30, "kind": "active",
         "learn_board": 2, "tier_level": None,
         "initial_use": None, "cooldown": None,
         "description": "AoE Fire damage",
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


def test_clear_character_tables_keeps_sync_history(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    run_id = repo.start_sync_run(conn)
    _seed(conn)
    repo.clear_character_tables(conn)
    assert conn.execute("SELECT COUNT(*) FROM characters").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM skills").fetchone()[0] == 0
    # sync_runs should NOT be wiped
    assert conn.execute("SELECT COUNT(*) FROM sync_runs").fetchone()[0] == 1
    conn.close()


def test_arena_fight_notes_seeded_and_refresh_safe(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    rows = repo.list_arena_fight_notes(conn)
    assert len(rows) == 13
    assert {row["display_name"] for row in rows} >= {"Tikilen", "Kagemune", "Rayme"}
    for row in rows:
        sections = json.loads(row["actions_json"])
        assert sections
        assert all(
            {"title", "kind", "columns", "rows"} <= set(section)
            for section in sections
        )
        assert {section["kind"] for section in sections} <= {
            "turn_table",
            "state_table",
            "action_catalog",
        }

    repo.clear_enemy_tables(conn)
    assert len(repo.list_arena_fight_notes(conn)) == 13
    conn.close()


def test_arena_fight_notes_match_enemy_alias(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    enemy_id = repo.upsert_enemy(
        conn,
        canonical_name="Kagemume",
        category="Solistia Lvl 75",
        region="Solistia",
        sheet_gid=1,
        source_row=1,
        name_color_hex=None,
        hyperlink_url=None,
        is_npc=False,
    )
    note = repo.get_arena_fight_note_for_enemy(conn, enemy_id)
    assert note is not None
    assert note["display_name"] == "Kagemune"
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


def test_insert_equipment_with_stats_and_get_back(tmp_db_path: Path) -> None:
    """Equipment dicts may carry a 'stats' list of (name, value) tuples;
    they round-trip through insert_equipment + get_equipment_stats_by_form."""
    conn = repo.connect(tmp_db_path)
    ch = repo.upsert_character(conn, canonical_name="Bargello",
                                base_role="thief", base_weapon="dagger")
    form_id = repo.insert_form(conn, character_id=ch, display_name="Bargello",
                               rarity="5*")
    repo.insert_equipment(conn, form_id, [
        {"slot": None, "name": "Cuffs of the Family",
         "description": "Self 100000 Damage Cap Up",
         "is_exclusive": False,
         "stats": [("SP", 40), ("ATK", 100)]},
        {"slot": None, "name": "Bare Accessory",
         "description": None, "is_exclusive": False, "stats": []},
    ])
    eq = repo.get_equipment(conn, form_id)
    by_eq = repo.get_equipment_stats_by_form(conn, form_id)
    assert {e["name"] for e in eq} == {"Cuffs of the Family", "Bare Accessory"}
    cuffs = next(e for e in eq if e["name"] == "Cuffs of the Family")
    cuffs_stats = by_eq[cuffs["id"]]
    assert [(s["stat_name"], s["stat_value"]) for s in cuffs_stats] == \
        [("SP", 40), ("ATK", 100)]
    bare = next(e for e in eq if e["name"] == "Bare Accessory")
    assert bare["id"] not in by_eq
    conn.close()


def test_equipment_stats_negative_value_round_trip(tmp_db_path: Path) -> None:
    """Negative stat values (e.g. ATK -200 on Sorcery) must persist as-is."""
    conn = repo.connect(tmp_db_path)
    ch = repo.upsert_character(conn, canonical_name="Throne",
                                base_role="scholar", base_weapon="tome")
    form_id = repo.insert_form(conn, character_id=ch, display_name="Throne",
                               rarity="5*")
    repo.insert_equipment(conn, form_id, [
        {"slot": None, "name": "The Secrets of Sorcery",
         "description": "...", "is_exclusive": False,
         "stats": [("ATK", -200)]},
    ])
    eq = repo.get_equipment(conn, form_id)
    by_eq = repo.get_equipment_stats_by_form(conn, form_id)
    assert by_eq[eq[0]["id"]][0]["stat_value"] == -200
    conn.close()


def test_equipment_stats_cascade_on_form_delete(tmp_db_path: Path) -> None:
    """Wiping character_forms must cascade through equipment to equipment_stats."""
    conn = repo.connect(tmp_db_path)
    ch = repo.upsert_character(conn, canonical_name="X",
                                base_role="r", base_weapon="w")
    form_id = repo.insert_form(conn, character_id=ch, display_name="X", rarity="3*")
    repo.insert_equipment(conn, form_id, [
        {"slot": None, "name": "A", "description": None, "is_exclusive": False,
         "stats": [("ATK", 1), ("SP", 2)]},
    ])
    assert conn.execute("SELECT COUNT(*) FROM equipment_stats").fetchone()[0] == 2
    repo.clear_character_tables(conn)
    assert conn.execute("SELECT COUNT(*) FROM equipment_stats").fetchone()[0] == 0
    conn.close()


def test_bootstrap_migrates_legacy_skills_columns(tmp_path: Path) -> None:
    """A DB that was bootstrapped under the old schema (boost_level column,
    no tier_level/initial_use/cooldown) must transparently upgrade when
    repo.bootstrap runs against it — and existing data must survive."""
    import sqlite3
    legacy_db = tmp_path / "legacy.sqlite"
    raw = sqlite3.connect(legacy_db)
    # Pre-seed only the skills table in its old shape (boost_level, no
    # tier_level/initial_use/cooldown). The rest of the schema is created by
    # bootstrap below; CREATE TABLE IF NOT EXISTS leaves the legacy `skills`
    # table alone so the migration path can rename + add columns on it.
    raw.executescript("""
        CREATE TABLE skills (
            id INTEGER PRIMARY KEY,
            form_id INTEGER NOT NULL,
            slot_order INTEGER NOT NULL,
            name TEXT, sp_cost INTEGER, kind TEXT,
            boost_level INTEGER, description TEXT,
            power_min INTEGER, power_max INTEGER, hits INTEGER
        );
        INSERT INTO skills(form_id, slot_order, kind, boost_level, description)
        VALUES (1, 1, 'active', 2, 'legacy row');
    """)
    raw.commit()
    raw.close()

    conn = repo.connect(legacy_db)
    repo.bootstrap(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(skills)")}
    assert "boost_level" not in cols, "legacy column should have been renamed"
    assert {"learn_board", "tier_level", "initial_use", "cooldown"} <= cols

    row = conn.execute(
        "SELECT learn_board, description FROM skills WHERE id=1"
    ).fetchone()
    assert row["learn_board"] == 2
    assert row["description"] == "legacy row"
    conn.close()


# --- feedback ---------------------------------------------------------------

def test_feedback_insert_list_clear_roundtrip(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    a = repo.insert_feedback(
        conn, user_id=42, username="alice", guild_id=999,
        feedback_text="Castti A4 missing SP bonus",
    )
    b = repo.insert_feedback(
        conn, user_id=43, username="bob", guild_id=None,
        feedback_text="Erika element wrong",
    )
    rows = repo.list_feedback(conn, limit=10)
    assert len(rows) == 2
    # newest first; both rows share submitted_at granularity (seconds), so the
    # tiebreaker is id DESC — the second insert (`b`) must come first.
    assert rows[0]["id"] == b
    assert rows[1]["id"] == a
    assert rows[0]["username"] == "bob"
    assert rows[0]["guild_id"] is None
    assert rows[1]["guild_id"] == 999

    assert repo.count_feedback(conn) == 2
    deleted = repo.clear_feedback(conn)
    assert deleted == 2
    assert repo.list_feedback(conn) == []
    assert repo.count_feedback(conn) == 0
    conn.close()


def test_feedback_survives_clear_character_tables(tmp_db_path: Path) -> None:
    """`/refresh` calls clear_character_tables; community feedback MUST survive it."""
    conn = repo.connect(tmp_db_path)
    repo.insert_feedback(
        conn, user_id=1, username="user", guild_id=None,
        feedback_text="don't wipe me",
    )
    _seed(conn)
    repo.clear_character_tables(conn)
    assert conn.execute(
        "SELECT COUNT(*) FROM feedback_submissions"
    ).fetchone()[0] == 1
    conn.close()


def test_recent_feedback_timestamps_window(tmp_db_path: Path) -> None:
    """The rate-limit query returns rows newer than the cutoff and excludes older ones."""
    import sqlite3
    conn = repo.connect(tmp_db_path)
    # Insert three rows for user 42 with explicit timestamps spread across
    # the window, plus one outside it.
    cur: sqlite3.Cursor = conn.cursor()
    cur.executemany(
        "INSERT INTO feedback_submissions("
        "submitted_at, user_id, username, guild_id, feedback_text) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            ("2026-04-27T12:00:30Z", 42, "alice", None, "msg1"),
            ("2026-04-27T12:00:45Z", 42, "alice", None, "msg2"),
            ("2026-04-27T12:00:55Z", 42, "alice", None, "msg3"),
            ("2026-04-27T11:59:00Z", 42, "alice", None, "old"),
            ("2026-04-27T12:00:50Z", 99, "other", None, "different user"),
        ],
    )

    cutoff = "2026-04-27T12:00:00Z"   # everything in the same minute is recent
    recent = repo.recent_feedback_timestamps(
        conn, user_id=42, since_iso=cutoff, limit=10,
    )
    assert len(recent) == 3            # the "old" row is excluded
    # Ordered newest first
    assert recent[0] == "2026-04-27T12:00:55Z"
    assert recent[-1] == "2026-04-27T12:00:30Z"

    # Limit caps the result so a spammy user can't force an unbounded read.
    capped = repo.recent_feedback_timestamps(
        conn, user_id=42, since_iso=cutoff, limit=2,
    )
    assert capped == ["2026-04-27T12:00:55Z", "2026-04-27T12:00:45Z"]

    # Different user filter still works.
    assert repo.recent_feedback_timestamps(
        conn, user_id=42, since_iso="2026-04-27T12:00:50Z", limit=10,
    ) == ["2026-04-27T12:00:55Z"]
    conn.close()


def test_feedback_accepts_2000_char_body(tmp_db_path: Path) -> None:
    """Length cap is enforced by Discord's app_commands.Range, but the schema
    itself must not surprise us with a hidden cap."""
    conn = repo.connect(tmp_db_path)
    body = "x" * 2000
    rid = repo.insert_feedback(
        conn, user_id=1, username="bigtext", guild_id=None, feedback_text=body,
    )
    row = conn.execute(
        "SELECT feedback_text FROM feedback_submissions WHERE id = ?", (rid,)
    ).fetchone()
    assert len(row["feedback_text"]) == 2000
    conn.close()


# --- command usage telemetry ------------------------------------------------

def test_increment_command_usage_inserts_first_call(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    repo.increment_command_usage(conn, "character", usage_date="2026-04-28")
    rows = list(conn.execute(
        "SELECT command_name, usage_date, count FROM command_usage_daily"
    ))
    conn.close()
    assert len(rows) == 1
    assert rows[0]["command_name"] == "character"
    assert rows[0]["usage_date"] == "2026-04-28"
    assert rows[0]["count"] == 1


def test_increment_command_usage_increments_same_day(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    for _ in range(3):
        repo.increment_command_usage(conn, "enemy", usage_date="2026-04-28")
    rows = list(conn.execute(
        "SELECT command_name, count FROM command_usage_daily"
    ))
    conn.close()
    assert len(rows) == 1
    assert rows[0]["command_name"] == "enemy"
    assert rows[0]["count"] == 3


def test_increment_command_usage_separates_by_command_and_date(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    repo.increment_command_usage(conn, "character", usage_date="2026-04-28")
    repo.increment_command_usage(conn, "enemy",     usage_date="2026-04-28")
    repo.increment_command_usage(conn, "character", usage_date="2026-04-29")
    rows = sorted(
        conn.execute(
            "SELECT command_name, usage_date, count FROM command_usage_daily"
        ),
        key=lambda r: (r["command_name"], r["usage_date"]),
    )
    conn.close()
    assert [(r["command_name"], r["usage_date"], r["count"]) for r in rows] == [
        ("character", "2026-04-28", 1),
        ("character", "2026-04-29", 1),
        ("enemy",     "2026-04-28", 1),
    ]


def test_usage_in_window_returns_only_last_n_days(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    # Today - 0..11 days
    repo.increment_command_usage(conn, "character", usage_date="2026-04-29")
    repo.increment_command_usage(conn, "enemy",     usage_date="2026-04-29")
    repo.increment_command_usage(conn, "character", usage_date="2026-04-25")
    repo.increment_command_usage(conn, "character", usage_date="2026-04-20")  # day 9 (in)
    repo.increment_command_usage(conn, "character", usage_date="2026-04-19")  # day 10 (out)
    repo.increment_command_usage(conn, "enemy",     usage_date="2026-04-10")  # way out
    rows = repo.usage_in_window(conn, days=10, today="2026-04-29")
    conn.close()
    triples = [(r["usage_date"], r["command_name"], r["count"]) for r in rows]
    assert triples == [
        ("2026-04-29", "character", 1),
        ("2026-04-29", "enemy",     1),
        ("2026-04-25", "character", 1),
        ("2026-04-20", "character", 1),
    ]


def test_usage_in_window_empty_when_no_data(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    rows = repo.usage_in_window(conn, days=10, today="2026-04-29")
    conn.close()
    assert rows == []


def test_clear_tables_preserves_command_usage(tmp_db_path: Path) -> None:
    """The whole point of this table — counts must survive /refresh."""
    conn = repo.connect(tmp_db_path)
    repo.increment_command_usage(conn, "character", usage_date="2026-04-28")
    repo.increment_command_usage(conn, "enemy",     usage_date="2026-04-28")
    repo.clear_character_tables(conn)
    repo.clear_enemy_tables(conn)
    n = conn.execute("SELECT COUNT(*) FROM command_usage_daily").fetchone()[0]
    total = conn.execute("SELECT SUM(count) FROM command_usage_daily").fetchone()[0]
    conn.close()
    assert n == 2
    assert total == 2
