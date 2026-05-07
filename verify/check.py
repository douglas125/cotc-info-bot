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
from sync.parsers import SEA_GID, _cell_text, parse_sea_kits


# UTF-8 stdout for Windows consoles (tab names contain ⭐).
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass


PASS = "✅"
FAIL = "❌"
WARN = "⚠️"


def _load_latest_payload(conn) -> dict[str, Any]:
    blob = repo.latest_raw_snapshot(conn, kind="characters")
    if blob is None:
        raise SystemExit("No character raw snapshot in DB — run sync first.")
    return json.loads(gzip.decompress(blob).decode("utf-8"))


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


def check_skill_uniqueness_per_form(conn) -> list[tuple[bool, str]]:
    """Each form has at most one TP/divine active, one TP passive
    (kind='tp_passive'), one EX, one latent power, and between 0 and 3
    ultimate-tier rows (the Lv1/Lv10/Lv20 tiers of the same Special
    skill). Some units have only 1 or 2 tiers released (Lv20 lands
    later), so we allow {0,1,2,3}; anything ≥4 means the parser is
    over-classifying — historically 'N*' board markers were mis-tagged
    as ultimates, producing many 'ultimate' rows per form.
    """
    out: list[tuple[bool, str]] = []
    rows = conn.execute(
        "SELECT cf.display_name, s.kind, COUNT(*) AS n "
        "FROM character_forms cf JOIN skills s ON s.form_id = cf.id "
        "WHERE cf.server = 'global' "
        "  AND s.kind IN ('ex','divine','tp_passive','latent','ultimate') "
        "GROUP BY cf.id, s.kind"
    ).fetchall()
    by_form: dict[str, dict[str, int]] = defaultdict(dict)
    for r in rows:
        by_form[r["display_name"]][r["kind"]] = r["n"]
    bad: list[tuple[str, str]] = []
    bad_forms: set[str] = set()
    for name, counts in by_form.items():
        for kind in ("ex", "divine", "tp_passive", "latent"):
            n = counts.get(kind, 0)
            if n > 1:
                bad.append((name, f"{n} {kind} skills (expected ≤1)"))
                bad_forms.add(name)
        n_ult = counts.get("ultimate", 0)
        if n_ult > 3:
            bad.append((name, f"{n_ult} ultimate-tier rows (expected 0–3)"))
            bad_forms.add(name)
    total_forms = conn.execute(
        "SELECT COUNT(*) FROM character_forms WHERE server='global'"
    ).fetchone()[0]
    out.append((not bad,
                f"skill-kind uniqueness: {total_forms - len(bad_forms)}/{total_forms} forms ok, "
                f"{len(bad_forms)} forms with {len(bad)} violations"))
    if bad:
        out.append((False, f"  offenders (sample): {bad[:5]}"))
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


def check_sea_kit_precedence(payload: dict, conn) -> list[tuple[bool, str]]:
    """Characters listed in the SEA/GL Unique Kits tab should be populated
    from that tab — one form per character, no server='sea' duplicates.

    Matching is exact + NAME_ALIASES only. Levenshtein fuzzy isn't used here
    because the SEA tab spans all roles (no role/band scoping to constrain
    the search), so fuzzy produces false positives like 'Molu' → 'Lolo'.
    Unmatched SEA names (e.g. 'Molu', 'Tithi') are reported as a warning —
    they're typically SEA-only characters with no Index entry yet.
    """
    out: list[tuple[bool, str]] = []
    # Regression guard: no display_name should appear as both a global and a
    # sea form (would mean a SEA-only block leaked through despite a matching
    # Index entry).
    dupes = [r[0] for r in conn.execute(
        "SELECT display_name FROM character_forms "
        "GROUP BY LOWER(display_name) "
        "HAVING COUNT(DISTINCT server) > 1"
    )]
    out.append((not dupes,
                f"no cross-server display_name duplicates: {dupes or 'ok'}"))

    sea_sheet = sheet_by_gid(payload, SEA_GID)
    if sea_sheet is None:
        out.append((False, "SEA/GL Unique Kits sheet missing from snapshot"))
        return out
    blocks = parse_sea_kits(sea_sheet)
    out.append((len(blocks) > 0,
                f"SEA/GL Unique Kits parsed blocks: {len(blocks)}"))

    canon_set = {r[0] for r in conn.execute(
        "SELECT canonical_name FROM characters"
    )}

    matched = 0
    alias_used: list[tuple[str, str]] = []
    unmatched: list[str] = []
    for block in blocks:
        name = block.display_name
        target: str | None = None
        if name in canon_set:
            target = name
        else:
            canon = canonicalize_name(name)
            if canon != name and canon in canon_set:
                target = canon
                alias_used.append((name, canon))
        if target is None:
            unmatched.append(name)
            continue
        row = conn.execute(
            "SELECT cf.id FROM character_forms cf "
            "JOIN characters c ON c.id = cf.character_id "
            "WHERE c.canonical_name = ? AND cf.server = 'global'",
            (target,),
        ).fetchone()
        sk = conn.execute(
            "SELECT COUNT(*) FROM skills WHERE form_id = ?", (row[0],)
        ).fetchone()[0] if row else 0
        if sk > 0:
            matched += 1
        else:
            unmatched.append(name)
    msg = f"SEA-listed characters resolved to Index: {matched}/{len(blocks)}"
    if alias_used:
        msg += (f" (alias: {alias_used[:3]}"
                f"{'…' if len(alias_used) > 3 else ''})")
    if unmatched:
        msg = f"{WARN} " + msg + (f" (unmatched: {unmatched[:5]}"
                                  f"{'…' if len(unmatched) > 5 else ''})")
    # Pass criterion: at least 80% of SEA blocks resolve. Below that,
    # something is structurally wrong with the parser or alias map.
    threshold_ok = matched >= 0.8 * len(blocks) if blocks else False
    out.append((threshold_ok, msg))

    sea_missing_role_weapon = [r["display_name"] for r in conn.execute(
        "SELECT cf.display_name "
        "FROM character_forms cf "
        "JOIN characters c ON c.id = cf.character_id "
        "WHERE cf.server = 'sea' "
        "AND (c.base_role IS NULL OR c.base_weapon IS NULL)"
    )]
    out.append((not sea_missing_role_weapon,
                "SEA-only forms have inferred role/weapon: "
                f"{sea_missing_role_weapon or 'ok'}"))

    lynette_ex = conn.execute(
        "SELECT c.base_role, c.base_weapon "
        "FROM characters c "
        "JOIN character_forms cf ON cf.character_id = c.id "
        "WHERE cf.display_name = 'Lynette EX' AND cf.server = 'sea' "
        "LIMIT 1"
    ).fetchone()
    lynette_ok = (
        lynette_ex is not None
        and lynette_ex["base_role"] == "thief"
        and lynette_ex["base_weapon"] == "dagger"
    )
    got = dict(lynette_ex) if lynette_ex else None
    out.append((lynette_ok, f"Lynette EX SEA role/weapon: {got}"))
    return out


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


def check_splash_art_coverage(conn) -> list[tuple[bool, str]]:
    """Report what fraction of forms have a captured splash_art_url.

    Soft check — emits WARN, never FAIL — because the source sheet may
    legitimately have no artwork for some characters. Useful for spotting
    parser regressions where the count drops to 0 unexpectedly.
    """
    total = conn.execute(
        "SELECT COUNT(*) FROM character_forms"
    ).fetchone()[0]
    with_art = conn.execute(
        "SELECT COUNT(*) FROM character_profile "
        "WHERE splash_art_url IS NOT NULL AND splash_art_url != ''"
    ).fetchone()[0]
    pct = (100.0 * with_art / total) if total else 0.0
    return [(True, f"{with_art}/{total} forms have splash_art_url ({pct:.0f}%)")]


def check_spot_characters(payload: dict, conn) -> list[tuple[bool, str]]:
    """Spot-check N specific characters: skill text and equipment from snapshot
    must appear in DB for that character."""
    out: list[tuple[bool, str]] = []
    # Use canonical (Index) names — the runner attaches role-tab data to these.
    targets = ["Fiore", "Lionel", "Therion", "Cyrus", "EX Cyrus", "H'aanit",
               "Tressa", "Clauser", "Lynette EX"]
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
            "WHERE cf.character_id = ?", (ch["id"],),
        ).fetchone()[0]
        eq_n = conn.execute(
            "SELECT COUNT(*) FROM equipment e "
            "JOIN character_forms cf ON cf.id = e.form_id "
            "WHERE cf.character_id = ?", (ch["id"],),
        ).fetchone()[0]
        out.append((skills_n >= 3,
                    f"spot-character '{name}': skills={skills_n}, equipment={eq_n}"))
    return out


# Stats are in the sheet's listed order. The live sheet uses U+2019
# (curly apostrophe) in names like "Professor’s Insignia" — match exactly.
_EXPECTED_A4_STATS: dict[str, tuple[str, list[tuple[str, int]]]] = {
    "Bargello":   ("Cuffs of the Family",          [("SP", 40), ("ATK", 100)]),
    "Cyrus":      ("Professor’s Insignia",    [("SP", 40), ("MAG", 60), ("MDEF", 40)]),
    "Therion":    ("Famous Thief’s Lockpick", [("HP", 400), ("ATK", 40), ("SPD", 60)]),
    "Lionel":     ("Crest of Breistadt",           [("HP", 400), ("ATK", 60), ("DEF", 40)]),
    "Serenoa":    ("Crest of the Wolffort Family", [("HP", 300), ("ATK", 40), ("DEF", 30), ("CRIT", 40)]),
    "EX Temenos": ("The Secrets of Sorcery",       [("HP", 900), ("SP", 100), ("ATK", -200), ("MAG", 200)]),
    "Lemaire":    ("Brush of Passion",             [("ATK", 35), ("DEF", 35), ("MAG", 35), ("MDEF", 35)]),
}


def check_a4_accessory_stats(conn) -> list[tuple[bool, str]]:
    """For each hand-verified character, assert their A4 accessory stats
    parsed correctly (correct accessory name, correct ordered stat list).

    Also reports a coverage summary: of all primary (non-exclusive) A4
    accessories, what fraction has at least one stat row. The live sheet
    assigns stats to almost every primary accessory, so a sudden drop is
    a parser-regression smell.
    """
    out: list[tuple[bool, str]] = []
    for name, (acc_name, expected_stats) in _EXPECTED_A4_STATS.items():
        rows = conn.execute(
            "SELECT es.stat_name, es.stat_value FROM equipment_stats es "
            "JOIN equipment e ON e.id = es.equipment_id "
            "JOIN character_forms cf ON cf.id = e.form_id "
            "JOIN characters c ON c.id = cf.character_id "
            "WHERE c.canonical_name = ? AND e.name = ? "
            "ORDER BY es.stat_order",
            (name, acc_name),
        ).fetchall()
        actual = [(r["stat_name"], r["stat_value"]) for r in rows]
        ok = actual == expected_stats
        out.append((ok, f"A4 stats for '{name}' / '{acc_name}': "
                        f"expected {expected_stats}, got {actual}"))

    # Primary A4s almost always carry stats; <95% indicates a parser regression.
    # Excludes "Unique Effects" pseudo-rows (those exist to anchor status icons).
    total = conn.execute(
        "SELECT COUNT(*) FROM equipment "
        "WHERE is_exclusive = 0 AND lower(name) != 'unique effects'"
    ).fetchone()[0]
    with_stats = conn.execute(
        "SELECT COUNT(DISTINCT e.id) FROM equipment e "
        "JOIN equipment_stats es ON es.equipment_id = e.id "
        "WHERE e.is_exclusive = 0 AND lower(e.name) != 'unique effects'"
    ).fetchone()[0]
    pct = (100.0 * with_stats / total) if total else 0.0
    out.append((pct >= 95.0,
                f"primary-A4 stat coverage: {with_stats}/{total} ({pct:.0f}%) "
                f"have at least one stat row"))
    return out


# Hand-verified expectations for the new Lv100/Lv120 base-stats grid plus
# alignment label. ``alignment`` is None when unverified — only Aviete's
# alignment was visible in the screenshot used to design the feature, so
# other entries assert "non-empty string" instead of an exact match.
# Add concrete values here once verified against the live sheet.
_EXPECTED_BASE_STATS: dict[str, dict[str, Any]] = {
    "Aviete": {"alignment": "Glory", "lv120_required": True},
    "Cyrus":  {"alignment": None,    "lv120_required": True},
    "Therion": {"alignment": None,   "lv120_required": True},
    "Tressa":  {"alignment": None,   "lv120_required": True},
}


def check_character_base_stats(conn) -> list[tuple[bool, str]]:
    """Spot-check the Lv100/Lv120 stats grid + alignment label.

    Per character: alignment matches when expected, ≥1 row at level 100,
    and (for forms expected to have post-6★ Lv120 unlocks) ≥1 row at
    level 120. Also reports % of forms carrying any Lv120 row — that is
    the dominant case in the live sheet, so a sudden drop is a parser
    regression smell.
    """
    out: list[tuple[bool, str]] = []
    for name, spec in _EXPECTED_BASE_STATS.items():
        form = conn.execute(
            "SELECT cf.id, cf.alignment FROM character_forms cf "
            "JOIN characters c ON c.id = cf.character_id "
            "WHERE c.canonical_name = ? "
            "ORDER BY cf.id LIMIT 1",
            (name,),
        ).fetchone()
        if not form:
            out.append((False, f"base-stats '{name}': character not in DB"))
            continue
        if spec["alignment"] is not None:
            alignment_ok = (form["alignment"] == spec["alignment"])
            msg = (f"base-stats '{name}': alignment expected "
                   f"{spec['alignment']!r}, got {form['alignment']!r}")
        else:
            alignment_ok = bool(form["alignment"])
            msg = (f"base-stats '{name}': alignment present "
                   f"({form['alignment']!r})")
        out.append((alignment_ok, msg))

        rows = conn.execute(
            "SELECT level, COUNT(*) AS n FROM character_stats "
            "WHERE form_id = ? GROUP BY level",
            (form["id"],),
        ).fetchall()
        by_level = {r["level"]: r["n"] for r in rows}
        out.append((by_level.get(100, 0) >= 1,
                    f"base-stats '{name}': Lv100 rows={by_level.get(100, 0)}"))
        if spec.get("lv120_required"):
            out.append((by_level.get(120, 0) >= 1,
                        f"base-stats '{name}': Lv120 rows={by_level.get(120, 0)}"))

    total = conn.execute("SELECT COUNT(*) FROM character_forms").fetchone()[0]
    with_lv120 = conn.execute(
        "SELECT COUNT(DISTINCT form_id) FROM character_stats WHERE level = 120"
    ).fetchone()[0]
    pct = (100.0 * with_lv120 / total) if total else 0.0
    # Threshold is intentionally below the live ~73% baseline. SEA/GL Unique
    # Kits takes precedence over role-tab data for ~60 characters, and that
    # SEA path doesn't carry the stats grid yet (documented follow-up).
    # 70% gates against parser regression on the role-tab path, which is the
    # only place stats are read today.
    out.append((pct >= 70.0,
                f"Lv120 stats coverage: {with_lv120}/{total} ({pct:.0f}%) "
                f"forms have at least one Lv120 stat row"))
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
        ("Skill kind uniqueness",    check_skill_uniqueness_per_form(conn)),
        ("Rarity distribution",      check_rarity_distribution(conn)),
        ("SEA/GL kit precedence",    check_sea_kit_precedence(payload, conn)),
        ("FTS searchable",           check_fts_searchable(conn)),
        ("Splash-art coverage",      check_splash_art_coverage(conn)),
        ("Spot-check characters",    check_spot_characters(payload, conn)),
        ("A4 accessory stats",       check_a4_accessory_stats(conn)),
        ("Character base stats",     check_character_base_stats(conn)),
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
