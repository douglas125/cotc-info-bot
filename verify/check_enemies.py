"""Automated verification for the enemy pipeline.

Sibling to `verify.check`. Reads the most recent enemy snapshot from
`raw_snapshots WHERE kind = 'enemies'` and compares the parsed/persisted
DB rows against it. Exits non-zero on any failed check.

Run:
    conda activate cotc-search && python -m verify.check_enemies
"""
from __future__ import annotations

import gzip
import json
import sys
from typing import Any

from config import (
    ENEMIES_TABS,
    ENEMY_DATA_TAB_GIDS,
    ENEMY_NPC_TAB_GIDS,
    ENEMY_SKIP_TABS,
)
from db import repo
from sync.enemy_parsers import (
    parse_data_tab,
    parse_npc_data_tab,
)
from sync.fetch import sheet_by_gid

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass

PASS = "[OK]   "
FAIL = "[FAIL] "
WARN = "[WARN] "


def _load_payload(conn) -> dict[str, Any]:
    blob = repo.latest_raw_snapshot(conn, kind="enemies")
    if blob is None:
        raise SystemExit(
            "No enemy raw snapshot in DB — run /refresh or `python -m sync.cli` first."
        )
    return json.loads(gzip.decompress(blob).decode("utf-8"))


# --- individual checks ------------------------------------------------------

def check_tab_inventory(payload: dict[str, Any]) -> tuple[bool, str]:
    payload_titles = {
        s.get("properties", {}).get("title", "")
        for s in payload.get("sheets", [])
    }
    missing: list[str] = []
    for spec in ENEMIES_TABS:
        if spec.name not in payload_titles:
            missing.append(spec.name)
    for region, gid in ENEMY_DATA_TAB_GIDS.items():
        s = sheet_by_gid(payload, gid)
        if s is None:
            missing.append(f"<data tab gid={gid} for region {region}>")
    if missing:
        return False, f"missing tabs: {missing}"
    return True, f"all {len(ENEMIES_TABS)} display + 3 data tabs present"


def check_data_tab_encounter_counts(payload: dict[str, Any]) -> tuple[bool, str]:
    counts: dict[str, int] = {}
    for region, gid in ENEMY_DATA_TAB_GIDS.items():
        sheet = sheet_by_gid(payload, gid)
        if sheet is None:
            counts[region] = -1
            continue
        if region == "NPCs":
            counts[region] = len(parse_npc_data_tab(sheet))
        else:
            counts[region] = len(parse_data_tab(sheet, region))
    failures = [r for r, n in counts.items() if n <= 0]
    if failures:
        return False, f"empty parse for regions {failures}: {counts}"
    return True, f"data tabs parsed: {counts}"


def check_enemies_persisted(conn, payload: dict[str, Any]) -> tuple[bool, str]:
    n = conn.execute("SELECT COUNT(*) FROM enemies").fetchone()[0]
    if n == 0:
        return False, "no rows in `enemies` — runner failed to persist"
    return True, f"{n} enemies persisted"


def check_rank_coverage(conn) -> tuple[bool, str]:
    """Most ranked enemies should have all 6 ranks."""
    ranked_total = conn.execute(
        "SELECT COUNT(*) FROM enemies WHERE is_npc = 0"
    ).fetchone()[0]
    if ranked_total == 0:
        return False, "no ranked enemies in DB"
    full = conn.execute(
        "SELECT COUNT(*) FROM enemies e WHERE e.is_npc = 0 "
        "AND (SELECT COUNT(*) FROM enemy_forms f WHERE f.enemy_id = e.id) = 6"
    ).fetchone()[0]
    pct = full * 100.0 / ranked_total
    if pct < 70:
        return False, f"only {full}/{ranked_total} ranked enemies have all 6 ranks ({pct:.0f}%)"
    return True, f"{full}/{ranked_total} ranked enemies have all 6 ranks ({pct:.0f}%)"


def check_npc_single_rank(conn) -> tuple[bool, str]:
    """Every NPC must have exactly one form with rank='Default'."""
    bad = conn.execute(
        "SELECT COUNT(*) FROM enemies e WHERE e.is_npc = 1 AND ("
        "  (SELECT COUNT(*) FROM enemy_forms f WHERE f.enemy_id = e.id) != 1 OR "
        "  (SELECT rank FROM enemy_forms f WHERE f.enemy_id = e.id LIMIT 1) != 'Default'"
        ")"
    ).fetchone()[0]
    if bad:
        return False, f"{bad} NPCs have wrong form layout (expected 1×Default)"
    n = conn.execute("SELECT COUNT(*) FROM enemies WHERE is_npc = 1").fetchone()[0]
    return True, f"{n} NPCs each have exactly one Default form"


def check_stats_present(conn) -> tuple[bool, str]:
    """Every form should have ≥1 stats row, and HP should look numeric."""
    bad = conn.execute(
        "SELECT COUNT(*) FROM enemy_forms f WHERE NOT EXISTS ("
        "  SELECT 1 FROM enemy_member_stats s WHERE s.form_id = f.id)"
    ).fetchone()[0]
    if bad:
        return False, f"{bad} forms have no stats rows"
    n = conn.execute("SELECT COUNT(*) FROM enemy_member_stats").fetchone()[0]
    # HP sanity: every HP value should parse as a positive number once commas
    # and any trailing '.00' are stripped. Some entries are formatted as
    # decimals (e.g. '5,827,800.00') — accept those too.
    bad_hp = 0
    for row in conn.execute(
        "SELECT stat_value FROM enemy_member_stats WHERE stat_name = 'HP'"
    ):
        try:
            v = float(row[0].replace(",", ""))
            if v <= 0:
                bad_hp += 1
        except (ValueError, AttributeError):
            bad_hp += 1
    if bad_hp:
        return False, f"{bad_hp} HP values aren't positive numbers"
    return True, f"{n} stat rows, all HP values numeric"


def check_fts_searchable(conn) -> tuple[bool, str]:
    """The enemies_fts index should return hits for at least one common token."""
    rows = list(conn.execute(
        "SELECT enemy_id FROM enemies_fts WHERE enemies_fts MATCH ? LIMIT 5",
        ("Lloris*",),
    ))
    if not rows:
        return False, "FTS for 'Lloris*' returned no matches"
    return True, f"FTS for 'Lloris*' returned {len(rows)} match(es)"


def check_largo_ex3_present(conn) -> tuple[bool, str]:
    """Regression: Largo's display block has a Wave label between name and rank."""
    row = conn.execute(
        "SELECT e.category, e.region, e.hyperlink_url "
        "FROM enemies e "
        "JOIN enemy_forms f ON f.enemy_id = e.id AND f.rank = 'EX3' "
        "WHERE e.canonical_name = 'Largo' AND e.category = 'Lvl 75' "
        "AND e.region = 'Osterra' "
        "LIMIT 1"
    ).fetchone()
    if row is None:
        return False, "Largo Lvl 75 EX3 not found in DB"
    return True, f"Largo EX3 present ({row['hyperlink_url']})"


def check_lloris_ex3_against_screenshot(conn) -> tuple[bool, str]:
    """Spot-check: Sly Leader Lloris EX3 must match the user-provided screenshot.

    Position 0 (Leader Lloris) HP = 1,143,210 / Shields = 30.
    Position 1 (Mini Lloris) HP = 822,762  / Shields = 18.
    """
    row = conn.execute(
        "SELECT s.position, s.stat_name, s.stat_value "
        "FROM enemies e "
        "JOIN enemy_forms f ON f.enemy_id = e.id AND f.rank = 'EX3' "
        "JOIN enemy_member_stats s ON s.form_id = f.id "
        "WHERE e.canonical_name = 'Sly Leader Lloris' "
        "ORDER BY s.position, s.id"
    ).fetchall()
    if not row:
        return False, "Sly Leader Lloris EX3 not found in DB"
    expected = {
        (0, "HP"):      "1143210",
        (0, "Shields"): "30",
        (1, "HP"):      "822762",
        (1, "Shields"): "18",
    }
    for r in row:
        key = (r["position"], r["stat_name"])
        if key in expected:
            if r["stat_value"].replace(",", "") != expected[key]:
                return False, (
                    f"Lloris EX3 {key}: expected {expected[key]!r}, "
                    f"got {r['stat_value']!r}"
                )
    return True, "Lloris EX3 HP/Shields match the user screenshot"


def check_weaknesses_present(conn) -> tuple[bool, str]:
    """Most ranked enemies should have at least one weakness row per form."""
    n_forms = conn.execute(
        "SELECT COUNT(*) FROM enemy_forms f "
        "JOIN enemies e ON e.id = f.enemy_id WHERE e.is_npc = 0"
    ).fetchone()[0]
    if n_forms == 0:
        return False, "no ranked enemy_forms"
    n_with_weak = conn.execute(
        "SELECT COUNT(*) FROM enemy_forms f "
        "JOIN enemies e ON e.id = f.enemy_id "
        "WHERE e.is_npc = 0 AND EXISTS ("
        "  SELECT 1 FROM enemy_weaknesses w WHERE w.form_id = f.id)"
    ).fetchone()[0]
    pct = n_with_weak * 100.0 / n_forms
    if pct < 70:
        return False, (
            f"only {n_with_weak}/{n_forms} ranked forms have weaknesses ({pct:.0f}%)"
        )
    return True, f"{n_with_weak}/{n_forms} ranked forms have weaknesses ({pct:.0f}%)"


def check_lvl120_npc_weaknesses_present(conn) -> tuple[bool, str]:
    """Sibling of check_weaknesses_present, scoped to the 120 NPCs tab.

    Pre-fix this would have been 0% — `parse_npc_display_tab` never extracted
    weaknesses. Set the bar high (>=70%) so any future regression that drops
    them again is loud.
    """
    n_forms = conn.execute(
        "SELECT COUNT(*) FROM enemy_forms f "
        "JOIN enemies e ON e.id = f.enemy_id WHERE e.category = '120 NPCs'"
    ).fetchone()[0]
    if n_forms == 0:
        return False, "no 120 NPCs forms in DB — display-tab parser dropped them all"
    n_with_weak = conn.execute(
        "SELECT COUNT(*) FROM enemy_forms f "
        "JOIN enemies e ON e.id = f.enemy_id "
        "WHERE e.category = '120 NPCs' AND EXISTS ("
        "  SELECT 1 FROM enemy_weaknesses w WHERE w.form_id = f.id)"
    ).fetchone()[0]
    pct = n_with_weak * 100.0 / n_forms
    if pct < 70:
        return False, (
            f"only {n_with_weak}/{n_forms} lvl120 forms have weaknesses ({pct:.0f}%)"
        )
    return True, f"{n_with_weak}/{n_forms} lvl120 forms have weaknesses ({pct:.0f}%)"


def check_lvl120_canalbrine_multi_member(conn) -> tuple[bool, str]:
    """Spot-check that Canalbrine — a known multi-position lvl120 encounter —
    persists with each member as a distinct row.

    Pre-fix the NPC parser collapsed every encounter to a single position
    using the encounter name as the member name, so this would have shown
    1 position for Canalbrine instead of 3.
    """
    rows = list(conn.execute(
        "SELECT DISTINCT s.position, s.member_name "
        "FROM enemies e "
        "JOIN enemy_forms f ON f.enemy_id = e.id "
        "JOIN enemy_member_stats s ON s.form_id = f.id "
        "WHERE e.canonical_name = 'Canalbrine' AND e.category = '120 NPCs' "
        "ORDER BY s.position"
    ))
    if not rows:
        return False, "Canalbrine not found in 120 NPCs — parser failed to detect it"
    n_positions = len({r["position"] for r in rows})
    member_names = [r["member_name"] for r in rows]
    if n_positions < 2:
        return False, (
            f"Canalbrine should have multi-position members; got {n_positions} "
            f"position(s) with member names {member_names}"
        )
    if len(set(member_names)) < 2:
        return False, (
            f"Canalbrine positions all share one member name {member_names!r} — "
            "the per-position member-name extractor regressed"
        )
    return True, (
        f"Canalbrine has {n_positions} positions with distinct members "
        f"{member_names}"
    )


def check_lloris_ex3_weaknesses(conn) -> tuple[bool, str]:
    """Per the screenshot: Leader Lloris EX3 = Axe/Bow/Ice/Wind/Dark."""
    rows = list(conn.execute(
        "SELECT w.position, w.weakness_label "
        "FROM enemies e "
        "JOIN enemy_forms f ON f.enemy_id = e.id AND f.rank = 'EX3' "
        "JOIN enemy_weaknesses w ON w.form_id = f.id "
        "WHERE e.canonical_name = 'Sly Leader Lloris' "
        "ORDER BY w.position, w.slot_order"
    ))
    by_pos: dict[int, list[str]] = {}
    for r in rows:
        by_pos.setdefault(r["position"], []).append(r["weakness_label"])
    expected = {
        0: ["Axe", "Bow", "Ice", "Wind", "Dark"],
        1: ["Dagger", "Bow", "Ice", "Lightning", "Dark"],
    }
    for pos, exp in expected.items():
        if by_pos.get(pos) != exp:
            return False, (
                f"Lloris EX3 pos{pos} weaknesses: expected {exp}, "
                f"got {by_pos.get(pos)}"
            )
    return True, "Lloris EX3 weaknesses match the screenshot (10 labels)"


# --- runner -----------------------------------------------------------------

def main() -> int:
    conn = repo.connect()
    repo.bootstrap(conn)
    payload = _load_payload(conn)

    checks = [
        ("tab inventory",                 lambda: check_tab_inventory(payload)),
        ("data tab encounter counts",     lambda: check_data_tab_encounter_counts(payload)),
        ("enemies persisted",             lambda: check_enemies_persisted(conn, payload)),
        ("rank coverage",                 lambda: check_rank_coverage(conn)),
        ("NPC single-rank shape",         lambda: check_npc_single_rank(conn)),
        ("stats present + HP sanity",     lambda: check_stats_present(conn)),
        ("FTS searchable",                lambda: check_fts_searchable(conn)),
        ("Largo EX3 present",             lambda: check_largo_ex3_present(conn)),
        ("Lloris EX3 stats vs screenshot", lambda: check_lloris_ex3_against_screenshot(conn)),
        ("weaknesses present per form",    lambda: check_weaknesses_present(conn)),
        ("lvl120 NPC weaknesses present",   lambda: check_lvl120_npc_weaknesses_present(conn)),
        ("lvl120 Canalbrine multi-member",  lambda: check_lvl120_canalbrine_multi_member(conn)),
        ("Lloris EX3 weaknesses vs screenshot", lambda: check_lloris_ex3_weaknesses(conn)),
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

    # Soft check: report any unmatched display blocks from the latest sync.
    n_enemies = conn.execute("SELECT COUNT(*) FROM enemies").fetchone()[0]
    last = repo.latest_sync_run(conn)
    if last and last["enemies_count"] is not None:
        if last["enemies_count"] != n_enemies:
            print(f"{WARN}sync_runs.enemies_count ({last['enemies_count']}) "
                  f"!= live enemies count ({n_enemies}) — DB and sync record drifted")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
