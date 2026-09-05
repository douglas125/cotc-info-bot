"""Verify the enemy mirror against its latest source snapshot.

Run after sync: ``python -m verify.check_enemies``. Rank coverage follows
populated source data; missing EX4 and future-rank placeholders are valid.
Historical screenshot values live in hermetic tests, not live assertions.
"""
from __future__ import annotations

import gzip
import json

from config import ENEMIES_SPREADSHEET_ID, ENEMIES_TABS, ENEMY_DATA_TAB_GIDS, ENEMY_NPC_DATA_KEYS, force_utf8_console
from db import repo
from enemy_ranks import normalize_rank, rank_order
from sync.enemy_parsers import parse_all, parse_data_tab, parse_npc_data_tab, validate_source
from sync.fetch import sheet_by_gid


def check_tab_inventory(payload):
    if payload.get('spreadsheetId') != ENEMIES_SPREADSHEET_ID:
        return False, 'snapshot is from a different enemy source; run sync first'
    validate_source(payload, ENEMY_DATA_TAB_GIDS)
    return True, f'all {len(ENEMIES_TABS)} display + {len(ENEMY_DATA_TAB_GIDS)} data tabs present'


def check_data_tab_encounter_counts(payload):
    counts = {}
    for key, gid in ENEMY_DATA_TAB_GIDS.items():
        sheet = sheet_by_gid(payload, gid)
        parse = parse_npc_data_tab(sheet) if key in ENEMY_NPC_DATA_KEYS.values() else parse_data_tab(sheet, key)
        counts[key] = len(parse)
    return all(counts.values()), f'data catalogs parsed: {counts}'


def check_rank_coverage(conn, payload):
    """Compare actual available ranks and members, never a fixed rank count."""
    parsed = parse_all(payload, ENEMY_DATA_TAB_GIDS)
    if parsed.unmatched:
        return False, f'unresolved display members: {parsed.unmatched}'
    expected = {(e.canonical_name, e.category, e.sheet_gid): e for e in parsed.enemies}
    actual = {(e['canonical_name'], e['category'], e['sheet_gid']): e
              for e in conn.execute('SELECT * FROM enemies')}
    if not expected or expected.keys() != actual.keys():
        return False, f'encounter mismatch: missing={expected.keys() - actual.keys()}, extra={actual.keys() - expected.keys()}'
    forms = 0
    for identity, enemy in expected.items():
        stored = actual[identity]
        if stored['hyperlink_url'] != enemy.hyperlink_url or stored['source_row'] != enemy.source_row:
            return False, f'{identity}: incorrect source anchor'
        stored_forms = {f['rank']: f for f in repo.get_enemy_forms(conn, stored['id'])}
        if stored_forms.keys() != enemy.rank_stats.keys():
            return False, f'{identity}: source ranks {list(enemy.rank_stats)} != DB {list(stored_forms)}'
        for rank, rows in enemy.rank_stats.items():
            form = stored_forms[rank]
            if normalize_rank(rank) != rank or form['rank_order'] != rank_order(rank):
                return False, f'{identity}: invalid rank/order {rank}'
            wanted = {(r['position'], r['member_name'], r['stat_name'], r['stat_value']) for r in rows}
            found = {(r['position'], r['member_name'], r['stat_name'], r['stat_value'])
                     for r in repo.get_enemy_member_stats(conn, form['id'])}
            if wanted != found:
                return False, f'{identity} {rank}: member stats differ from source'
            wanted_weak = {(pos, slot, label) for pos, labels in enumerate(enemy.weaknesses_by_position)
                           for slot, label in enumerate(labels)}
            found_weak = {(r['position'], r['slot_order'], r['weakness_label'])
                          for r in repo.get_enemy_weaknesses(conn, form['id'])}
            if wanted_weak != found_weak:
                return False, f'{identity} {rank}: weaknesses differ from source'
            forms += 1
    for warning in parsed.warnings:
        print(f'[WARN] {warning}')
    return True, f'{len(expected)} encounters / {forms} forms match source ranks, members, stats, and weaknesses'


def check_npc_single_rank(conn):
    bad = conn.execute(
        "SELECT COUNT(*) FROM enemies e WHERE is_npc=1 AND ("
        "(SELECT COUNT(*) FROM enemy_forms f WHERE f.enemy_id=e.id) != 1 OR "
        "NOT EXISTS (SELECT 1 FROM enemy_forms f WHERE f.enemy_id=e.id AND rank='Default'))"
    ).fetchone()[0]
    categories = {r[0] for r in conn.execute('SELECT DISTINCT category FROM enemies WHERE is_npc=1')}
    return bad == 0 and {'120 NPCs', '140 NPCs'} <= categories, f'NPC categories={sorted(categories)}, invalid forms={bad}'


def check_stats_present(conn):
    bad = conn.execute(
        "SELECT COUNT(*) FROM enemy_forms f WHERE NOT EXISTS "
        "(SELECT 1 FROM enemy_member_stats s WHERE s.form_id=f.id AND stat_name='HP')"
    ).fetchone()[0]
    for row in conn.execute("SELECT stat_value FROM enemy_member_stats WHERE stat_name='HP'"):
        try:
            if int(row[0].replace(',', '')) <= 0:
                bad += 1
        except (ValueError, AttributeError):
            bad += 1
    return bad == 0, f'forms and HP checked; invalid={bad}'


def check_fts_searchable(conn):
    rows = conn.execute("SELECT enemy_id FROM enemies_fts WHERE enemies_fts MATCH 'Lloris*'").fetchall()
    return bool(rows), f'FTS Lloris matches={len(rows)}'


def main() -> int:
    force_utf8_console()
    conn = repo.connect()
    try:
        blob = repo.latest_raw_snapshot(conn, kind='enemies')
        if blob is None:
            raise SystemExit('No enemy snapshot; run sync first.')
        payload = json.loads(gzip.decompress(blob).decode('utf-8'))
        checks = [
            ('source/tab inventory', lambda: check_tab_inventory(payload)),
            ('data catalogs', lambda: check_data_tab_encounter_counts(payload)),
            ('source coverage', lambda: check_rank_coverage(conn, payload)),
            ('NPC forms', lambda: check_npc_single_rank(conn)),
            ('stats/HP', lambda: check_stats_present(conn)),
            ('FTS', lambda: check_fts_searchable(conn)),
        ]
        ok = True
        for label, check in checks:
            try:
                passed, detail = check()
            except Exception as exc:
                passed, detail = False, str(exc)
            print(f"{'[OK]  ' if passed else '[FAIL]'} {label}: {detail}")
            ok &= passed
        return 0 if ok else 1
    finally:
        conn.close()


if __name__ == '__main__':
    raise SystemExit(main())
