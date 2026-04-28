"""SQLite connection helpers, schema bootstrap, and high-level upsert/search APIs."""
from __future__ import annotations

import gzip
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from config import DATA_DIR, DB_PATH, SCHEMA_PATH


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
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(sql)
    _migrate_skills_columns(conn)
    _migrate_sync_runs_enemy_counts(conn)


def _migrate_skills_columns(conn: sqlite3.Connection) -> None:
    """In-place upgrade for older DBs whose `skills` table predates the
    learn_board / tier_level / initial_use / cooldown columns."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(skills)")}
    if cols:
        if "boost_level" in cols and "learn_board" not in cols:
            conn.execute("ALTER TABLE skills RENAME COLUMN boost_level TO learn_board")
            cols = (cols - {"boost_level"}) | {"learn_board"}
        for col, decl in (
            ("learn_board", "INTEGER"),
            ("tier_level",  "INTEGER"),
            ("initial_use", "INTEGER"),
            ("cooldown",    "INTEGER"),
        ):
            if col not in cols:
                conn.execute(f"ALTER TABLE skills ADD COLUMN {col} {decl}")

    eq_cols = {row[1] for row in conn.execute("PRAGMA table_info(equipment)")}
    if eq_cols and "is_exclusive" not in eq_cols:
        conn.execute("ALTER TABLE equipment ADD COLUMN is_exclusive INTEGER NOT NULL DEFAULT 0")


def _migrate_sync_runs_enemy_counts(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(sync_runs)")}
    if not cols:
        return
    for col in ("enemies_count", "enemy_forms_count"):
        if col not in cols:
            conn.execute(f"ALTER TABLE sync_runs ADD COLUMN {col} INTEGER")


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
) -> None:
    conn.execute(
        "UPDATE sync_runs "
        "SET finished_at = ?, status = ?, error = ?, "
        "    forms_count = ?, skills_count = ?, "
        "    enemies_count = ?, enemy_forms_count = ? "
        "WHERE id = ?",
        (_now_iso(), status, error, forms_count, skills_count,
         enemies_count, enemy_forms_count, run_id),
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
        "initial_use, cooldown, description, power_min, power_max, hits"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (form_id, s.get("slot_order"), s.get("name"), s.get("sp_cost"),
             s.get("kind"), s.get("learn_board"), s.get("tier_level"),
             s.get("initial_use"), s.get("cooldown"), s.get("description"),
             s.get("power_min"), s.get("power_max"), s.get("hits"))
            for s in skills
        ],
    )


def insert_equipment(conn: sqlite3.Connection, form_id: int, items: list[dict]) -> None:
    if not items:
        return
    conn.executemany(
        "INSERT INTO equipment(form_id, slot, name, description, is_exclusive) "
        "VALUES (?, ?, ?, ?, ?)",
        [(form_id, e.get("slot"), e.get("name"), e.get("description"),
          1 if e.get("is_exclusive") else 0) for e in items],
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
                SELECT GROUP_CONCAT(COALESCE(s.name,'') || ' ' || COALESCE(s.description,''), ' \n ')
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


def get_equipment(conn: sqlite3.Connection, form_id: int) -> list[sqlite3.Row]:
    return list(conn.execute(
        "SELECT * FROM equipment WHERE form_id = ? ORDER BY id", (form_id,)
    ))


def get_profile(conn: sqlite3.Connection, form_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM character_profile WHERE form_id = ?", (form_id,)
    ).fetchone()


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    out = {}
    for tbl in ("characters", "character_forms", "skills", "equipment",
                "character_affinities",
                "enemies", "enemy_forms", "enemy_member_stats", "enemy_weaknesses"):
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
    row = conn.execute(
        "SELECT id FROM enemies WHERE canonical_name = ? AND category = ? "
        "AND COALESCE(sheet_gid, -1) = COALESCE(?, -1)",
        (canonical_name, category, sheet_gid),
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE enemies SET region = ?, source_row = ?, "
            "name_color_hex = ?, hyperlink_url = ?, is_npc = ? WHERE id = ?",
            (region, source_row, name_color_hex, hyperlink_url, int(is_npc), row[0]),
        )
        return row[0]
    cur = conn.execute(
        "INSERT INTO enemies(canonical_name, category, region, sheet_gid, "
        "source_row, name_color_hex, hyperlink_url, is_npc) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (canonical_name, category, region, sheet_gid, source_row,
         name_color_hex, hyperlink_url, int(is_npc)),
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
    """Autocomplete source. Returns enemies whose canonical_name matches `current`,
    prefix matches first, then substring matches. One row per enemy."""
    needle = (current or "").strip().lower()
    if not needle:
        return list(conn.execute(
            "SELECT id AS enemy_id, canonical_name, category, region, is_npc "
            "FROM enemies ORDER BY canonical_name LIMIT ?",
            (limit,),
        ))
    sql = """
        SELECT id AS enemy_id, canonical_name, category, region, is_npc
        FROM enemies
        WHERE LOWER(canonical_name) LIKE ?
        ORDER BY
            CASE WHEN LOWER(canonical_name) LIKE ? THEN 0 ELSE 1 END,
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
