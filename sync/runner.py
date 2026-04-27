"""Orchestrates fetch + parse + persist as a single sync run."""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from typing import Any, Callable

from config import NAME_ALIASES, ROLE_TABS, TABS_BY_GID, canonicalize_name
from db import repo
from sync.fetch import fetch_spreadsheet, sheet_by_gid
from sync.parsers import (
    Anchor,
    IndexEntry,
    parse_anchor,
    parse_index,
    parse_role_tab,
    parse_sea_unique,
)


def _levenshtein(a: str, b: str) -> int:
    """Plain DP Levenshtein distance. Inline to avoid an extra dependency."""
    a = a.lower(); b = b.lower()
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(
                prev[j] + 1,           # delete
                cur[j - 1] + 1,        # insert
                prev[j - 1] + (ca != cb),  # replace
            )
        prev = cur
    return prev[-1]


ProgressCB = Callable[[str], None]


def _noop(_msg: str) -> None:
    return None


def run_sync(api_key: str, *, progress: ProgressCB = _noop) -> dict[str, Any]:
    """Run a full sync. Returns a summary dict."""
    progress("Connecting to local DB and applying schema...")
    conn = repo.connect()
    repo.bootstrap(conn)
    run_id = repo.start_sync_run(conn)

    try:
        progress("Fetching spreadsheet from Google Sheets API...")
        payload = fetch_spreadsheet(api_key)
        progress("Persisting raw snapshot...")
        repo.store_raw_snapshot(conn, run_id, payload)

        progress("Parsing Characters Index...")
        index_sheet = sheet_by_gid(payload, 1917707422)
        if index_sheet is None:
            raise RuntimeError("Characters Index tab (gid=1917707422) not found in payload.")
        index_entries = parse_index(index_sheet)
        progress(f"  Found {len(index_entries)} index entries.")

        progress("Parsing role tabs...")
        # Map (canonical_name → list of (tab_gid, FormBlock)) so we can merge
        # role-tab data into Index entries by name. Also keep blocks_by_tab
        # so we can fuzzy-fall-back within the right tab when names disagree.
        # Index each block under BOTH its raw role-tab spelling and its
        # canonicalized spelling (per config.NAME_ALIASES) so Index lookups
        # find blocks that the community sheet spelled differently.
        blocks_by_name: dict[str, list[tuple[int, Any]]] = defaultdict(list)
        blocks_by_tab: dict[int, list[Any]] = defaultdict(list)
        for tab in ROLE_TABS:
            sheet = sheet_by_gid(payload, tab.gid)
            if sheet is None:
                progress(f"  WARN: tab {tab.name} ({tab.gid}) missing from payload")
                continue
            blocks = parse_role_tab(sheet, gid=tab.gid)
            for b in blocks:
                blocks_by_name[b.display_name].append((tab.gid, b))
                canon = canonicalize_name(b.display_name)
                if canon != b.display_name:
                    blocks_by_name[canon].append((tab.gid, b))
                blocks_by_tab[tab.gid].append(b)
            progress(f"  {tab.name}: {len(blocks)} character blocks")

        progress("Parsing SEA/GL Unique Kits...")
        sea_sheet = sheet_by_gid(payload, 291065169)
        sea_names = set(parse_sea_unique(sea_sheet)) if sea_sheet else set()
        progress(f"  Flagged {len(sea_names)} characters for SEA variants (best-effort).")

        progress("Writing into SQLite (transactional)...")
        with repo.transaction(conn):
            repo.clear_data_tables(conn)
            for entry in index_entries:
                ch_id = repo.upsert_character(
                    conn, canonical_name=entry.canonical_name,
                    base_role=entry.role, base_weapon=entry.weapon,
                )
                form_id = repo.insert_form(
                    conn,
                    character_id=ch_id,
                    display_name=entry.canonical_name,
                    rarity=entry.rarity,
                    variant_kind=_variant_kind_for(entry.canonical_name),
                    server="global",
                    sheet_gid=entry.sheet_gid,
                    source_row=entry.source_row,
                    name_color_hex=entry.color_hex,
                    hyperlink_url=entry.hyperlink_url,
                )
                # role-tab data: pick the block whose tab matches the entry's
                # role; if multiple, prefer the rarity-matching one (⭐5 vs 3&4).
                candidates = blocks_by_name.get(entry.canonical_name, [])
                block = _select_block_for(entry, candidates, blocks_by_tab)
                if block is not None:
                    if block.level_cap is not None:
                        conn.execute(
                            "UPDATE character_forms SET level_cap = ? WHERE id = ?",
                            (block.level_cap, form_id),
                        )
                    repo.insert_skills(conn, form_id, block.skills)
                    repo.insert_equipment(conn, form_id, block.equipment)
                    if block.splash_art_url or block.self_buffs_text:
                        repo.upsert_profile(
                            conn, form_id,
                            splash_art_url=block.splash_art_url,
                            self_buffs_text=block.self_buffs_text,
                        )
                # SEA flag
                if entry.canonical_name in sea_names:
                    repo.insert_form(
                        conn,
                        character_id=ch_id,
                        display_name=entry.canonical_name,
                        rarity=entry.rarity,
                        variant_kind=_variant_kind_for(entry.canonical_name),
                        server="sea",
                        sheet_gid=entry.sheet_gid,
                        source_row=entry.source_row,
                        name_color_hex=entry.color_hex,
                        hyperlink_url=entry.hyperlink_url,
                    )
            progress("Rebuilding FTS index...")
            repo.rebuild_fts(conn)

        c = repo.counts(conn)
        repo.finish_sync_run(
            conn, run_id, status="ok",
            forms_count=c["character_forms"],
            skills_count=c["skills"],
        )
        progress(f"Sync OK. Forms={c['character_forms']} Skills={c['skills']} "
                 f"Equipment={c['equipment']} Affinities={c['character_affinities']}.")
        return {"run_id": run_id, "status": "ok", **c}

    except Exception as exc:
        repo.finish_sync_run(conn, run_id, status="error", error=str(exc))
        raise
    finally:
        conn.close()


def _select_block_for(entry, candidates, blocks_by_tab):
    """Pick the FormBlock that best matches an Index entry.

    Strategy:
    1. Exact-name match in same role+rarity-band tab (preferred).
    2. Exact-name match on any tab matching the entry's role.
    3. Fuzzy-name match (similarity ≥ 0.85) within the role+rarity-band tab —
       handles community-sheet typos like 'Fior' vs 'Fiore' or 'Krauser' vs
       'Clauser'. Only ever fuzzy-matches inside the correct tab so we can't
       accidentally bind to a different character on a different role/rarity.
    """
    want_band = "5*" if entry.rarity == "5*" else "34"

    if candidates:
        # 1. exact match in the right band
        for gid, block in candidates:
            tab = TABS_BY_GID.get(gid)
            if tab and tab.rarity_band == want_band and tab.role == entry.role:
                return block
        # 2. exact match in same role, any band
        for gid, block in candidates:
            tab = TABS_BY_GID.get(gid)
            if tab and tab.role == entry.role:
                return block
        # last resort: first candidate
        if len(candidates) == 1:
            return candidates[0][1]

    # 3. fuzzy match within the matching role+band tab — accept distance ≤ 2
    # (catches typos like 'Fior'↔'Fiore' and 'Krauser'↔'Clauser', both d=1).
    target_gid = next(
        (t.gid for t in TABS_BY_GID.values()
         if t.kind == "role" and t.role == entry.role and t.rarity_band == want_band),
        None,
    )
    if target_gid is None:
        return None
    pool = blocks_by_tab.get(target_gid, [])
    if not pool:
        return None
    best: Any = None
    best_dist = 999
    for b in pool:
        d = _levenshtein(entry.canonical_name, b.display_name)
        if d < best_dist:
            best_dist = d
            best = b
    if best is not None and best_dist <= 2 and best_dist < min(
        len(entry.canonical_name), len(best.display_name)
    ) // 2 + 1:
        return best
    return None


def _variant_kind_for(name: str) -> str:
    n = name.lower()
    if n.startswith("ex2 "):
        return "ex2"
    if n.startswith("ex "):
        return "ex"
    if "saint of" in n or "(alt)" in n:
        return "alt"
    return "base"
