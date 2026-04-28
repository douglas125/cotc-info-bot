"""Spot-check live DB against the user-provided Lloris EX3 screenshot.

Run after `python -m sync.cli`. Exits non-zero if the EX3 stats don't match
the screenshot. This is the proof artifact for the PR.
"""
from __future__ import annotations

import sys
from typing import Any

from config import DB_PATH
from db import repo


# Expected values from the user-provided Sly Leader Lloris EX3 screenshot.
# Position 0 = Leader Lloris, Position 1 = Mini Lloris.
EXPECTED: dict[int, dict[str, str]] = {
    0: {
        "Shields":   "30",
        "HP":        "1,143,210",
        "P. Atk":    "1,752",
        "P. Def":    "217",
        "E. Atk":    "1,871",
        "E. Def":    "227",
        "Speed":     "481",
        "Crit":      "328",
        "CritDef":   "610",
        "Equip Atk": "275",
    },
    1: {
        "Shields":   "18",
        "HP":        "822,762",
        "P. Atk":    "1,344",
        "P. Def":    "189",
        "E. Atk":    "1,456",
        "E. Def":    "195",
        "Speed":     "429",
        "Crit":      "328",
        "CritDef":   "610",
        "Equip Atk": "275",
    },
}


def main() -> int:
    conn = repo.connect()
    repo.bootstrap(conn)
    enemy = conn.execute(
        "SELECT id, canonical_name, category FROM enemies "
        "WHERE canonical_name = 'Sly Leader Lloris'"
    ).fetchone()
    if enemy is None:
        print("FAIL: 'Sly Leader Lloris' not in DB. Did /refresh complete?")
        return 1
    form = conn.execute(
        "SELECT id FROM enemy_forms WHERE enemy_id = ? AND rank = 'EX3'", (enemy["id"],)
    ).fetchone()
    if form is None:
        print(f"FAIL: 'Sly Leader Lloris' has no EX3 form. enemy_id={enemy['id']}")
        return 1
    rows = list(conn.execute(
        "SELECT position, member_name, stat_name, stat_value "
        "FROM enemy_member_stats WHERE form_id = ? ORDER BY position, id",
        (form["id"],),
    ))

    actual: dict[int, dict[str, str]] = {}
    member_names: dict[int, str] = {}
    for r in rows:
        actual.setdefault(r["position"], {})[r["stat_name"]] = r["stat_value"]
        member_names.setdefault(r["position"], r["member_name"] or "?")

    failures: list[str] = []
    for pos, expected_stats in EXPECTED.items():
        if pos not in actual:
            failures.append(f"position {pos} missing from DB")
            continue
        for stat, expected_val in expected_stats.items():
            got = actual[pos].get(stat, "<missing>")
            # Some screenshot values are formatted ('1,143,210') while data tab
            # values may be plain ('1143210'). Normalize commas for compare.
            if got.replace(",", "") != expected_val.replace(",", ""):
                failures.append(
                    f"position {pos} ({member_names.get(pos, '?')}) "
                    f"stat {stat!r}: expected {expected_val!r}, got {got!r}"
                )

    print(f"Sly Leader Lloris EX3 spot check (form_id={form['id']})")
    print(f"  position 0 ({member_names.get(0, '?')}): {len(actual.get(0, {}))} stats")
    print(f"  position 1 ({member_names.get(1, '?')}): {len(actual.get(1, {}))} stats")
    if failures:
        print()
        print("FAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print()
    print("PASS — all 20 stat cells match the user-provided screenshot.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
