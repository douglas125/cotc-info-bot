"""Round-trip tests for the enemy repo functions."""
from __future__ import annotations

from pathlib import Path

from db import repo


def _seed_lloris(conn) -> int:
    enemy_id = repo.upsert_enemy(
        conn,
        canonical_name="Sly Leader Lloris",
        category="Solistia Lvl 25",
        region="Solistia",
        sheet_gid=795720982,
        source_row=3,
        name_color_hex="#ffffff",
        hyperlink_url="#gid=795720982&range=B4",
        is_npc=False,
    )
    for rank, rank_order in (("Rank1", 1), ("EX3", 6)):
        form_id = repo.insert_enemy_form(
            conn, enemy_id=enemy_id, rank=rank, rank_order=rank_order,
        )
        repo.insert_enemy_member_stats(conn, form_id, [
            {"position": 0, "member_name": "Leader Lloris",
             "stat_name": "HP", "stat_value": "1143210" if rank == "EX3" else "11029"},
            {"position": 0, "member_name": "Leader Lloris",
             "stat_name": "Shields", "stat_value": "30" if rank == "EX3" else "16"},
            {"position": 1, "member_name": "Mini Lloris",
             "stat_name": "HP", "stat_value": "822762" if rank == "EX3" else "8588"},
            {"position": 1, "member_name": "Mini Lloris",
             "stat_name": "Shields", "stat_value": "18" if rank == "EX3" else "11"},
        ])
    return enemy_id


def test_upsert_enemy_is_idempotent(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    a = repo.upsert_enemy(
        conn, canonical_name="Dokabro", category="Solistia Lvl 1",
        region="Solistia", sheet_gid=1, source_row=3,
        name_color_hex=None, hyperlink_url=None, is_npc=False,
    )
    b = repo.upsert_enemy(
        conn, canonical_name="Dokabro", category="Solistia Lvl 1",
        region="Solistia", sheet_gid=1, source_row=3,
        name_color_hex="#abcdef", hyperlink_url="#gid=1&range=A1", is_npc=False,
    )
    assert a == b
    row = repo.get_enemy(conn, a)
    assert row["name_color_hex"] == "#abcdef"
    assert row["hyperlink_url"] == "#gid=1&range=A1"
    conn.close()


def test_get_enemy_forms_orders_by_rank_order(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    enemy_id = _seed_lloris(conn)
    forms = repo.get_enemy_forms(conn, enemy_id)
    assert [f["rank"] for f in forms] == ["Rank1", "EX3"]
    conn.close()


def test_get_enemy_form_by_rank(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    enemy_id = _seed_lloris(conn)
    f = repo.get_enemy_form_by_rank(conn, enemy_id, "EX3")
    assert f is not None
    assert f["rank_order"] == 6
    assert repo.get_enemy_form_by_rank(conn, enemy_id, "EX2") is None
    conn.close()


def test_get_enemy_member_stats_groups_by_position(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    enemy_id = _seed_lloris(conn)
    f = repo.get_enemy_form_by_rank(conn, enemy_id, "EX3")
    rows = repo.get_enemy_member_stats(conn, f["id"])
    by_pos = {(r["position"], r["stat_name"]): r["stat_value"] for r in rows}
    assert by_pos[(0, "HP")] == "1143210"
    assert by_pos[(1, "HP")] == "822762"
    assert by_pos[(0, "Shields")] == "30"
    assert by_pos[(1, "Shields")] == "18"
    conn.close()


def test_search_enemies_text_uses_fts(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    _seed_lloris(conn)
    repo.rebuild_enemy_fts(conn)
    rows = repo.search_enemies(conn, text="Lloris")
    assert len(rows) == 1
    assert rows[0]["canonical_name"] == "Sly Leader Lloris"
    conn.close()


def test_enemy_choices_by_name_prefix_first(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    repo.upsert_enemy(
        conn, canonical_name="Sly Leader Lloris", category="Solistia Lvl 25",
        region="Solistia", sheet_gid=1, source_row=3,
        name_color_hex=None, hyperlink_url=None, is_npc=False,
    )
    repo.upsert_enemy(
        conn, canonical_name="Mini Lloris", category="Solistia Lvl 25",
        region="Solistia", sheet_gid=1, source_row=10,
        name_color_hex=None, hyperlink_url=None, is_npc=False,
    )
    rows = repo.enemy_choices_by_name(conn, "Sly", limit=10)
    assert len(rows) == 1
    assert rows[0]["canonical_name"] == "Sly Leader Lloris"
    rows = repo.enemy_choices_by_name(conn, "Lloris", limit=10)
    assert {r["canonical_name"] for r in rows} == {"Sly Leader Lloris", "Mini Lloris"}
    conn.close()


def test_insert_and_get_enemy_weaknesses(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    enemy_id = _seed_lloris(conn)
    form = repo.get_enemy_form_by_rank(conn, enemy_id, "EX3")
    repo.insert_enemy_weaknesses(conn, form["id"], [
        ["Axe", "Bow", "Ice", "Wind", "Dark"],
        ["Dagger", "Bow", "Ice", "Lightning", "Dark"],
    ])
    rows = repo.get_enemy_weaknesses(conn, form["id"])
    by_pos: dict[int, list[str]] = {}
    for r in rows:
        by_pos.setdefault(r["position"], []).append(r["weakness_label"])
    assert by_pos[0] == ["Axe", "Bow", "Ice", "Wind", "Dark"]
    assert by_pos[1] == ["Dagger", "Bow", "Ice", "Lightning", "Dark"]
    # Slot order is stable.
    pos0_slots = [r["slot_order"] for r in rows if r["position"] == 0]
    assert pos0_slots == [0, 1, 2, 3, 4]
    conn.close()


def test_insert_enemy_weaknesses_handles_empty(tmp_db_path: Path) -> None:
    """Empty weaknesses (e.g. NPCs) should be a no-op, not an error."""
    conn = repo.connect(tmp_db_path)
    enemy_id = _seed_lloris(conn)
    form = repo.get_enemy_form_by_rank(conn, enemy_id, "EX3")
    repo.insert_enemy_weaknesses(conn, form["id"], [])
    assert repo.get_enemy_weaknesses(conn, form["id"]) == []
    conn.close()


def test_clear_enemy_tables_leaves_characters_intact(tmp_db_path: Path) -> None:
    """Regression: splitting clear_data_tables means clear_enemy_tables MUST NOT
    wipe the character side, and vice versa."""
    conn = repo.connect(tmp_db_path)
    # Seed one of each kind.
    char_id = repo.upsert_character(conn, "Castti", "apothecary", "axe")
    repo.insert_form(
        conn, character_id=char_id, display_name="Castti", rarity="5*",
        sheet_gid=1, source_row=5,
    )
    _seed_lloris(conn)

    repo.clear_enemy_tables(conn)
    assert conn.execute("SELECT COUNT(*) FROM enemies").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM enemy_forms").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM enemy_member_stats").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM enemy_weaknesses").fetchone()[0] == 0
    # Characters intact.
    assert conn.execute("SELECT COUNT(*) FROM characters").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM character_forms").fetchone()[0] == 1
    conn.close()


def test_raw_snapshot_kind_separates_character_from_enemy(tmp_db_path: Path) -> None:
    """A single sync_run can carry one snapshot per kind."""
    conn = repo.connect(tmp_db_path)
    run_id = repo.start_sync_run(conn)
    repo.store_raw_snapshot(conn, run_id, {"kind": "char"}, kind="characters")
    repo.store_raw_snapshot(conn, run_id, {"kind": "enemy"}, kind="enemies")
    rows = list(conn.execute(
        "SELECT kind FROM raw_snapshots WHERE sync_run_id = ? ORDER BY kind",
        (run_id,),
    ))
    assert [r[0] for r in rows] == ["characters", "enemies"]
    char_blob = repo.latest_raw_snapshot(conn, kind="characters")
    enemy_blob = repo.latest_raw_snapshot(conn, kind="enemies")
    assert char_blob is not None
    assert enemy_blob is not None
    assert char_blob != enemy_blob
    conn.close()
