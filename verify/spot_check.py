"""Spot-check the live DB against the user-provided enemy screenshots.

Run after `python -m sync.cli`. Exits non-zero if either of the two
proof-points (Lloris EX3 stats, Lyblac EX3 weaknesses) doesn't match.
"""
from __future__ import annotations

import sys

from db import repo


# --- Sly Leader Lloris EX3 (Solistia Lvl 25) -------------------------------
# Position 0 = Leader Lloris, Position 1 = Mini Lloris.
LLORIS_EX3_STATS: dict[int, dict[str, str]] = {
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
LLORIS_EX3_WEAKNESSES: dict[int, list[str]] = {
    0: ["Axe", "Bow", "Ice", "Wind", "Dark"],         # Leader Lloris
    1: ["Dagger", "Bow", "Ice", "Lightning", "Dark"], # Mini Lloris
}

# --- Lyblac EX3 (Lvl 75 Osterra) — from the second user screenshot ---------
LYBLAC_EX3_WEAKNESSES: dict[int, list[str]] = {
    0: ["Sword", "Dagger", "Wind", "Light"],
}


def _check_stats(conn, name: str, rank: str,
                 expected: dict[int, dict[str, str]]) -> list[str]:
    enemy = conn.execute(
        "SELECT id FROM enemies WHERE canonical_name = ?", (name,),
    ).fetchone()
    if enemy is None:
        return [f"{name!r} not in DB"]
    form = conn.execute(
        "SELECT id FROM enemy_forms WHERE enemy_id = ? AND rank = ?",
        (enemy["id"], rank),
    ).fetchone()
    if form is None:
        return [f"{name!r} has no {rank} form"]
    actual: dict[int, dict[str, str]] = {}
    for r in conn.execute(
        "SELECT position, stat_name, stat_value FROM enemy_member_stats "
        "WHERE form_id = ? ORDER BY position, id", (form["id"],),
    ):
        actual.setdefault(r["position"], {})[r["stat_name"]] = r["stat_value"]
    fails: list[str] = []
    for pos, stats in expected.items():
        for stat, exp in stats.items():
            got = actual.get(pos, {}).get(stat, "<missing>")
            if got.replace(",", "") != exp.replace(",", ""):
                fails.append(
                    f"{name} {rank} pos{pos} {stat!r}: expected {exp!r}, got {got!r}"
                )
    return fails


def _check_weaknesses(conn, name: str, rank: str,
                      expected: dict[int, list[str]]) -> list[str]:
    enemy = conn.execute(
        "SELECT id FROM enemies WHERE canonical_name = ?", (name,),
    ).fetchone()
    if enemy is None:
        return [f"{name!r} not in DB"]
    form = conn.execute(
        "SELECT id FROM enemy_forms WHERE enemy_id = ? AND rank = ?",
        (enemy["id"], rank),
    ).fetchone()
    if form is None:
        return [f"{name!r} has no {rank} form"]
    actual: dict[int, list[str]] = {}
    for r in conn.execute(
        "SELECT position, weakness_label FROM enemy_weaknesses "
        "WHERE form_id = ? ORDER BY position, slot_order", (form["id"],),
    ):
        actual.setdefault(r["position"], []).append(r["weakness_label"])
    fails: list[str] = []
    for pos, exp_list in expected.items():
        got_list = actual.get(pos, [])
        if got_list != exp_list:
            fails.append(
                f"{name} {rank} pos{pos} weaknesses: expected {exp_list}, got {got_list}"
            )
    return fails


def main() -> int:
    conn = repo.connect()
    repo.bootstrap(conn)
    fails: list[str] = []
    fails += _check_stats(conn, "Sly Leader Lloris", "EX3", LLORIS_EX3_STATS)
    fails += _check_weaknesses(conn, "Sly Leader Lloris", "EX3", LLORIS_EX3_WEAKNESSES)
    fails += _check_weaknesses(conn, "Lyblac", "EX3", LYBLAC_EX3_WEAKNESSES)

    if fails:
        print("FAIL:")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("PASS — Lloris EX3 stats + weaknesses and Lyblac EX3 weaknesses match the user screenshots.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
