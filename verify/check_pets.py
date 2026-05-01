"""Automated verification for the pet pipeline.

Sibling to `verify.check_enemies`. Reads the most recent pet snapshot
from `raw_snapshots WHERE kind = 'pets'` and compares the parsed +
persisted DB rows against it. Exits non-zero on any failed check.

Run:
    conda activate cotc-search && python -m verify.check_pets
"""
from __future__ import annotations

import gzip
import json
import sys
from collections import Counter
from typing import Any

from config import PETS_LIST_GID
from db import repo
from sync.pet_parsers import parse_pets

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass

PASS = "[OK]   "
FAIL = "[FAIL] "
WARN = "[WARN] "

# Lower bound — the live sheet has ~60-70 pets today; this guards
# against a catastrophic parse regression that drops the count near zero.
PET_COUNT_FLOOR = 30

# Stat sanity bounds. Real outliers exist (Metal Slime HP=4,
# Pdef=100), so this is a SOFT check that warns rather than fails.
STAT_OUTLIER_MIN = 0
STAT_OUTLIER_MAX = 99999

# Pets we expect by name in any healthy DB. They cover the named edge
# cases: nested-paren names, the Christmas-Dog auto-heal, the Metal
# Slime extreme stats, and the duplicate-name "White Rabbit" pair.
SPOT_NAMES = (
    "Red Brown Cat",
    "Purple Lulu",
    "Metal Slime",
    "Christmas Dog",
    "White Rabbit",
)


def _load_payload(conn) -> dict[str, Any]:
    blob = repo.latest_raw_snapshot(conn, kind="pets")
    if blob is None:
        raise SystemExit(
            "No pet raw snapshot in DB — run `python -m sync.cli` first."
        )
    return json.loads(gzip.decompress(blob).decode("utf-8"))


# --- individual checks ------------------------------------------------------

def check_pet_count(conn) -> tuple[bool, str]:
    n = conn.execute("SELECT COUNT(*) FROM pets").fetchone()[0]
    if n < PET_COUNT_FLOOR:
        return False, f"only {n} pets persisted (floor {PET_COUNT_FLOOR})"
    return True, f"{n} pets persisted (floor {PET_COUNT_FLOOR})"


def check_no_duplicate_id_per_name_row(conn) -> tuple[bool, str]:
    rows = conn.execute(
        "SELECT canonical_name, source_row, COUNT(*) AS n FROM pets "
        "GROUP BY canonical_name, source_row HAVING n > 1"
    ).fetchall()
    if rows:
        return False, f"duplicate (name, source_row) keys: {[dict(r) for r in rows][:3]}"
    return True, "no duplicate (canonical_name, source_row) keys"


def check_white_rabbit_collision(conn) -> tuple[bool, str]:
    """Both 'White Rabbit' rows must persist as distinct pets."""
    rows = conn.execute(
        "SELECT id, source_row, source_text FROM pets "
        "WHERE canonical_name = 'White Rabbit' ORDER BY source_row"
    ).fetchall()
    if len(rows) < 2:
        return False, f"expected ≥2 'White Rabbit' rows, got {len(rows)}"
    if rows[0]["source_row"] == rows[1]["source_row"]:
        return False, "'White Rabbit' rows share source_row — collision lost"
    sources = " | ".join((r["source_text"] or "")[:40] for r in rows[:2])
    return True, f"both White Rabbits present (rows {rows[0]['source_row']}, {rows[1]['source_row']}): {sources}"


def check_christmas_dog_present(conn) -> tuple[bool, str]:
    """Auto-heal must have populated cooldown for the Christmas Dog typo."""
    row = conn.execute(
        "SELECT prep_base, prep_lv10, cooldown_base, cooldown_lv5 "
        "FROM pets WHERE canonical_name = 'Christmas Dog'"
    ).fetchone()
    if row is None:
        return False, "Christmas Dog not in DB"
    if row["cooldown_base"] is None:
        return False, (
            "Christmas Dog cooldown is NULL — auto-heal regressed; "
            "the maintainer typed two 'Turn Preparation' lines and the "
            "second one should map to Turn Cooldown"
        )
    return True, (
        f"Christmas Dog: prep={row['prep_base']} (Lv10:{row['prep_lv10']}), "
        f"cd={row['cooldown_base']} (Lv5:{row['cooldown_lv5']})"
    )


def check_fts_searchable(conn) -> tuple[bool, str]:
    """Hits for at least one ability term that should appear in the corpus."""
    misses: list[str] = []
    for term in ("Patk", "Heal", "BP"):
        try:
            n = conn.execute(
                "SELECT COUNT(*) FROM pets_fts WHERE pets_fts MATCH ?",
                (f'"{term}"*',),
            ).fetchone()[0]
        except Exception as exc:
            return False, f"FTS query for {term!r} raised: {exc}"
        if n == 0:
            misses.append(term)
    if misses:
        return False, f"FTS returned 0 hits for {misses}"
    return True, "FTS hits for ability terms (Patk, Heal, BP)"


def check_spot_pets_persisted(conn) -> tuple[bool, str]:
    missing = []
    for name in SPOT_NAMES:
        n = conn.execute(
            "SELECT COUNT(*) FROM pets WHERE canonical_name = ?", (name,)
        ).fetchone()[0]
        if n == 0:
            missing.append(name)
    if missing:
        return False, f"missing named pets: {missing}"
    return True, f"all {len(SPOT_NAMES)} named pets persisted"


def check_db_matches_snapshot_parse(conn, payload: dict[str, Any]) -> tuple[bool, str]:
    """Re-parse the snapshot and confirm the DB rows match for a sample of pets."""
    pets, _ = parse_pets(payload, PETS_LIST_GID)
    by_key = {(p.canonical_name, p.source_row): p for p in pets}
    if len(pets) == 0:
        return False, "snapshot re-parse returned 0 pets"

    diffs: list[str] = []
    sample_count = 0
    for name in SPOT_NAMES:
        rows = conn.execute(
            "SELECT * FROM pets WHERE canonical_name = ? ORDER BY source_row",
            (name,),
        ).fetchall()
        for db_row in rows:
            key = (db_row["canonical_name"], db_row["source_row"])
            parsed = by_key.get(key)
            if parsed is None:
                diffs.append(f"{key}: in DB but not in re-parse")
                continue
            sample_count += 1
            for attr in ("hp", "sp", "patk", "pdef", "matk", "mdef",
                         "crit", "speed", "max_boost",
                         "prep_base", "prep_lv10",
                         "cooldown_base", "cooldown_lv5"):
                if getattr(parsed, attr) != db_row[attr]:
                    diffs.append(
                        f"{key} {attr}: parsed={getattr(parsed, attr)!r} "
                        f"DB={db_row[attr]!r}"
                    )
    if diffs:
        return False, f"DB ↔ snapshot drift: {diffs[:5]}"
    return True, f"{sample_count} sampled (name, row) tuples match snapshot exactly"


def check_stat_outliers(conn) -> tuple[bool, str]:
    """Soft check — Metal Slime's HP=4 is real, but a flood of outliers
    smells like a parser regression. WARN-only."""
    rows = conn.execute(
        "SELECT canonical_name, hp, sp, patk, pdef, matk, mdef, crit, speed FROM pets"
    ).fetchall()
    flagged: list[str] = []
    for r in rows:
        for stat in ("hp", "sp", "patk", "pdef", "matk", "mdef", "crit", "speed"):
            v = r[stat]
            if v is None:
                continue
            if v < STAT_OUTLIER_MIN or v > STAT_OUTLIER_MAX:
                flagged.append(f"{r['canonical_name']}.{stat}={v}")
    if flagged:
        return True, f"{WARN}stat outliers (review): {flagged[:5]}"
    return True, "no stat outliers outside (0, 99999)"


def check_sync_run_pets_count(conn) -> tuple[bool, str]:
    last = repo.latest_sync_run(conn)
    if last is None:
        return False, "no sync_runs row"
    if last["pets_count"] is None:
        return False, "latest sync_runs.pets_count is NULL — runner didn't record it"
    db_n = conn.execute("SELECT COUNT(*) FROM pets").fetchone()[0]
    if last["pets_count"] != db_n:
        return False, (
            f"sync_runs.pets_count ({last['pets_count']}) != live count ({db_n})"
        )
    return True, f"sync_runs.pets_count = {last['pets_count']} matches live count"


# --- runner -----------------------------------------------------------------

def main() -> int:
    conn = repo.connect()
    repo.bootstrap(conn)
    payload = _load_payload(conn)

    checks = [
        ("pet count plausibility",            lambda: check_pet_count(conn)),
        ("no duplicate (name, row) keys",     lambda: check_no_duplicate_id_per_name_row(conn)),
        ("White Rabbit collision preserved",  lambda: check_white_rabbit_collision(conn)),
        ("Christmas Dog auto-heal applied",   lambda: check_christmas_dog_present(conn)),
        ("FTS searchable",                    lambda: check_fts_searchable(conn)),
        ("named pets persisted",              lambda: check_spot_pets_persisted(conn)),
        ("DB matches snapshot re-parse",      lambda: check_db_matches_snapshot_parse(conn, payload)),
        ("stat outliers soft check",          lambda: check_stat_outliers(conn)),
        ("sync_runs.pets_count matches",      lambda: check_sync_run_pets_count(conn)),
    ]

    ok = True
    for name, fn in checks:
        try:
            passed, detail = fn()
        except Exception as exc:
            passed, detail = False, f"raised: {exc}"
        prefix = PASS if passed else FAIL
        print(f"{prefix}{name}: {detail}")
        if not passed:
            ok = False

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
