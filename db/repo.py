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
    """Apply schema.sql idempotently."""
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(sql)


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
) -> None:
    conn.execute(
        "UPDATE sync_runs "
        "SET finished_at = ?, status = ?, error = ?, forms_count = ?, skills_count = ? "
        "WHERE id = ?",
        (_now_iso(), status, error, forms_count, skills_count, run_id),
    )


def store_raw_snapshot(conn: sqlite3.Connection, run_id: int, payload: dict[str, Any]) -> None:
    blob = gzip.compress(json.dumps(payload).encode("utf-8"))
    conn.execute(
        "INSERT OR REPLACE INTO raw_snapshots(sync_run_id, payload_json) VALUES (?, ?)",
        (run_id, blob),
    )


def latest_sync_run(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM sync_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()


# --- destructive replace ----------------------------------------------------

def clear_data_tables(conn: sqlite3.Connection) -> None:
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
        "INSERT INTO skills(form_id, slot_order, name, sp_cost, kind, boost_level, "
        "description, power_min, power_max, hits) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (form_id, s.get("slot_order"), s.get("name"), s.get("sp_cost"),
             s.get("kind"), s.get("boost_level"), s.get("description"),
             s.get("power_min"), s.get("power_max"), s.get("hits"))
            for s in skills
        ],
    )


def insert_equipment(conn: sqlite3.Connection, form_id: int, items: list[dict]) -> None:
    if not items:
        return
    conn.executemany(
        "INSERT INTO equipment(form_id, slot, name, description) VALUES (?, ?, ?, ?)",
        [(form_id, e.get("slot"), e.get("name"), e.get("description")) for e in items],
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
                "character_affinities"):
        out[tbl] = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
    return out
