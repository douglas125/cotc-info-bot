"""Accessory persistence and local search. All writes belong to sync."""
from accessories import TIERS, effect_matches, effect_tokens, name_key, text_query


def replace(conn, items, run_id):
    # Caller owns the transaction, including the other sheet-derived tables.
    conn.execute('DELETE FROM accessories_fts')
    conn.execute('DELETE FROM accessory_effects')
    conn.execute('DELETE FROM accessories')
    for item in items:
        row = dict(item, sync_run_id=run_id)
        columns = ','.join('"' + k + '"' for k in row)
        conn.execute(f'INSERT INTO accessories ({columns}) VALUES ({",".join("?" for _ in row)})', list(row.values()))
        matches = effect_matches(item['description']) if item['verification'] == 'verified_text' else {}
        conn.executemany('INSERT INTO accessory_effects VALUES (?, ?, ?)',
                         [(item['accessory_id'], key, line) for key, line in matches.items()])
        conn.execute('INSERT INTO accessories_fts VALUES (?, ?, ?, ?, ?, ?)', (
            item['accessory_id'], name_key(item['name'] + ' ' + item['in_game_name']),
            item['description'] if item['verification'] == 'verified_text' else '',
            item['stats'], name_key(item['owner'] + ' ' + item['restriction']), effect_tokens(matches)))


def get(conn, accessory_id):
    return conn.execute('SELECT * FROM accessories WHERE accessory_id = ?', (accessory_id,)).fetchone()


def choices(conn, query, *, exact=False):
    needle = name_key(query)
    rows = list(conn.execute('SELECT * FROM accessories'))
    def names(row):
        return [name_key(row['name']), name_key(row['in_game_name'])]
    if exact:
        return [r for r in rows if needle in names(r)]
    return sorted((r for r in rows if any(needle in n for n in names(r))),
                  key=lambda r: (not any(n.startswith(needle) for n in names(r)), name_key(r['name'])))


def search(conn, *, text=None, effect=None, tier=None, a4=None, gacha_a4=None, exchange=None):
    conditions, args = [], []
    query = text_query(text or '')
    if text and not query:
        return []
    join = ''
    order = ''
    if query:
        join = ' JOIN accessories_fts f ON a.accessory_id = f.accessory_id'
        conditions.append('accessories_fts MATCH ?')
        args.append(query)
        order = 'bm25(accessories_fts), '
    for column, val in [('tier', tier), ('is_a4', a4), ('gacha_a4', gacha_a4), ('exchange', exchange)]:
        if val:
            conditions.append(f'a."{column}" = ?')
            args.append(val)
    if effect:
        conditions.append('EXISTS (SELECT 1 FROM accessory_effects e WHERE e.accessory_id=a.accessory_id AND e.effect=?)')
        args.append(effect)
    where = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
    rank = 'CASE a.tier ' + ' '.join(f"WHEN '{t}' THEN {i}" for i, t in enumerate(TIERS)) + ' ELSE 7 END'
    return list(conn.execute('SELECT a.* FROM accessories a' + join + where + ' ORDER BY ' + order + rank + ', a.name COLLATE NOCASE, a.accessory_id', args))


def synced_at(conn):
    row = conn.execute('SELECT MAX(s.finished_at) FROM sync_runs s JOIN accessories a ON a.sync_run_id=s.id WHERE s.status="ok"').fetchone()
    return row[0] if row and row[0] else 'unknown'
