"""Compare the accessory mirror to its successful source snapshot.

Run after sync: python -m verify.check_accessories
"""
import gzip
import json

from accessories import effect_matches
from db import accessories as catalog, repo
from sync.accessory_parsers import parse_accessories


def check(conn):
    stored = list(conn.execute('SELECT * FROM accessories'))
    assert stored, 'No accessories: run sync first'
    run_ids = {r['sync_run_id'] for r in stored}
    assert len(run_ids) == 1, 'Mixed accessory generations'
    run_id = next(iter(run_ids))
    run = conn.execute('SELECT status,accessories_count FROM sync_runs WHERE id=?', (run_id,)).fetchone()
    assert run and run['status'] == 'ok' and run['accessories_count'] == len(stored), 'Invalid accessory sync provenance/count'
    snapshot = conn.execute("SELECT payload_json FROM raw_snapshots WHERE sync_run_id=? AND kind='accessories'", (run_id,)).fetchone()
    assert snapshot, 'Missing accessory snapshot'
    expected, warnings = parse_accessories(json.loads(gzip.decompress(snapshot[0])))
    expected_by_id = {r['accessory_id']: r for r in expected}
    assert {r['accessory_id'] for r in stored} == set(expected_by_id), 'Source/DB ID mismatch'
    for row in stored:
        for key, value in expected_by_id[row['accessory_id']].items():
            assert row[key] == value, f'{row["name"]}: {key} differs from snapshot'
        effects = set(effect_matches(row['description'])) if row['verification'] == 'verified_text' else set()
        actual = {r[0] for r in conn.execute('SELECT effect FROM accessory_effects WHERE accessory_id=?', (row['accessory_id'],))}
        assert actual == effects, f'{row["name"]}: effect index mismatch'
    assert conn.execute('SELECT COUNT(*) FROM accessories_fts').fetchone()[0] == len(stored)
    # Independent real-world wording checks: conditional, allied, and direct cap.
    cap_names = {r['name'] for r in catalog.search(conn, effect='damage_cap')}
    for name in ('Wellsley Crest', 'Gauntlets of the Unbroken', 'Ornament of Remembrance', "Velnorte's Manuscript", "Conjurer's Purifying Rod"):
        assert name in cap_names, f'{name}: expected damage-cap effect missing'
    assert 'Arcanist\'s Mask' not in cap_names, 'Commentary leaked into effects'
    assert not any(r['name'] == 'Jet-Black Anklet' for r in catalog.search(conn, effect='bp_recovery'))
    assert not any(r['name'] == 'Cursed Shield' for r in catalog.search(conn, effect='res_down'))
    assert {r['accessory_id'] for r in catalog.search(conn, text='damage cap up')} == {
        r['accessory_id'] for r in catalog.search(conn, effect='damage_cap')}
    print(f'[OK] {len(stored)} source rows, verbatim descriptions, stable IDs and effect indexes verified')
    print(f'[OK] {len(cap_names)} damage-cap matches; synonyms agree')
    for warning in warnings:
        print(f'[WARN] {warning}')


def main():
    from config import force_utf8_console
    force_utf8_console()
    conn = repo.connect()
    try:
        check(conn)
    finally:
        conn.close()


if __name__ == '__main__':
    main()
