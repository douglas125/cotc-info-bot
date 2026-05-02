"""SQLite connection helpers, schema bootstrap, and high-level upsert/search APIs."""
from __future__ import annotations

import gzip
import json
import sqlite3
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from config import DATA_DIR, DB_PATH, SCHEMA_PATH


def _search_key(name: str | None) -> str:
    """Fold a name to a typing-friendly form for autocomplete/resolver.

    Mirrors the `unicode61 remove_diacritics 2` tokenizer used by FTS5:
      * NFKD splits accented letters into base + combining marks
      * combining marks are dropped, so 'Kainé?' → 'kaine?'
      * NFKC then re-composes (and folds fullwidth → halfwidth, e.g.
        '９Ｓ？' → '9s?')
      * casefold for case-insensitive matching

    The result is what the bot's `/enemy` autocomplete and exact-name
    resolver query against, and is also what the user's typed input is
    folded through, so a user typing 'kaine?' or '9s?' resolves the
    canonical 'Kainé?' / '９Ｓ？' rows.
    """
    if not name:
        return ""
    decomposed = unicodedata.normalize("NFKD", name)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return unicodedata.normalize("NFKC", stripped).casefold()


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(path: Path | None = None) -> sqlite3.Connection:
    target = Path(path) if path else DB_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def bootstrap(conn: sqlite3.Connection) -> None:
    """Apply schema.sql idempotently, then run any column-shape migrations.

    Order matters: pre-create migrations run BEFORE executescript so that
    older table shapes (e.g. raw_snapshots without `kind`) are upgraded
    before CREATE TABLE IF NOT EXISTS is a no-op against them.
    """
    _migrate_raw_snapshots_kind(conn)
    # Add new columns before executescript so the schema's CREATE INDEX
    # statements that reference them don't fail on legacy DBs.
    _migrate_enemies_search_key(conn)
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(sql)
    _migrate_skills_columns(conn)
    _migrate_sync_runs_enemy_counts(conn)
    _migrate_sync_runs_pets_count(conn)


def _ensure_columns(
    conn: sqlite3.Connection, table: str, pairs: tuple[tuple[str, str], ...],
) -> None:
    """Idempotently add missing columns to ``table``. No-op if the table
    doesn't exist yet (the schema CREATE will handle it)."""
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if not cols:
        return
    for col, decl in pairs:
        if col not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


def _migrate_skills_columns(conn: sqlite3.Connection) -> None:
    """In-place upgrade for older DBs whose `skills` table predates the
    learn_board / tier_level / initial_use / cooldown columns."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(skills)")}
    if cols and "boost_level" in cols and "learn_board" not in cols:
        conn.execute("ALTER TABLE skills RENAME COLUMN boost_level TO learn_board")
    _ensure_columns(conn, "skills", (
        ("learn_board",      "INTEGER"),
        ("tier_level",       "INTEGER"),
        ("initial_use",      "INTEGER"),
        ("cooldown",         "INTEGER"),
        ("max_uses",         "INTEGER"),
        ("unlock_condition", "TEXT"),
    ))

    eq_cols = {row[1] for row in conn.execute("PRAGMA table_info(equipment)")}
    if eq_cols and "is_exclusive" not in eq_cols:
        conn.execute("ALTER TABLE equipment ADD COLUMN is_exclusive INTEGER NOT NULL DEFAULT 0")


def _migrate_sync_runs_enemy_counts(conn: sqlite3.Connection) -> None:
    _ensure_columns(conn, "sync_runs", (
        ("enemies_count",     "INTEGER"),
        ("enemy_forms_count", "INTEGER"),
    ))


def _migrate_sync_runs_pets_count(conn: sqlite3.Connection) -> None:
    _ensure_columns(conn, "sync_runs", (
        ("pets_count", "INTEGER"),
    ))


def _migrate_enemies_search_key(conn: sqlite3.Connection) -> None:
    """Add `enemies.search_key` and backfill from existing canonical_name.

    Pre-migration DBs lack the column, so the autocomplete query would
    crash. Backfilling lets existing data answer accent/fullwidth folded
    queries before the next /refresh re-populates everything.
    """
    cols = {row[1] for row in conn.execute("PRAGMA table_info(enemies)")}
    if not cols or "search_key" in cols:
        return
    conn.execute("ALTER TABLE enemies ADD COLUMN search_key TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_enemies_search_key ON enemies(search_key)")
    for row in conn.execute("SELECT id, canonical_name FROM enemies").fetchall():
        conn.execute(
            "UPDATE enemies SET search_key = ? WHERE id = ?",
            (_search_key(row[1]), row[0]),
        )


def _migrate_raw_snapshots_kind(conn: sqlite3.Connection) -> None:
    """Add `kind` to raw_snapshots and switch the PK to (sync_run_id, kind).

    The original schema was `PRIMARY KEY (sync_run_id)` — i.e. one snapshot
    per run. The two-pipeline /refresh writes one row per kind, so the PK
    has to widen. SQLite can't ALTER a PK in place; we rebuild the table.
    """
    cols = {row[1] for row in conn.execute("PRAGMA table_info(raw_snapshots)")}
    if not cols or "kind" in cols:
        return
    conn.executescript("""
        ALTER TABLE raw_snapshots RENAME TO _raw_snapshots_old;
        CREATE TABLE raw_snapshots (
            sync_run_id  INTEGER NOT NULL REFERENCES sync_runs(id) ON DELETE CASCADE,
            kind         TEXT NOT NULL DEFAULT 'characters',
            payload_json BLOB NOT NULL,
            PRIMARY KEY (sync_run_id, kind)
        );
        INSERT INTO raw_snapshots(sync_run_id, kind, payload_json)
            SELECT sync_run_id, 'characters', payload_json FROM _raw_snapshots_old;
        DROP TABLE _raw_snapshots_old;
    """)


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[None]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


# --- sync run lifecycle -----------------------------------------------------

def start_sync_run(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        "INSERT INTO sync_runs(started_at, status) VALUES (?, 'running')",
        (_now_iso(),),
    )
    return cur.lastrowid


def finish_sync_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    status: str,
    error: str | None = None,
    forms_count: int | None = None,
    skills_count: int | None = None,
    enemies_count: int | None = None,
    enemy_forms_count: int | None = None,
    pets_count: int | None = None,
) -> None:
    conn.execute(
        "UPDATE sync_runs "
        "SET finished_at = ?, status = ?, error = ?, "
        "    forms_count = ?, skills_count = ?, "
        "    enemies_count = ?, enemy_forms_count = ?, "
        "    pets_count = ? "
        "WHERE id = ?",
        (_now_iso(), status, error, forms_count, skills_count,
         enemies_count, enemy_forms_count, pets_count, run_id),
    )


def store_raw_snapshot(conn: sqlite3.Connection, run_id: int, payload: dict[str, Any],
                       *, kind: str = "characters") -> None:
    blob = gzip.compress(json.dumps(payload).encode("utf-8"))
    conn.execute(
        "INSERT OR REPLACE INTO raw_snapshots(sync_run_id, kind, payload_json) VALUES (?, ?, ?)",
        (run_id, kind, blob),
    )


def latest_raw_snapshot(conn: sqlite3.Connection, *, kind: str = "characters") -> bytes | None:
    row = conn.execute(
        "SELECT payload_json FROM raw_snapshots "
        "WHERE kind = ? ORDER BY sync_run_id DESC LIMIT 1",
        (kind,),
    ).fetchone()
    return row[0] if row else None


def latest_sync_run(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM sync_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()


# --- destructive replace ----------------------------------------------------

def clear_character_tables(conn: sqlite3.Connection) -> None:
    """Wipe all character/skill/form data in dependency order. Keep sync history."""
    for tbl in (
        "characters_fts",
        "character_profile",
        "equipment_stats",
        "equipment",
        "skills",
        "character_affinities",
        "character_forms",
        "characters",
    ):
        conn.execute(f"DELETE FROM {tbl}")


def clear_enemy_tables(conn: sqlite3.Connection) -> None:
    """Wipe all enemy data in dependency order. Keep sync history."""
    for tbl in (
        "enemies_fts",
        "enemy_weaknesses",
        "enemy_member_stats",
        "enemy_forms",
        "enemies",
    ):
        conn.execute(f"DELETE FROM {tbl}")


def clear_pet_tables(conn: sqlite3.Connection) -> None:
    """Wipe all pet data in dependency order. Keep sync history.

    Intentionally narrow — does NOT touch character/enemy tables, sync
    history, feedback, or usage counters. Each /refresh re-parses the
    pet snapshot and rewrites these two tables.
    """
    for tbl in ("pets_fts", "pets"):
        conn.execute(f"DELETE FROM {tbl}")


# --- writers ----------------------------------------------------------------

def upsert_character(conn: sqlite3.Connection, canonical_name: str,
                     base_role: str | None, base_weapon: str | None) -> int:
    row = conn.execute(
        "SELECT id FROM characters WHERE canonical_name = ?", (canonical_name,)
    ).fetchone()
    if row:
        if base_role or base_weapon:
            conn.execute(
                "UPDATE characters SET base_role = COALESCE(?, base_role), "
                "base_weapon = COALESCE(?, base_weapon) WHERE id = ?",
                (base_role, base_weapon, row["id"]),
            )
        return row["id"]
    cur = conn.execute(
        "INSERT INTO characters(canonical_name, base_role, base_weapon) VALUES (?, ?, ?)",
        (canonical_name, base_role, base_weapon),
    )
    return cur.lastrowid


def insert_form(conn: sqlite3.Connection, *, character_id: int, display_name: str,
                rarity: str | None, variant_kind: str = "base", server: str = "global",
                level_cap: int | None = None, sheet_gid: int | None = None,
                source_row: int | None = None, name_color_hex: str | None = None,
                hyperlink_url: str | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO character_forms("
        "character_id, display_name, rarity, variant_kind, server, level_cap, "
        "sheet_gid, source_row, name_color_hex, hyperlink_url"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (character_id, display_name, rarity, variant_kind, server, level_cap,
         sheet_gid, source_row, name_color_hex, hyperlink_url),
    )
    return cur.lastrowid


def insert_affinities(conn: sqlite3.Connection, form_id: int,
                      items: Iterable[tuple[str, str | None, str | None]]) -> None:
    rows = [(form_id, k, lab, url) for k, lab, url in items]
    if rows:
        conn.executemany(
            "INSERT OR IGNORE INTO character_affinities(form_id, kind, icon_label, icon_url) "
            "VALUES (?, ?, ?, ?)",
            rows,
        )


def insert_skills(conn: sqlite3.Connection, form_id: int, skills: list[dict]) -> None:
    if not skills:
        return
    conn.executemany(
        "INSERT INTO skills("
        "form_id, slot_order, name, sp_cost, kind, learn_board, tier_level, "
        "initial_use, cooldown, description, power_min, power_max, hits, "
        "max_uses, unlock_condition"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (form_id, s.get("slot_order"), s.get("name"), s.get("sp_cost"),
             s.get("kind"), s.get("learn_board"), s.get("tier_level"),
             s.get("initial_use"), s.get("cooldown"), s.get("description"),
             s.get("power_min"), s.get("power_max"), s.get("hits"),
             s.get("max_uses"), s.get("unlock_condition"))
            for s in skills
        ],
    )


def insert_equipment(conn: sqlite3.Connection, form_id: int, items: list[dict]) -> None:
    if not items:
        return
    for e in items:
        cur = conn.execute(
            "INSERT INTO equipment(form_id, slot, name, description, is_exclusive) "
            "VALUES (?, ?, ?, ?, ?)",
            (form_id, e.get("slot"), e.get("name"), e.get("description"),
             1 if e.get("is_exclusive") else 0),
        )
        stats = e.get("stats") or []
        if stats:
            conn.executemany(
                "INSERT INTO equipment_stats(equipment_id, stat_name, stat_value, stat_order) "
                "VALUES (?, ?, ?, ?)",
                [(cur.lastrowid, name, value, order)
                 for order, (name, value) in enumerate(stats)],
            )


def upsert_profile(conn: sqlite3.Connection, form_id: int,
                   splash_art_url: str | None, self_buffs_text: str | None) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO character_profile(form_id, splash_art_url, self_buffs_text) "
        "VALUES (?, ?, ?)",
        (form_id, splash_art_url, self_buffs_text),
    )


def rebuild_fts(conn: sqlite3.Connection) -> None:
    """Repopulate the FTS index from the relational tables."""
    conn.execute("DELETE FROM characters_fts")
    conn.execute("""
        INSERT INTO characters_fts(form_id, canonical_name, display_name,
                                   skill_text, equipment_text)
        SELECT
            f.id,
            c.canonical_name,
            f.display_name,
            COALESCE((
                SELECT GROUP_CONCAT(
                    COALESCE(s.name,'') || ' ' || COALESCE(s.description,'') || ' ' || COALESCE(s.unlock_condition,''),
                    ' \n '
                )
                FROM skills s WHERE s.form_id = f.id
            ), ''),
            COALESCE((
                SELECT GROUP_CONCAT(COALESCE(e.name,'') || ' ' || COALESCE(e.description,''), ' \n ')
                FROM equipment e WHERE e.form_id = f.id
            ), '')
        FROM character_forms f JOIN characters c ON c.id = f.character_id
    """)


# --- read-side queries ------------------------------------------------------

def role_choices(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT base_role FROM characters WHERE base_role IS NOT NULL ORDER BY 1"
    )]


def weapon_choices(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT base_weapon FROM characters WHERE base_weapon IS NOT NULL ORDER BY 1"
    )]


def rarity_choices(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT rarity FROM character_forms WHERE rarity IS NOT NULL ORDER BY 1"
    )]


def affinity_choices(conn: sqlite3.Connection, kind: str) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT icon_label FROM character_affinities "
        "WHERE kind = ? AND icon_label IS NOT NULL ORDER BY 1",
        (kind,),
    )]


def search_forms(
    conn: sqlite3.Connection,
    *,
    roles: list[str] | None = None,
    weapons: list[str] | None = None,
    rarities: list[str] | None = None,
    weaknesses: list[str] | None = None,
    text: str | None = None,
    limit: int = 500,
) -> list[sqlite3.Row]:
    where = []
    params: list[Any] = []
    join_fts = ""
    if text and text.strip():
        join_fts = "JOIN characters_fts fts ON fts.form_id = f.id"
        where.append("characters_fts MATCH ?")
        params.append(_fts_query(text))
    if roles:
        where.append(f"c.base_role IN ({','.join(['?']*len(roles))})")
        params.extend(roles)
    if weapons:
        where.append(f"c.base_weapon IN ({','.join(['?']*len(weapons))})")
        params.extend(weapons)
    if rarities:
        where.append(f"f.rarity IN ({','.join(['?']*len(rarities))})")
        params.extend(rarities)
    if weaknesses:
        where.append(
            "f.id IN (SELECT form_id FROM character_affinities "
            f"WHERE kind = 'weakness' AND icon_label IN ({','.join(['?']*len(weaknesses))}))"
        )
        params.extend(weaknesses)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    sql = f"""
        SELECT f.id AS form_id, f.display_name, f.rarity, f.variant_kind, f.server,
               f.name_color_hex, f.hyperlink_url, f.sheet_gid, f.source_row,
               c.canonical_name, c.base_role, c.base_weapon
        FROM character_forms f
        JOIN characters c ON c.id = f.character_id
        {join_fts}
        {where_sql}
        ORDER BY c.base_role, f.rarity, c.canonical_name, f.variant_kind
        LIMIT ?
    """
    params.append(limit)
    return list(conn.execute(sql, params))


def _fts_query(s: str) -> str:
    """Sanitize free-text into an FTS5 prefix query."""
    parts = []
    for tok in s.split():
        clean = "".join(ch for ch in tok if ch.isalnum() or ch in "_-")
        if clean:
            parts.append(f'"{clean}"*')
    return " ".join(parts) if parts else '""'


def get_form(conn: sqlite3.Connection, form_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT f.*, c.canonical_name, c.base_role, c.base_weapon "
        "FROM character_forms f JOIN characters c ON c.id = f.character_id "
        "WHERE f.id = ?", (form_id,),
    ).fetchone()


def get_skills(conn: sqlite3.Connection, form_id: int) -> list[sqlite3.Row]:
    return list(conn.execute(
        "SELECT * FROM skills WHERE form_id = ? ORDER BY slot_order", (form_id,)
    ))


def get_affinities(conn: sqlite3.Connection, form_id: int) -> list[sqlite3.Row]:
    return list(conn.execute(
        "SELECT * FROM character_affinities WHERE form_id = ? ORDER BY kind, icon_label",
        (form_id,),
    ))


def skills_for_forms(
    conn: sqlite3.Connection, form_ids: list[int],
) -> list[sqlite3.Row]:
    """Batch-fetch skills for several forms in one query.

    Used by the team analyser, which pulls skills for up to 4 active
    members at once. Order is (form_id, slot_order) so callers can group
    by form_id without a second sort.
    """
    if not form_ids:
        return []
    placeholders = ",".join("?" * len(form_ids))
    return list(conn.execute(
        f"SELECT * FROM skills WHERE form_id IN ({placeholders}) "
        f"ORDER BY form_id, slot_order",
        form_ids,
    ))


def equipment_for_forms(
    conn: sqlite3.Connection, form_ids: list[int],
) -> list[sqlite3.Row]:
    """Batch-fetch A4 equipment for several forms in one query."""
    if not form_ids:
        return []
    placeholders = ",".join("?" * len(form_ids))
    return list(conn.execute(
        f"SELECT * FROM equipment WHERE form_id IN ({placeholders}) "
        f"ORDER BY form_id, id",
        form_ids,
    ))


def affinities_for_forms(
    conn: sqlite3.Connection, form_ids: list[int],
) -> list[sqlite3.Row]:
    """Batch-fetch affinities (weapon/element/weakness/trait) for several forms."""
    if not form_ids:
        return []
    placeholders = ",".join("?" * len(form_ids))
    return list(conn.execute(
        f"SELECT * FROM character_affinities WHERE form_id IN ({placeholders}) "
        f"ORDER BY form_id, kind, icon_label",
        form_ids,
    ))


def get_equipment(conn: sqlite3.Connection, form_id: int) -> list[sqlite3.Row]:
    return list(conn.execute(
        "SELECT * FROM equipment WHERE form_id = ? ORDER BY id", (form_id,)
    ))


def get_equipment_stats_by_form(
    conn: sqlite3.Connection, form_id: int,
) -> dict[int, list[sqlite3.Row]]:
    """Return ``{equipment_id: [stat_row, ...]}`` for one form, ordered by stat_order."""
    rows = conn.execute(
        "SELECT es.equipment_id, es.stat_name, es.stat_value, es.stat_order "
        "FROM equipment_stats es "
        "JOIN equipment e ON e.id = es.equipment_id "
        "WHERE e.form_id = ? "
        "ORDER BY es.equipment_id, es.stat_order",
        (form_id,),
    ).fetchall()
    out: dict[int, list[sqlite3.Row]] = {}
    for r in rows:
        out.setdefault(r["equipment_id"], []).append(r)
    return out


def get_profile(conn: sqlite3.Connection, form_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM character_profile WHERE form_id = ?", (form_id,)
    ).fetchone()


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    out = {}
    for tbl in ("characters", "character_forms", "skills", "equipment",
                "equipment_stats", "character_affinities",
                "enemies", "enemy_forms", "enemy_member_stats", "enemy_weaknesses",
                "pets"):
        out[tbl] = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
    return out


# --- enemy writers ----------------------------------------------------------

def upsert_enemy(
    conn: sqlite3.Connection,
    *,
    canonical_name: str,
    category: str,
    region: str | None,
    sheet_gid: int | None,
    source_row: int | None,
    name_color_hex: str | None,
    hyperlink_url: str | None,
    is_npc: bool,
) -> int:
    skey = _search_key(canonical_name)
    row = conn.execute(
        "SELECT id FROM enemies WHERE canonical_name = ? AND category = ? "
        "AND COALESCE(sheet_gid, -1) = COALESCE(?, -1)",
        (canonical_name, category, sheet_gid),
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE enemies SET region = ?, source_row = ?, "
            "name_color_hex = ?, hyperlink_url = ?, is_npc = ?, search_key = ? "
            "WHERE id = ?",
            (region, source_row, name_color_hex, hyperlink_url, int(is_npc),
             skey, row[0]),
        )
        return row[0]
    cur = conn.execute(
        "INSERT INTO enemies(canonical_name, category, region, sheet_gid, "
        "source_row, name_color_hex, hyperlink_url, is_npc, search_key) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (canonical_name, category, region, sheet_gid, source_row,
         name_color_hex, hyperlink_url, int(is_npc), skey),
    )
    return cur.lastrowid


def insert_enemy_form(
    conn: sqlite3.Connection,
    *,
    enemy_id: int,
    rank: str,
    rank_order: int,
) -> int:
    cur = conn.execute(
        "INSERT INTO enemy_forms(enemy_id, rank, rank_order) VALUES (?, ?, ?)",
        (enemy_id, rank, rank_order),
    )
    return cur.lastrowid


def insert_enemy_member_stats(
    conn: sqlite3.Connection,
    form_id: int,
    rows: Iterable[dict[str, Any]],
) -> None:
    """Bulk insert. Each row dict needs: position, member_name, stat_name, stat_value."""
    conn.executemany(
        "INSERT INTO enemy_member_stats(form_id, position, member_name, "
        "stat_name, stat_value) VALUES (?, ?, ?, ?, ?)",
        [
            (form_id, r["position"], r.get("member_name"),
             r["stat_name"], r["stat_value"])
            for r in rows
        ],
    )


def insert_enemy_weaknesses(
    conn: sqlite3.Connection,
    form_id: int,
    weaknesses_by_position: list[list[str]],
) -> None:
    """Bulk insert. `weaknesses_by_position[i]` = ['Sword', 'Wind', ...] in slot order."""
    rows = [
        (form_id, pos, label, slot)
        for pos, labels in enumerate(weaknesses_by_position)
        for slot, label in enumerate(labels)
    ]
    if not rows:
        return
    conn.executemany(
        "INSERT INTO enemy_weaknesses(form_id, position, weakness_label, slot_order) "
        "VALUES (?, ?, ?, ?)",
        rows,
    )


def rebuild_enemy_fts(conn: sqlite3.Connection) -> None:
    """Repopulate the enemy FTS index from the relational tables."""
    conn.execute("DELETE FROM enemies_fts")
    conn.execute("""
        INSERT INTO enemies_fts(enemy_id, canonical_name, category, member_names)
        SELECT
            e.id,
            e.canonical_name,
            e.category,
            COALESCE((
                SELECT GROUP_CONCAT(DISTINCT s.member_name)
                FROM enemy_forms f
                JOIN enemy_member_stats s ON s.form_id = f.id
                WHERE f.enemy_id = e.id AND s.member_name IS NOT NULL
            ), '')
        FROM enemies e
    """)


# --- enemy reads ------------------------------------------------------------

def get_enemy(conn: sqlite3.Connection, enemy_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM enemies WHERE id = ?", (enemy_id,)
    ).fetchone()


def get_enemy_forms(conn: sqlite3.Connection, enemy_id: int) -> list[sqlite3.Row]:
    return list(conn.execute(
        "SELECT * FROM enemy_forms WHERE enemy_id = ? ORDER BY rank_order",
        (enemy_id,),
    ))


def get_enemy_form_by_rank(
    conn: sqlite3.Connection, enemy_id: int, rank: str,
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM enemy_forms WHERE enemy_id = ? AND rank = ?",
        (enemy_id, rank),
    ).fetchone()


def get_enemy_member_stats(
    conn: sqlite3.Connection, form_id: int,
) -> list[sqlite3.Row]:
    """Return rows ordered by encounter position then a stable stat order."""
    return list(conn.execute(
        "SELECT position, member_name, stat_name, stat_value "
        "FROM enemy_member_stats WHERE form_id = ? "
        "ORDER BY position, id",
        (form_id,),
    ))


def get_enemy_weaknesses(
    conn: sqlite3.Connection, form_id: int,
) -> list[sqlite3.Row]:
    """Return weakness rows ordered by (position, slot_order)."""
    return list(conn.execute(
        "SELECT position, weakness_label, slot_order "
        "FROM enemy_weaknesses WHERE form_id = ? "
        "ORDER BY position, slot_order",
        (form_id,),
    ))


def enemy_choices_by_name(
    conn: sqlite3.Connection, current: str, limit: int,
) -> list[sqlite3.Row]:
    """Autocomplete source. Returns enemies whose name matches `current`,
    prefix matches first, then substring matches. Both the user's input
    and the stored canonical_name are NFKC + accent-folded so users can
    type 'Kaine?' to match 'Kainé?' and '9S?' to match '９Ｓ？'."""
    needle = _search_key((current or "").strip())
    if not needle:
        return list(conn.execute(
            "SELECT id AS enemy_id, canonical_name, category, region, is_npc "
            "FROM enemies ORDER BY canonical_name LIMIT ?",
            (limit,),
        ))
    sql = """
        SELECT id AS enemy_id, canonical_name, category, region, is_npc
        FROM enemies
        WHERE search_key LIKE ?
        ORDER BY
            CASE WHEN search_key LIKE ? THEN 0 ELSE 1 END,
            canonical_name
        LIMIT ?
    """
    return list(conn.execute(sql, (f"%{needle}%", f"{needle}%", limit)))


def search_enemies(
    conn: sqlite3.Connection,
    *,
    category: str | None = None,
    text: str | None = None,
    is_npc: bool | None = None,
    limit: int = 200,
) -> list[sqlite3.Row]:
    where: list[str] = []
    params: list[Any] = []
    join_fts = ""
    if text and text.strip():
        join_fts = "JOIN enemies_fts fts ON fts.enemy_id = e.id"
        where.append("enemies_fts MATCH ?")
        params.append(_fts_query(text))
    if category:
        where.append("e.category = ?")
        params.append(category)
    if is_npc is not None:
        where.append("e.is_npc = ?")
        params.append(int(is_npc))
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    sql = f"""
        SELECT e.id AS enemy_id, e.canonical_name, e.category, e.region,
               e.name_color_hex, e.hyperlink_url, e.sheet_gid, e.is_npc
        FROM enemies e
        {join_fts}
        {where_sql}
        ORDER BY e.category, e.canonical_name
        LIMIT ?
    """
    params.append(limit)
    return list(conn.execute(sql, params))


def enemy_categories(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT category FROM enemies ORDER BY 1"
    )]


# --- feedback (community-submitted corrections) -----------------------------

def insert_feedback(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    username: str,
    guild_id: int | None,
    feedback_text: str,
) -> int:
    cur = conn.execute(
        "INSERT INTO feedback_submissions("
        "submitted_at, user_id, username, guild_id, feedback_text"
        ") VALUES (?, ?, ?, ?, ?)",
        (_now_iso(), user_id, username, guild_id, feedback_text),
    )
    return cur.lastrowid


def list_feedback(conn: sqlite3.Connection, limit: int = 25) -> list[sqlite3.Row]:
    return list(conn.execute(
        "SELECT id, submitted_at, user_id, username, guild_id, feedback_text "
        "FROM feedback_submissions ORDER BY submitted_at DESC, id DESC LIMIT ?",
        (limit,),
    ))


def clear_feedback(conn: sqlite3.Connection) -> int:
    cur = conn.execute("DELETE FROM feedback_submissions")
    return cur.rowcount


def count_feedback(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM feedback_submissions").fetchone()[0]


def recent_feedback_timestamps(
    conn: sqlite3.Connection, user_id: int, since_iso: str, *, limit: int,
) -> list[str]:
    """Timestamps of this user's submissions newer than `since_iso`, newest first.

    Bounded by `limit` so a spammy user can't force an unbounded read — the
    rate-limit caller only needs to know whether the window is full."""
    return [r[0] for r in conn.execute(
        "SELECT submitted_at FROM feedback_submissions "
        "WHERE user_id = ? AND submitted_at > ? "
        "ORDER BY submitted_at DESC LIMIT ?",
        (user_id, since_iso, limit),
    )]


# --- command usage telemetry ------------------------------------------------

def increment_command_usage(
    conn: sqlite3.Connection,
    command_name: str,
    *,
    usage_date: str | None = None,
) -> None:
    """Bump the (command_name, today-UTC) counter by 1.

    `usage_date` override exists only for tests."""
    date = usage_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    conn.execute(
        "INSERT INTO command_usage_daily(command_name, usage_date, count) "
        "VALUES (?, ?, 1) "
        "ON CONFLICT(command_name, usage_date) "
        "DO UPDATE SET count = count + 1",
        (command_name, date),
    )


def usage_in_window(
    conn: sqlite3.Connection,
    *,
    days: int = 10,
    today: str | None = None,
) -> list[sqlite3.Row]:
    """Per-(usage_date, command_name) rows for the trailing `days` days (UTC, inclusive).

    Newest day first, then alphabetic by command. `today` override is for tests."""
    end = today or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    start = (
        datetime.strptime(end, "%Y-%m-%d") - timedelta(days=days - 1)
    ).strftime("%Y-%m-%d")
    return list(conn.execute(
        "SELECT usage_date, command_name, count "
        "FROM command_usage_daily "
        "WHERE usage_date >= ? AND usage_date <= ? "
        "ORDER BY usage_date DESC, command_name",
        (start, end),
    ))


# --- pet writers/readers ----------------------------------------------------

_PET_INSERT_COLUMNS = (
    "canonical_name", "display_name_jp", "source_text", "ability_text",
    "max_boost", "prep_base", "prep_lv10", "cooldown_base", "cooldown_lv5",
    "hp", "sp", "patk", "pdef", "matk", "mdef", "crit", "speed",
    "sheet_gid", "source_row", "name_color_hex", "hyperlink_url",
)


def _pet_value(pet: Any, column: str) -> Any:
    """Read a pet column from either a dataclass or a plain dict."""
    if isinstance(pet, dict):
        return pet.get(column)
    return getattr(pet, column, None)


def upsert_pet(conn: sqlite3.Connection, pet: Any) -> int:
    """Insert or update one pet keyed by (canonical_name, source_row).

    `pet` is either a `sync.pet_parsers.ParsedPet` dataclass (the runner
    path) or a plain dict (test fixtures). Both shapes carry every
    column listed in `_PET_INSERT_COLUMNS`. The UNIQUE constraint lets
    two pets share an English name as long as their source rows differ
    — the documented "White Rabbit" collision.
    """
    placeholders = ", ".join("?" for _ in _PET_INSERT_COLUMNS)
    columns = ", ".join(_PET_INSERT_COLUMNS)
    values = tuple(_pet_value(pet, c) for c in _PET_INSERT_COLUMNS)
    row = conn.execute(
        "SELECT id FROM pets WHERE canonical_name = ? AND "
        "COALESCE(source_row, -1) = COALESCE(?, -1)",
        (_pet_value(pet, "canonical_name"), _pet_value(pet, "source_row")),
    ).fetchone()
    if row:
        update_cols = ", ".join(f"{c} = ?" for c in _PET_INSERT_COLUMNS)
        conn.execute(
            f"UPDATE pets SET {update_cols} WHERE id = ?",
            values + (row[0],),
        )
        return row[0]
    cur = conn.execute(
        f"INSERT INTO pets({columns}) VALUES ({placeholders})",
        values,
    )
    return cur.lastrowid


def rebuild_pet_fts(conn: sqlite3.Connection) -> None:
    """Repopulate the pet FTS index from the relational table."""
    conn.execute("DELETE FROM pets_fts")
    conn.execute(
        "INSERT INTO pets_fts(pet_id, canonical_name, display_name_jp, "
        "ability_text, source_text) "
        "SELECT id, canonical_name, COALESCE(display_name_jp, ''), "
        "COALESCE(ability_text, ''), COALESCE(source_text, '') FROM pets"
    )


def get_pet(conn: sqlite3.Connection, pet_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM pets WHERE id = ?", (pet_id,)).fetchone()


def pet_choices_by_name(
    conn: sqlite3.Connection, current: str, limit: int,
) -> list[sqlite3.Row]:
    """Autocomplete source. Returns pets whose canonical_name matches `current`,
    prefix matches first, then substring matches. Multiple rows may share a
    name (different `source_row`); both are returned and the bot disambiguates
    via the source-text hint at label-time."""
    needle = (current or "").strip().lower()
    if not needle:
        return list(conn.execute(
            "SELECT id AS pet_id, canonical_name, source_text, source_row "
            "FROM pets ORDER BY canonical_name, source_row LIMIT ?",
            (limit,),
        ))
    sql = """
        SELECT id AS pet_id, canonical_name, source_text, source_row
        FROM pets
        WHERE LOWER(canonical_name) LIKE ?
        ORDER BY
            CASE WHEN LOWER(canonical_name) LIKE ? THEN 0 ELSE 1 END,
            canonical_name, source_row
        LIMIT ?
    """
    return list(conn.execute(sql, (f"%{needle}%", f"{needle}%", limit)))


def search_pets(
    conn: sqlite3.Connection,
    *,
    text: str | None = None,
    limit: int = 200,
) -> list[sqlite3.Row]:
    where: list[str] = []
    params: list[Any] = []
    join_fts = ""
    if text and text.strip():
        join_fts = "JOIN pets_fts fts ON fts.pet_id = p.id"
        where.append("pets_fts MATCH ?")
        params.append(_fts_query(text))
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    sql = f"""
        SELECT p.id AS pet_id, p.canonical_name, p.source_text, p.source_row
        FROM pets p
        {join_fts}
        {where_sql}
        ORDER BY p.canonical_name, p.source_row
        LIMIT ?
    """
    params.append(limit)
    return list(conn.execute(sql, params))
