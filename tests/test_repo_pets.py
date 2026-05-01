"""Round-trip tests for the pet repo functions."""
from __future__ import annotations

from pathlib import Path

from db import repo


def _white_rabbit_quest() -> dict:
    return {
        "canonical_name":  "White Rabbit",
        "display_name_jp": "ウサギ 白 (White Rabbit)",
        "source_text":     "Quest",
        "ability_text":    "AoE Taunt 3-4T",
        "max_boost":       "Lv2",
        "prep_base":       1, "prep_lv10": 0,
        "cooldown_base":   9, "cooldown_lv5": 8,
        "hp": 140, "sp": 21, "patk": 5, "pdef": 12,
        "matk": 29, "mdef": 22, "crit": 19, "speed": 38,
        "sheet_gid": 243040141, "source_row": 77,
        "name_color_hex": None, "hyperlink_url": "#gid=243040141&range=A78",
    }


def _white_rabbit_login() -> dict:
    return {
        "canonical_name":  "White Rabbit",
        "display_name_jp": "白 (White Rabbit)",
        "source_text":     "New Year 2023 Login (JP)\n(Until Jan 31st 2023)\n\n1.5th Anni Login (EN)",
        "ability_text":    "Grant owner Evade Magic (1-2 hits)",
        "max_boost":       "Lv2",
        "prep_base":       2, "prep_lv10": 1,
        "cooldown_base":   5, "cooldown_lv5": 4,
        "hp": 120, "sp": 22, "patk": 13, "pdef": 11,
        "matk": 13, "mdef": 11, "crit": 28, "speed": 50,
        "sheet_gid": 243040141, "source_row": 189,
        "name_color_hex": None, "hyperlink_url": "#gid=243040141&range=A190",
    }


def _red_brown_cat() -> dict:
    return {
        "canonical_name":  "Red Brown Cat",
        "display_name_jp": "赤茶 (Red Brown Cat)",
        "source_text":     "Quest\n\nBeat the Titan Tower F3",
        "ability_text":    "Grant owner 30% Patk/Matk Up 1T",
        "max_boost":       None,
        "prep_base":       13, "prep_lv10": 12,
        "cooldown_base":   13, "cooldown_lv5": 12,
        "hp": 300, "sp": 11, "patk": 23, "pdef": 20,
        "matk": 24, "mdef": 22, "crit": 15, "speed": 15,
        "sheet_gid": 243040141, "source_row": 25,
        "name_color_hex": None, "hyperlink_url": "#gid=243040141&range=A26",
    }


def test_upsert_pet_is_idempotent_on_name_and_row(tmp_db_path: Path) -> None:
    """Same (canonical_name, source_row) → update; ID stays stable."""
    conn = repo.connect(tmp_db_path)
    pet = _red_brown_cat()
    a = repo.upsert_pet(conn, pet)
    pet["hp"] = 999  # mutated value should overwrite the row
    b = repo.upsert_pet(conn, pet)
    assert a == b
    row = repo.get_pet(conn, a)
    assert row["hp"] == 999
    conn.close()


def test_two_white_rabbits_distinct_rows(tmp_db_path: Path) -> None:
    """Same English name on different source rows → two distinct pets."""
    conn = repo.connect(tmp_db_path)
    quest_id = repo.upsert_pet(conn, _white_rabbit_quest())
    login_id = repo.upsert_pet(conn, _white_rabbit_login())
    assert quest_id != login_id

    rows = list(conn.execute(
        "SELECT id, source_row, source_text FROM pets "
        "WHERE canonical_name = 'White Rabbit' ORDER BY source_row"
    ))
    assert [r["source_row"] for r in rows] == [77, 189]
    assert "Quest" in rows[0]["source_text"]
    assert "Login" in rows[1]["source_text"]
    conn.close()


def test_clear_pet_tables_does_not_touch_others(tmp_db_path: Path) -> None:
    """Wiping pets must leave characters/enemies/sync_runs intact."""
    conn = repo.connect(tmp_db_path)
    # Seed minimal data in each pipeline.
    repo.upsert_pet(conn, _red_brown_cat())
    char_id = repo.upsert_character(
        conn, canonical_name="Cyrus", base_role="scholar", base_weapon="tome",
    )
    repo.insert_form(
        conn, character_id=char_id, display_name="Cyrus",
        rarity="5*", variant_kind="base", server="global",
    )
    enemy_id = repo.upsert_enemy(
        conn, canonical_name="Dokabro", category="Solistia Lvl 1",
        region="Solistia", sheet_gid=1, source_row=3,
        name_color_hex=None, hyperlink_url=None, is_npc=False,
    )
    run_id = repo.start_sync_run(conn)
    repo.finish_sync_run(conn, run_id, status="ok", forms_count=1)

    repo.clear_pet_tables(conn)

    assert conn.execute("SELECT COUNT(*) FROM pets").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM pets_fts").fetchone()[0] == 0
    # Characters / enemies / sync history must survive.
    assert conn.execute("SELECT COUNT(*) FROM characters").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM character_forms").fetchone()[0] == 1
    assert conn.execute("SELECT id FROM enemies").fetchone()[0] == enemy_id
    assert conn.execute("SELECT COUNT(*) FROM sync_runs").fetchone()[0] == 1
    conn.close()


def test_pet_choices_prefix_then_substring(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    for p in (_red_brown_cat(),
              _white_rabbit_quest(), _white_rabbit_login(),
              {**_red_brown_cat(),
               "canonical_name": "Brown Cat",
               "source_row": 13}):
        repo.upsert_pet(conn, p)

    # "white" → both White Rabbit rows (prefix match), Brown Cat doesn't match.
    rows = repo.pet_choices_by_name(conn, "white", limit=10)
    assert all(r["canonical_name"].lower().startswith("white") for r in rows)
    assert len(rows) == 2

    # "rab" → both White Rabbits (substring).
    rows = repo.pet_choices_by_name(conn, "rab", limit=10)
    assert {r["canonical_name"] for r in rows} == {"White Rabbit"}
    assert len(rows) == 2
    conn.close()


def test_rebuild_pet_fts_finds_ability_term(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    repo.upsert_pet(conn, _red_brown_cat())
    repo.upsert_pet(conn, _white_rabbit_quest())
    repo.rebuild_pet_fts(conn)

    rows = list(conn.execute(
        "SELECT pet_id FROM pets_fts WHERE pets_fts MATCH ?", ('"Taunt"*',)
    ))
    assert len(rows) == 1
    rows = list(conn.execute(
        "SELECT pet_id FROM pets_fts WHERE pets_fts MATCH ?", ('"Patk"*',)
    ))
    assert len(rows) == 1
    conn.close()


def test_search_pets_filters_by_text(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    repo.upsert_pet(conn, _red_brown_cat())
    repo.upsert_pet(conn, _white_rabbit_quest())
    repo.rebuild_pet_fts(conn)

    rows = repo.search_pets(conn, text="Patk")
    assert {r["canonical_name"] for r in rows} == {"Red Brown Cat"}

    rows = repo.search_pets(conn, text="Taunt")
    assert {r["canonical_name"] for r in rows} == {"White Rabbit"}

    rows = repo.search_pets(conn, text=None)
    assert {r["canonical_name"] for r in rows} == {"Red Brown Cat", "White Rabbit"}
    conn.close()


def test_finish_sync_run_records_pets_count(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    run_id = repo.start_sync_run(conn)
    repo.finish_sync_run(
        conn, run_id, status="ok",
        forms_count=10, skills_count=20,
        enemies_count=5, enemy_forms_count=15,
        pets_count=42,
    )
    row = repo.latest_sync_run(conn)
    assert row["pets_count"] == 42
    conn.close()


def test_counts_includes_pets(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    repo.upsert_pet(conn, _red_brown_cat())
    repo.upsert_pet(conn, _white_rabbit_quest())
    c = repo.counts(conn)
    assert c["pets"] == 2
    conn.close()
