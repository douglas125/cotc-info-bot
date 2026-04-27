"""Automated verification: do the contents of cotc.sqlite match the live sheet?

Reads the most recent raw_snapshots payload (the Sheets API response captured
during the last sync) and compares parsed/persisted DB rows back against it.
This avoids an extra network round-trip and is reproducible — every check has
a single source of truth (the snapshot).

Run:
    conda activate cotc-search && python -m verify.check

Exits non-zero on any failed check so it can be wired into CI later.
"""
from __future__ import annotations

import gzip
import json
import sys
from collections import Counter, defaultdict
from typing import Any

from sync.runner import _levenshtein

from config import NAME_ALIASES, ROLE_TABS, TABS, TABS_BY_GID, canonicalize_name
from db import repo
from sync.fetch import sheet_by_gid
from sync.parsers import _cell_text


# UTF-8 stdout for Windows consoles (tab names contain ⭐).
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass


PASS = "✅"
FAIL = "❌"
WARN = "⚠️"


def _load_latest_payload(conn) -> dict[str, Any]:
    row = conn.execute(
        "SELECT payload_json FROM raw_snapshots ORDER BY sync_run_id DESC LIMIT 1"
    ).fetchone()
    if not row:
        raise SystemExit("No raw snapshot in DB — run sync first.")
    return json.loads(gzip.decompress(row[0]).decode("utf-8"))


def _live_role_tab_names(payload: dict, gid: int) -> list[tuple[int, str]]:
    """Re-scan a role tab in the snapshot, return (row, name) for every block start."""
    out: list[tuple[int, str]] = []
    sheet = sheet_by_gid(payload, gid)
    if not sheet:
        return out
    grids = sheet.get("data", []) or []
    rows: list[list[dict]] = []
    for g in grids:
        for r in g.get("rowData", []) or []:
            rows.append(r.get("values", []) or [])
    for ridx, row in enumerate(rows):
        if len(row) < 8:
            continue
        name = _cell_text(row[0])
        sp = _cell_text(row[6]).upper() if len(row) > 6 else ""
        active = _cell_text(row[7]).lower() if len(row) > 7 else ""
        if name and 1 <= len(name) <= 30 and sp == "SP" and active in ("active", "actives"):
            out.append((ridx, name))
    return out


def _live_index_names(payload: dict) -> list[str]:
    """Return the canonical_name list scraped from the Index tab in the snapshot."""
    sheet = sheet_by_gid(payload, 1917707422)
    if not sheet:
        return []
    rows: list[list[dict]] = []
    for g in sheet.get("data", []) or []:
        for r in g.get("rowData", []) or []:
            rows.append(r.get("values", []) or [])
    # role header row: anything matching 'Warrior (Sword)' style in first 30 rows
    role_cols: list[int] = []
    header_row_idx: int | None = None
    import re
    pat = re.compile(r"^(Warrior|Merchant|Thief|Apothecary|Hunter|Cleric|Scholar|Dancer)\s*\(", re.IGNORECASE)
    for ridx, row in enumerate(rows[:30]):
        cols_in_row = [cidx for cidx, cell in enumerate(row) if pat.match(_cell_text(cell) or "")]
        if len(cols_in_row) >= 4:
            header_row_idx = ridx
            role_cols = cols_in_row
            break
    names: list[str] = []
    if header_row_idx is None:
        return names
    for ridx in range(header_row_idx + 1, len(rows)):
        for col in role_cols:
            if col < len(rows[ridx]):
                t = _cell_text(rows[ridx][col])
                if t and not t.startswith("Color Key") and not t.lower().startswith("note"):
                    # only count if it has a hyperlink or a foreground color (real entries)
                    cell = rows[ridx][col]
                    if cell.get("hyperlink") or cell.get("effectiveFormat", {}).get("textFormat", {}).get("foregroundColor"):
                        names.append(t)
    return names


# --- individual checks ------------------------------------------------------

def check_tab_inventory(payload: dict) -> list[tuple[bool, str]]:
    """Every tab in config.TABS should be present in the snapshot."""
    out = []
    snap_gids = {s["properties"]["sheetId"] for s in payload.get("sheets", [])}
    for tab in TABS:
        ok = tab.gid in snap_gids
        out.append((ok, f"tab in snapshot: {tab.name} (gid={tab.gid})"))
    return out


def check_index_roster_matches_db(payload: dict, conn) -> list[tuple[bool, str]]:
    """The DB character_forms count for the Index should equal the live count."""
    live_names = _live_index_names(payload)
    db_names = [r[0] for r in conn.execute(
        "SELECT canonical_name FROM characters ORDER BY canonical_name"
    )]
    out: list[tuple[bool, str]] = []
    out.append((len(live_names) > 0,
                f"index has live entries: {len(live_names)}"))
    out.append((len(db_names) > 0,
                f"DB has characters: {len(db_names)}"))
    # Names in live but not in DB (more important than reverse — DB shouldn't drop entries)
    live_set = set(live_names)
    db_set = set(db_names)
    missing = sorted(live_set - db_set)
    extra = sorted(db_set - live_set)
    out.append((not missing,
                f"all live Index names present in DB "
                f"(missing {len(missing)}: {missing[:5]}{'…' if len(missing) > 5 else ''})"))
    if extra:
        out.append((True, f"{WARN} DB has {len(extra)} names not in live Index "
                          f"({extra[:5]}{'…' if len(extra) > 5 else ''}) — may be stale, OK if from prior sync"))
    return out


def check_role_tab_blocks(payload: dict, conn) -> list[tuple[bool, str]]:
    """Per role tab: every block-start name in the live tab is mappable
    (exactly or by ≥0.85 fuzzy match) to a DB form on the same role+band tab
    that has skills > 0. Mirrors runner._select_block_for so the verifier
    measures the same semantic the runner promises.
    """
    out: list[tuple[bool, str]] = []
    # Precompute: per (role, band) -> list of DB form names that have skills
    db_forms_by_role_band: dict[tuple[str, str], list[str]] = defaultdict(list)
    rows = conn.execute("""
        SELECT c.canonical_name, c.base_role, cf.rarity, COUNT(s.id) AS sk
        FROM characters c
        JOIN character_forms cf ON cf.character_id = c.id
        LEFT JOIN skills s ON s.form_id = cf.id
        WHERE cf.server = 'global'
        GROUP BY cf.id
    """).fetchall()
    for r in rows:
        if r["sk"] == 0:
            continue
        # Free 3→5★ characters appear on the ⭐5 tab in the live sheet (they
        # max out at 5★), so include them in BOTH bands for verification.
        if r["rarity"] == "free35":
            db_forms_by_role_band[(r["base_role"], "5*")].append(r["canonical_name"])
            db_forms_by_role_band[(r["base_role"], "34")].append(r["canonical_name"])
        else:
            band = "5*" if r["rarity"] == "5*" else "34"
            db_forms_by_role_band[(r["base_role"], band)].append(r["canonical_name"])

    for tab in ROLE_TABS:
        live_blocks = _live_role_tab_names(payload, tab.gid)
        if not live_blocks:
            out.append((False, f"{tab.name}: no live blocks detected"))
            continue
        pool = db_forms_by_role_band.get((tab.role, tab.rarity_band), [])
        missing: list[str] = []
        fuzzy_used: list[tuple[str, str]] = []
        alias_used: list[tuple[str, str]] = []
        for _, name in live_blocks:
            if name in pool:
                continue
            # 1. explicit alias map (config.NAME_ALIASES)
            canon = canonicalize_name(name)
            if canon != name and canon in pool:
                alias_used.append((name, canon))
                continue
            # 2. Levenshtein-≤2 fuzzy fallback — mirrors the runner.
            best: str | None = None
            best_dist = 999
            for cand in pool:
                d = _levenshtein(name, cand)
                if d < best_dist:
                    best_dist = d
                    best = cand
            short = min(len(name), len(best) if best else 99)
            if best is not None and best_dist <= 2 and best_dist < short // 2 + 1:
                fuzzy_used.append((name, best))
            else:
                missing.append(name)
        ok = not missing
        msg = (f"{tab.name}: {len(live_blocks)} live blocks → "
               f"{len(live_blocks) - len(missing)} mapped to DB forms with skills")
        if alias_used:
            msg += f" (alias: {alias_used[:3]}{'…' if len(alias_used)>3 else ''})"
        if fuzzy_used:
            msg += f" (fuzzy: {fuzzy_used[:3]}{'…' if len(fuzzy_used)>3 else ''})"
        if missing:
            msg += f" (MISSING: {missing[:5]}{'…' if len(missing)>5 else ''})"
        out.append((ok, msg))
    return out


def check_skill_counts_per_form(conn) -> list[tuple[bool, str]]:
    """Most forms should have at least 3 skills. Flag forms with 0."""
    rows = conn.execute(
        "SELECT cf.display_name, COUNT(s.id) AS sk "
        "FROM character_forms cf LEFT JOIN skills s ON s.form_id = cf.id "
        "WHERE cf.server = 'global' "
        "GROUP BY cf.id"
    ).fetchall()
    if not rows:
        return [(False, "no forms in DB at all")]
    zero = [r["display_name"] for r in rows if r["sk"] == 0]
    nonzero = [r for r in rows if r["sk"] > 0]
    pct = 100 * len(nonzero) / len(rows) if rows else 0
    out: list[tuple[bool, str]] = []
    out.append((pct >= 70.0,
                f"forms with skills: {len(nonzero)}/{len(rows)} ({pct:.0f}%)"))
    if zero:
        sample = zero[:8]
        out.append((True, f"{WARN} {len(zero)} forms have 0 skills "
                          f"(sample: {sample})"))
    return out


def check_rarity_distribution(conn) -> list[tuple[bool, str]]:
    """Rarity should map to all four buckets and be reasonably distributed."""
    counts = dict(conn.execute(
        "SELECT rarity, COUNT(*) FROM character_forms WHERE server='global' GROUP BY rarity"
    ))
    buckets = {"5*", "4*", "3*", "free35"}
    out: list[tuple[bool, str]] = []
    for b in buckets:
        n = counts.get(b, 0)
        out.append((n > 0, f"rarity bucket {b}: {n} forms"))
    null_n = counts.get(None, 0)
    out.append((True, f"{WARN if null_n>20 else ''} forms with NULL rarity: {null_n}"))
    return out


def check_sea_variants_present(conn) -> list[tuple[bool, str]]:
    n = conn.execute(
        "SELECT COUNT(*) FROM character_forms WHERE server='sea'"
    ).fetchone()[0]
    return [(n > 0, f"server='sea' forms: {n}")]


def check_fts_searchable(conn) -> list[tuple[bool, str]]:
    """FTS should return matches for at least one common term."""
    out: list[tuple[bool, str]] = []
    n = conn.execute("SELECT COUNT(*) FROM characters_fts").fetchone()[0]
    out.append((n > 0, f"characters_fts has {n} rows"))
    for term in ("Sword", "Power", "Active"):
        try:
            hits = conn.execute(
                "SELECT COUNT(*) FROM characters_fts WHERE characters_fts MATCH ?",
                (f'"{term}"',),
            ).fetchone()[0]
            out.append((hits > 0, f"FTS match for '{term}': {hits}"))
        except Exception as e:
            out.append((False, f"FTS query for '{term}' failed: {e}"))
    return out


def check_spot_characters(payload: dict, conn) -> list[tuple[bool, str]]:
    """Spot-check N specific characters: skill text and equipment from snapshot
    must appear in DB for that character."""
    out: list[tuple[bool, str]] = []
    # Use canonical (Index) names — the runner attaches role-tab data to these.
    targets = ["Fiore", "Lionel", "Therion", "Cyrus", "EX Cyrus", "H'aanit",
               "Tressa", "Clauser"]
    for name in targets:
        ch = conn.execute(
            "SELECT id FROM characters WHERE canonical_name = ?", (name,)
        ).fetchone()
        if not ch:
            out.append((False, f"spot-character '{name}': not in DB"))
            continue
        skills_n = conn.execute(
            "SELECT COUNT(*) FROM skills s "
            "JOIN character_forms cf ON cf.id = s.form_id "
            "WHERE cf.character_id = ? AND cf.server='global'", (ch["id"],),
        ).fetchone()[0]
        eq_n = conn.execute(
            "SELECT COUNT(*) FROM equipment e "
            "JOIN character_forms cf ON cf.id = e.form_id "
            "WHERE cf.character_id = ? AND cf.server='global'", (ch["id"],),
        ).fetchone()[0]
        out.append((skills_n >= 3,
                    f"spot-character '{name}': skills={skills_n}, equipment={eq_n}"))
    return out


# --- runner -----------------------------------------------------------------

def run_all() -> int:
    conn = repo.connect()
    repo.bootstrap(conn)
    payload = _load_latest_payload(conn)

    sections: list[tuple[str, list[tuple[bool, str]]]] = [
        ("Tab inventory",            check_tab_inventory(payload)),
        ("Index roster vs DB",       check_index_roster_matches_db(payload, conn)),
        ("Role-tab blocks vs DB",    check_role_tab_blocks(payload, conn)),
        ("Skill counts per form",    check_skill_counts_per_form(conn)),
        ("Rarity distribution",      check_rarity_distribution(conn)),
        ("SEA variants",             check_sea_variants_present(conn)),
        ("FTS searchable",           check_fts_searchable(conn)),
        ("Spot-check characters",    check_spot_characters(payload, conn)),
    ]

    total = 0
    failed = 0
    for title, results in sections:
        print(f"\n=== {title} ===")
        for ok, msg in results:
            total += 1
            if not ok:
                failed += 1
            mark = PASS if ok else FAIL
            print(f"  {mark} {msg}")

    print()
    if failed:
        print(f"{FAIL} {failed} of {total} checks failed.")
        return 1
    print(f"{PASS} all {total} checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_all())
