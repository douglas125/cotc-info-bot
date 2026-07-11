"""Orchestrates fetch + parse + persist as a single sync run."""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from typing import Any, Callable

from config import (
    ENEMIES_SPREADSHEET_ID,
    ENEMY_DATA_TAB_GIDS,
    PETS_LIST_GID,
    PETS_SPREADSHEET_ID,
    ROLE_TABS,
    TABS_BY_GID,
    WEAPON_TO_ROLE,
    _split_variant,
    canonical_name_keys,
    canonicalize_name,
)
from db import repo
from sync.enemy_parsers import parse_all as parse_enemies, rank_order
from sync.fetch import fetch_spreadsheet, sheet_by_gid
from sync.parsers import (
    Anchor,
    IndexEntry,
    SEA_GID,
    infer_weapon_from_block,
    parse_anchor,
    parse_index,
    parse_role_tab,
    parse_sea_kits,
)
from sync.pet_parsers import parse_pets


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
        progress("Fetching character spreadsheet from Google Sheets API...")
        payload = fetch_spreadsheet(api_key)
        progress("Persisting raw character snapshot...")
        repo.store_raw_snapshot(conn, run_id, payload, kind="characters")

        progress("Fetching enemy spreadsheet (Adversary Log CotC)...")
        enemy_payload = fetch_spreadsheet(api_key, ENEMIES_SPREADSHEET_ID)
        progress("Persisting raw enemy snapshot...")
        repo.store_raw_snapshot(conn, run_id, enemy_payload, kind="enemies")

        progress("Fetching pet spreadsheet (Seed Story Content)...")
        pets_payload = fetch_spreadsheet(api_key, PETS_SPREADSHEET_ID)
        progress("Persisting raw pet snapshot...")
        repo.store_raw_snapshot(conn, run_id, pets_payload, kind="pets")

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
        # Index each block under every key returned by canonical_name_keys —
        # raw spelling, aliased spelling, and both EX/EX2 word orders — so
        # Index lookups find blocks regardless of how the community sheet
        # spelled or ordered the variant marker.
        blocks_by_name: dict[str, list[tuple[int, Any]]] = defaultdict(list)
        blocks_by_tab: dict[int, list[Any]] = defaultdict(list)
        for tab in ROLE_TABS:
            sheet = sheet_by_gid(payload, tab.gid)
            if sheet is None:
                progress(f"  WARN: tab {tab.name} ({tab.gid}) missing from payload")
                continue
            blocks = parse_role_tab(sheet, gid=tab.gid)
            for b in blocks:
                for key in canonical_name_keys(b.display_name):
                    blocks_by_name[key].append((tab.gid, b))
                blocks_by_tab[tab.gid].append(b)
            progress(f"  {tab.name}: {len(blocks)} character blocks")

        progress("Parsing SEA/GL Unique Kits...")
        sea_sheet = sheet_by_gid(payload, SEA_GID)
        sea_blocks = parse_sea_kits(sea_sheet) if sea_sheet else []
        # Index SEA blocks under every alias-equivalent spelling, with the
        # raw display name first so SEA precedence falls on the original
        # block when an alias lookup races a same-key collision.
        sea_blocks_by_name: dict[str, Any] = {}
        for b in sea_blocks:
            for key in canonical_name_keys(b.display_name):
                sea_blocks_by_name.setdefault(key, b)
        progress(f"  Parsed {len(sea_blocks)} SEA/GL kit blocks.")

        progress("Writing into SQLite (transactional)...")
        with repo.transaction(conn):
            repo.clear_character_tables(conn)
            index_name_keys: set[str] = set()
            used_role_blocks: set[tuple[int, int, str]] = set()
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
                index_name_keys.add(entry.canonical_name.lower())
                # SEA/GL Unique Kits takes precedence: if the character has a
                # block in that tab, use it instead of the role-tab block.
                # Otherwise fall back to the role-tab block (rarity-band aware).
                block = sea_blocks_by_name.get(entry.canonical_name)
                if block is None:
                    candidates = blocks_by_name.get(entry.canonical_name, [])
                    block = _select_block_for(entry, candidates, blocks_by_tab)
                if block is not None:
                    _persist_block(conn, form_id, block)
                    if block.sheet_gid != SEA_GID:
                        used_role_blocks.add(
                            (block.sheet_gid, block.source_row, block.display_name)
                        )

            # Second pass: SEA blocks with no matching Index entry. These are
            # SEA-only EX variants (e.g. "Lynette EX") that the Index hasn't
            # caught up with — without this pass they'd be silently dropped.
            for block in sea_blocks:
                key = block.display_name.lower()
                if any(
                    k.lower() in index_name_keys
                    for k in canonical_name_keys(block.display_name)
                ):
                    continue
                base_weapon = infer_weapon_from_block(block)
                base_role = WEAPON_TO_ROLE.get(base_weapon) if base_weapon else None
                ch_id = repo.upsert_character(
                    conn, canonical_name=block.display_name,
                    base_role=base_role, base_weapon=base_weapon,
                )
                form_id = repo.insert_form(
                    conn,
                    character_id=ch_id,
                    display_name=block.display_name,
                    rarity="5*",
                    variant_kind=_variant_kind_for(block.display_name),
                    server="sea",
                    sheet_gid=SEA_GID,
                    source_row=block.source_row,
                )
                _persist_block(conn, form_id, block)
                index_name_keys.add(key)

            # Third pass: EX/EX2 forms that exist as complete role-tab blocks
            # before the Index catches up. The role tab itself is authoritative
            # for role/weapon here; keep this limited to variant forms so a
            # stray base-name typo remains visible in verify.check.
            for tab in ROLE_TABS:
                for block in blocks_by_tab.get(tab.gid, []):
                    block_key = (block.sheet_gid, block.source_row, block.display_name)
                    if block_key in used_role_blocks:
                        continue
                    key = block.display_name.lower()
                    if any(
                        k.lower() in index_name_keys
                        for k in canonical_name_keys(block.display_name)
                    ):
                        continue
                    variant_kind = _variant_kind_for(block.display_name)
                    if variant_kind not in {"ex", "ex2"}:
                        continue
                    ch_id = repo.upsert_character(
                        conn, canonical_name=block.display_name,
                        base_role=tab.role, base_weapon=tab.weapon,
                    )
                    form_id = repo.insert_form(
                        conn,
                        character_id=ch_id,
                        display_name=block.display_name,
                        rarity="5*" if tab.rarity_band == "5*" else None,
                        variant_kind=variant_kind,
                        server="global",
                        sheet_gid=block.sheet_gid,
                        source_row=block.source_row,
                    )
                    _persist_block(conn, form_id, block)
                    index_name_keys.add(key)

            progress("Rebuilding character FTS index...")
            repo.rebuild_fts(conn)

            progress("Parsing enemy spreadsheet...")
            enemy_parse = parse_enemies(enemy_payload, ENEMY_DATA_TAB_GIDS)
            for name, tab in enemy_parse.unmatched:
                progress(f"  WARN: enemy display block unmatched: '{name}' on tab '{tab}'")
            progress(f"  Parsed {len(enemy_parse.enemies)} enemies "
                     f"({sum(1 for e in enemy_parse.enemies if e.is_npc)} NPCs).")

            progress("Writing enemy data...")
            repo.clear_enemy_tables(conn)
            for enemy in enemy_parse.enemies:
                enemy_id = repo.upsert_enemy(
                    conn,
                    canonical_name=enemy.canonical_name,
                    category=enemy.category,
                    region=enemy.region,
                    sheet_gid=enemy.sheet_gid,
                    source_row=enemy.source_row,
                    name_color_hex=enemy.name_color_hex,
                    hyperlink_url=enemy.hyperlink_url,
                    is_npc=enemy.is_npc,
                )
                for rank_key, stat_rows in enemy.rank_stats.items():
                    form_id = repo.insert_enemy_form(
                        conn,
                        enemy_id=enemy_id,
                        rank=rank_key,
                        rank_order=rank_order(rank_key),
                    )
                    repo.insert_enemy_member_stats(conn, form_id, stat_rows)
                    repo.insert_enemy_weaknesses(
                        conn, form_id, enemy.weaknesses_by_position,
                    )

            progress("Rebuilding enemy FTS index...")
            repo.rebuild_enemy_fts(conn)

            progress("Parsing pet spreadsheet...")
            parsed_pets, pet_warnings = parse_pets(pets_payload, PETS_LIST_GID)
            for w in pet_warnings:
                progress(f"  WARN: {w}")
            progress(f"  Parsed {len(parsed_pets)} pets.")

            progress("Writing pet data...")
            repo.clear_pet_tables(conn)
            for pet in parsed_pets:
                repo.upsert_pet(conn, pet)

            progress("Rebuilding pet FTS index...")
            repo.rebuild_pet_fts(conn)

        # Non-fatal post-step OUTSIDE the main transaction so the HTTP
        # call doesn't hold a SQLite write lock — a wiki outage can't
        # abort an otherwise-good sync.
        try:
            from scripts.refresh_sprite_urls import refresh_sprite_urls
            sprite_summary = refresh_sprite_urls(conn)
            unmatched = len(sprite_summary["unmatched"])
            missing = len(sprite_summary["missing"])
            notes = []
            if unmatched:
                notes.append(f"{unmatched} unmatched wiki rows")
            if missing:
                notes.append(f"{missing} characters missing")
            note = f" ({', '.join(notes)})" if notes else ""
            progress(
                f"Refreshed sprites: {sprite_summary['total_mapped']}/"
                f"{sprite_summary['character_total']} characters mapped "
                f"({sprite_summary['page_mapped']} page + "
                f"{sprite_summary['overrides']} curated){note}"
            )
        except Exception as exc:
            progress(f"WARN: sprite refresh skipped: {exc}")

        c = repo.counts(conn)
        repo.finish_sync_run(
            conn, run_id, status="ok",
            forms_count=c["character_forms"],
            skills_count=c["skills"],
            enemies_count=c["enemies"],
            enemy_forms_count=c["enemy_forms"],
            pets_count=c["pets"],
        )
        progress(f"Sync OK. Forms={c['character_forms']} Skills={c['skills']} "
                 f"Equipment={c['equipment']} UniqueEffects={c['unique_effects']} "
                 f"Affinities={c['character_affinities']} "
                 f"Enemies={c['enemies']} EnemyForms={c['enemy_forms']} "
                 f"Pets={c['pets']}.")
        return {"run_id": run_id, "status": "ok",
                "unmatched_enemies": list(enemy_parse.unmatched),
                "pet_warnings": list(pet_warnings),
                **c}

    except Exception as exc:
        repo.finish_sync_run(conn, run_id, status="error", error=str(exc))
        raise
    finally:
        conn.close()


def _persist_block(conn: sqlite3.Connection, form_id: int, block: Any) -> None:
    """Write a parsed FormBlock's data onto an already-inserted form row.

    Called from each of the three persistence passes (Index attach,
    SEA-only, role-tab-only EX). The form row itself is created by the
    caller via ``repo.insert_form``; this helper attaches everything
    that hangs off ``form_id``.

    ``level_cap`` and ``alignment`` are stamped via a single UPDATE
    rather than two — they live on the same row and can never both be
    needed conditionally without one statement.
    """
    sets: list[str] = []
    params: list[Any] = []
    if block.level_cap is not None:
        sets.append("level_cap = ?")
        params.append(block.level_cap)
    if block.alignment:
        sets.append("alignment = ?")
        params.append(block.alignment)
    if sets:
        params.append(form_id)
        conn.execute(
            f"UPDATE character_forms SET {', '.join(sets)} WHERE id = ?",
            params,
        )
    repo.insert_skills(conn, form_id, block.skills)
    repo.insert_equipment(conn, form_id, block.equipment)
    repo.insert_unique_effects(conn, form_id, block.unique_effects)
    repo.insert_stats(conn, form_id, block.stats)
    if block.splash_art_url or block.self_buffs_text:
        repo.upsert_profile(
            conn, form_id,
            splash_art_url=block.splash_art_url,
            self_buffs_text=block.self_buffs_text,
        )


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
    # Compare bare names (variant marker stripped) so an EX form's word-order
    # swap doesn't blow past the threshold; we only consider blocks whose
    # variant kind matches the Index entry's.
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
    entry_prefix, entry_bare, entry_suffix = _split_variant(entry.canonical_name)
    entry_has_variant = bool(entry_prefix or entry_suffix)
    best: Any = None
    best_dist = 999
    for b in pool:
        b_prefix, b_bare, b_suffix = _split_variant(b.display_name)
        b_has_variant = bool(b_prefix or b_suffix)
        if entry_has_variant != b_has_variant:
            continue
        d = _levenshtein(entry_bare, b_bare)
        if d < best_dist:
            best_dist = d
            best = b
    if best is not None and best_dist <= 2 and best_dist < min(
        len(entry_bare), len(_split_variant(best.display_name)[1])
    ) // 2 + 1:
        return best
    return None


def _variant_kind_for(name: str) -> str:
    n = name.lower().strip()
    if n.startswith("ex2 ") or n.endswith(" ex2"):
        return "ex2"
    if n.startswith("ex ") or n.endswith(" ex"):
        return "ex"
    if "saint of" in n or "(alt)" in n:
        return "alt"
    return "base"
