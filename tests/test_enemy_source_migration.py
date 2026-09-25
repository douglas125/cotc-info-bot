"""Regression coverage for growing EX ranks and the replacement source."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.usefixtures('offline_sprite_refresh')

import config
from bot import enemy_embeds
from bot.enemy_views import EnemyView
from bot.enemy_views import _EnemySelect
from db import repo
from enemy_ranks import normalize_rank, rank_label, rank_order
from sync import enemy_parsers as parser, runner
from sync.fetch import iter_rows, sheet_by_gid
from tests.enemy_fixtures import cell, sheet, enemy_payload
from verify.check_enemies import check_rank_coverage


@pytest.mark.parametrize('raw,expected', [
    ('EX4', 'EX4'), (' ex 5 ', 'EX5'), ('EX10', 'EX10'), ('Rank 3', 'Rank3'),
    ('Default', 'Default'), ('EX0', None), ('EX-1', None), ('EX01', None),
    ('Rank4', None), ('EX?', None),
])
def test_rank_normalization(raw, expected):
    assert normalize_rank(raw) == expected


def test_future_ranks_keep_all_members_and_ignore_display_name_drift():
    payload = enemy_payload(('Rank 1', 'EX3', 'EX4', 'EX5', 'EX10'), ('Leader', 'Minion'))
    result = parser.parse_all(payload, config.ENEMY_DATA_TAB_GIDS)
    assert result.unmatched == result.warnings == []
    enemy, = result.enemies
    assert enemy.canonical_name == 'New display name'
    assert list(enemy.rank_stats) == ['Rank1', 'EX3', 'EX4', 'EX5', 'EX10']
    assert [(r['position'], r['member_name'], r['stat_value']) for r in enemy.rank_stats['EX10']
            if r['stat_name'] == 'HP'] == [(0, 'Leader', '10004'), (1, 'Minion', '11004')]
    assert rank_order('EX10') > rank_order('EX5') > rank_order('EX4')


@pytest.mark.parametrize('missing', ['', '#REF!', '???', '0', '-1'])
def test_incomplete_member_prevents_partial_ex4_form(missing):
    payload = enemy_payload(('EX3', 'EX4'), ('Leader', 'Minion'), hp={(1, 'EX4'): missing})
    result = parser.parse_all(payload, config.ENEMY_DATA_TAB_GIDS)
    assert list(result.enemies[0].rank_stats) == ['EX3']
    assert any('EX4' in warning for warning in result.warnings)


def test_blank_future_rank_is_not_published_or_a_warning():
    payload = enemy_payload(('EX3', 'EX4'))
    rows = iter_rows(sheet_by_gid(payload, config.ENEMY_DATA_TAB_GIDS['Osterra']))
    rows[2][2:] = [cell(), cell(), cell()]
    result = parser.parse_all(payload, config.ENEMY_DATA_TAB_GIDS)
    assert list(result.enemies[0].rank_stats) == ['EX3']
    assert result.warnings == []


def test_decimal_formatted_integer_hp_and_invalid_optional_stat():
    payload = enemy_payload(('EX4',), hp={(0, 'EX4'): '5,827,800.00'})
    rows = iter_rows(sheet_by_gid(payload, config.ENEMY_DATA_TAB_GIDS['Osterra']))
    rows[1][4] = cell('#DIV/0!')
    result = parser.parse_all(payload, config.ENEMY_DATA_TAB_GIDS)
    values = {r['stat_name']: r['stat_value'] for r in result.enemies[0].rank_stats['EX4']}
    assert values == {'Shields': '30', 'HP': '5,827,800'}
    assert any('Speed' in warning for warning in result.warnings)


def test_npc_direct_references_use_140_catalog_and_preserve_positions():
    payload = enemy_payload()
    rows = [[cell() for _ in range(13)] for _ in range(16)]
    rows[3][0], rows[3][1], rows[3][12] = cell(bg='#222222'), cell('Twin Knights'), cell(bg='#222222')
    rows[7][2] = cell('66,000,000', "='140 NPCs Data'!D3")
    rows[7][3] = cell('88,000,000', "='140 NPCs Data'!D4")
    rows[6][8], rows[7][8] = cell(formula='=Spear'), cell(formula='=Dagger')
    catalog = [[], [cell(), cell('NPC Name'), cell('Shields'), cell('HP')],
               [cell(), cell('Erhardt'), cell('38'), cell('66000000')],
               [cell(), cell('Olberic'), cell('50'), cell('88000000')]]
    replacements = {
        1358661359: sheet(1358661359, '140 NPCs', rows),
        493800596: sheet(493800596, '140 NPCs Data', catalog),
        1230510791: sheet(1230510791, '120 NPCs Data',
                         [[], catalog[1], [cell(), cell('Erhardt'), cell('1'), cell('1')]]),
    }
    payload['sheets'] = [replacements.get(s['properties']['sheetId'], s) for s in payload['sheets']]
    result = parser.parse_all(payload, config.ENEMY_DATA_TAB_GIDS)
    npc = next(e for e in result.enemies if e.is_npc)
    assert npc.category == '140 NPCs' and list(npc.rank_stats) == ['Default']
    assert [(r['member_name'], r['stat_value']) for r in npc.rank_stats['Default'] if r['stat_name'] == 'HP'] == [
        ('Erhardt', '66000000'), ('Olberic', '88000000')]
    assert npc.weaknesses_by_position == [['Spear'], ['Dagger']]


def test_wave_formula_selects_current_branch_and_never_evaluates_unknown_expression():
    rows = [[cell('Wave 2')]]
    formula = '=IFS($A$1="Wave 1", VLOOKUP(B4,\'Data\'!B3:L8,3,FALSE),A1="Wave 2", VLOOKUP(B4,\'Data\'!B9:L14,3,FALSE),TRUE,IFERROR(1/0))'
    assert parser._data_reference(parser._selected_formula(formula, rows)) == ('Data', 8, 1)
    assert parser._selected_formula('=IF(A1="Wave 2",Sword,"")', rows) == '=Sword'
    assert parser._selected_formula('=IF(A1="Wave 1",Sword,"")', rows) == '=""'
    unknown = '=IFS(RAND()>0.5,Sword,TRUE,Wind)'
    assert parser._selected_formula(unknown, rows) == unknown
    assert parser._data_reference(formula) is None


def test_inline_fallback_includes_shields_only_for_displayed_rank():
    payload = enemy_payload(('EX4',))
    display = sheet_by_gid(payload, config.ENEMIES_TABS[0].gid)
    rows = iter_rows(display)
    rows[7][2] = cell('10,000')
    payload['sheets'] = [display]
    result = parser.parse_all(payload, {})
    assert list(result.enemies[0].rank_stats) == ['EX4']
    assert {r['stat_name']: r['stat_value'] for r in result.enemies[0].rank_stats['EX4']} == {
        'HP': '10,000', 'Shields': '30'}


def test_historical_lloris_ex3_stats_and_weaknesses():
    # Fixed values from the original user screenshot, independent of live updates.
    payload = enemy_payload(('EX3',), ('Leader Lloris', 'Mini Lloris'),
                            hp={(0, 'EX3'): '1143210', (1, 'EX3'): '822762'},
                            canonical='Sly Leader Lloris')
    data = iter_rows(sheet_by_gid(payload, config.ENEMY_DATA_TAB_GIDS['Osterra']))
    data[2][2] = cell('18')
    display = iter_rows(sheet_by_gid(payload, config.ENEMIES_TABS[0].gid))
    expected = [['Axe', 'Bow', 'Ice', 'Wind', 'Dark'], ['Dagger', 'Bow', 'Ice', 'Lightning', 'Dark']]
    # Widen the historical fixture's weakness panel to fit all five icons.
    display[3].extend([cell(), cell(bg='#222222')])
    display[3][12] = cell()
    for pos, labels in enumerate(expected):
        for offset, label in enumerate(labels):
            display[6 + pos][8 + offset] = cell(formula='=' + label)
    result = parser.parse_all(payload, config.ENEMY_DATA_TAB_GIDS)
    enemy, = result.enemies
    assert enemy.weaknesses_by_position == expected
    assert [(r['position'], r['stat_name'], r['stat_value']) for r in enemy.rank_stats['EX3']
            if r['stat_name'] in ('HP', 'Shields')] == [
        (0, 'Shields', '30'), (0, 'HP', '1143210'), (1, 'Shields', '18'), (1, 'HP', '822762')]


def _sync_with(monkeypatch, path, payload):
    from tests.accessory_fixtures import accessory_payload
    monkeypatch.setattr(repo, 'DB_PATH', path)
    characters = {'sheets': [sheet(t.gid, t.name, []) for t in config.TABS]}
    monkeypatch.setattr(runner, 'fetch_spreadsheet',
                        lambda key, sid=None: payload if sid == config.ENEMIES_SPREADSHEET_ID else
                        accessory_payload() if sid == config.ACCESSORIES_SPREADSHEET_ID else characters)
    return runner.run_sync('test-key')


def test_refresh_discovers_ex4_and_preserves_community_state(tmp_db_path, monkeypatch):
    _sync_with(monkeypatch, tmp_db_path, enemy_payload())
    conn = repo.connect(tmp_db_path)
    conn.execute("INSERT INTO feedback_submissions(submitted_at, user_id, username, feedback_text) VALUES ('2026-01-01T00:00:00Z', 123, 'Tester', 'Keep this report')")
    conn.execute("INSERT INTO command_usage_daily(usage_date, command_name, count) VALUES ('2026-01-01', 'enemy', 7)")
    notes_before = [tuple(r) for r in conn.execute('SELECT * FROM arena_fight_notes ORDER BY fight_key')]
    conn.commit()
    assert [r[0] for r in conn.execute('SELECT rank FROM enemy_forms')] == ['EX3']
    _sync_with(monkeypatch, tmp_db_path, enemy_payload(('EX3', 'EX4', 'EX10')))
    enemy_id = conn.execute('SELECT id FROM enemies').fetchone()[0]
    assert enemy_embeds.available_ranks(conn, enemy_id) == ['EX10', 'EX4', 'EX3']
    assert enemy_embeds.default_rank(enemy_embeds.available_ranks(conn, enemy_id)) == 'EX10'
    assert conn.execute('SELECT feedback_text FROM feedback_submissions').fetchone()[0] == 'Keep this report'
    assert conn.execute('SELECT count FROM command_usage_daily').fetchone()[0] == 7
    assert [tuple(r) for r in conn.execute('SELECT * FROM arena_fight_notes ORDER BY fight_key')] == notes_before
    assert conn.execute("SELECT COUNT(*) FROM enemies_fts WHERE enemies_fts MATCH 'Captain'").fetchone()[0] == 1
    assert conn.execute('SELECT COUNT(*) FROM raw_snapshots').fetchone()[0] == 8
    assert conn.execute("SELECT COUNT(*) FROM raw_snapshots WHERE kind='accessories'").fetchone()[0] == 2
    assert enemy_embeds.build_enemy_embed(conn, enemy_id, 'EX10').title.endswith('EX 10')
    assert check_rank_coverage(conn, enemy_payload(('EX3', 'EX4', 'EX10')))[0]
    conn.execute("UPDATE enemy_member_stats SET stat_value='123' WHERE stat_name='HP'")
    assert not check_rank_coverage(conn, enemy_payload(('EX3', 'EX4', 'EX10')))[0]
    conn.close()


@pytest.mark.parametrize('failure', ['missing_tab', 'empty', 'write_failure'])
def test_failed_refresh_preserves_existing_mirror(tmp_db_path, monkeypatch, failure):
    _sync_with(monkeypatch, tmp_db_path, enemy_payload())
    conn = repo.connect(tmp_db_path)
    repo.upsert_character(conn, 'Preserve on rollback', 'warrior', 'sword')
    conn.commit()
    before = [tuple(r) for r in conn.execute('SELECT * FROM enemy_member_stats')]
    payload = enemy_payload(('EX4',))
    if failure == 'missing_tab':
        payload['sheets'].pop()
    elif failure == 'empty':
        for s in payload['sheets']:
            s['data'] = []
    else:
        def fail(*args, **kwargs):
            raise RuntimeError('simulated enemy insert failure')
        monkeypatch.setattr(repo, 'insert_enemy_member_stats', fail)
    with pytest.raises((ValueError, RuntimeError)):
        _sync_with(monkeypatch, tmp_db_path, payload)
    assert [tuple(r) for r in conn.execute('SELECT * FROM enemy_member_stats')] == before
    assert conn.execute("SELECT COUNT(*) FROM characters WHERE canonical_name='Preserve on rollback'").fetchone()[0] == 1
    conn.close()


def test_dynamic_rank_dropdown_and_notes():
    view = EnemyView(1, ['EX4', 'Rank1', 'EX10', 'EX5'], 'EX10', has_fight_notes=True)
    options = view.children[0].options
    assert [o.value for o in options] == ['rank:EX10', 'rank:EX5', 'rank:EX4', 'rank:Rank1', 'notes']
    assert next(o for o in options if o.default).label == 'EX 10'
    assert all(o.description != 'Highest difficulty' for o in options)
    assert rank_label('EX5') == 'EX 5'


def test_rank_dropdown_paginates_without_losing_future_ranks():
    ranks = [f'EX{n}' for n in range(60, 0, -1)]
    seen = []
    for page in range(3):
        select = _EnemySelect(ranks, 'rank:EX60', has_fight_notes=True, page=page)
        assert len(select.options) <= 25
        assert any(o.value == 'notes' for o in select.options)
        seen.extend(o.value.removeprefix('rank:') for o in select.options if o.value.startswith('rank:'))
    assert seen == ranks


def test_ex4_callback_builds_selected_rank_and_keeps_notes(tmp_db_path, monkeypatch):
    _sync_with(monkeypatch, tmp_db_path, enemy_payload(('EX3', 'EX4')))
    conn = repo.connect(tmp_db_path)
    enemy_id = conn.execute('SELECT id FROM enemies').fetchone()[0]
    monkeypatch.setattr('bot.enemy_views.bot_db.conn', lambda: conn)
    view = EnemyView(enemy_id, ['EX3', 'EX4'], 'EX3', has_fight_notes=True)
    select = view.children[0]
    select._values = ['rank:EX4']
    interaction = SimpleNamespace(response=SimpleNamespace(edit_message=AsyncMock()))
    asyncio.run(select.callback(interaction))
    edited = interaction.response.edit_message.call_args.kwargs
    assert edited['embed'].title.endswith('EX 4')
    assert next(o.value for o in view.children[0].options if o.default) == 'rank:EX4'
    assert any(o.value == 'notes' for o in view.children[0].options)
    conn.close()


def test_page_callback_keeps_displayed_stats_without_querying_database(monkeypatch):
    view = EnemyView(1, [f'EX{n}' for n in range(60, 0, -1)], 'EX60')
    select = view.children[0]
    select._values = ['page:1']
    def unexpected_connection():
        raise AssertionError('pagination must not rebuild the displayed stats')
    monkeypatch.setattr('bot.enemy_views.bot_db.conn', unexpected_connection)
    interaction = SimpleNamespace(response=SimpleNamespace(edit_message=AsyncMock()))
    asyncio.run(select.callback(interaction))
    assert interaction.response.edit_message.call_args.kwargs == {'view': view}
    assert view.children[0].page == 1
    assert view.children[0].current == 'rank:EX60'
