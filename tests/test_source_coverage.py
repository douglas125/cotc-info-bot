"""Source-shaped regressions for the November production audit."""
from __future__ import annotations

import asyncio

import pytest

import config
from bot import enemy_embeds
from bot.enemy_views import EnemyView
from db import repo
from enemy_ranks import normalize_rank, rank_label, rank_order
from sync import enemy_parsers as enemies, parsers, runner
from sync.fetch import iter_rows, sheet_by_gid
from tests.enemy_fixtures import cell, sheet, enemy_payload, fetch_with_enemies
from tests.test_runner import _index_sheet_with, _block_rows
from tests.test_parsers import _stats_block_rows, _make_role_sheet
from tests.test_enemy_source_migration import _sync_with
from verify.check import check_source_form_coverage
from verify.check_enemies import check_tab_inventory


@pytest.mark.parametrize('index_name,source_name', [
    ('Phenn ⚔️', 'Phenn'), ('Lynette EX ⚔️', 'Lynette EX'),
    ('Levina EX ⚔️', 'Levina EX'), ('Mooloo', 'Molu'),
])
def test_global_kits_attach_to_index_without_duplicate_forms(tmp_db_path, monkeypatch, offline_sprite_refresh,
                                                          index_name, source_name):
    payload = {'sheets': [_index_sheet_with((index_name, 'warrior')),
                         sheet(519845584, 'Warriors', _block_rows(index_name, 'Old JP kit')),
                         sheet(parsers.SEA_GID, 'Global Unique Kits', _block_rows(source_name, 'Global kit'))]}
    monkeypatch.setattr(repo, 'DB_PATH', tmp_db_path)
    monkeypatch.setattr(runner, 'fetch_spreadsheet', fetch_with_enemies(payload))
    runner.run_sync('test-key')
    conn = repo.connect(tmp_db_path)
    forms = list(conn.execute('SELECT * FROM character_forms'))
    assert len(forms) == 1
    assert forms[0]['display_name'] == index_name
    assert forms[0]['variant_kind'] == ('ex' if ' EX' in index_name else 'base')
    assert [r['description'] for r in repo.get_skills(conn, forms[0]['id'])] == ['Global kit']
    assert all(ok for ok, _ in check_source_form_coverage(payload, conn))
    # Verifier must reject an empty kit and a duplicate under its source spelling.
    conn.execute('DELETE FROM skills')
    assert not check_source_form_coverage(payload, conn)[0][0]
    ch_id = repo.upsert_character(conn, source_name, 'warrior', 'sword')
    repo.insert_form(conn, character_id=ch_id, display_name=source_name, rarity='5*',
                     variant_kind='base', server='sea', sheet_gid=parsers.SEA_GID,
                     source_row=0, name_color_hex=None, hyperlink_url=None)
    assert not check_source_form_coverage(payload, conn)[2][0]
    conn.close()


def _image_stat_rows():
    names = ('HP', 'SP', 'ATK', 'DEF', 'MAG', 'MDEF', 'CRIT', 'SPD')
    rows = _stats_block_rows('Barbara', alignment='Glory', lv100=None,
                             lv120=dict(zip(names, (4612, 376, 669, 443, 302, 326, 478, 280))),
                             lv120_col=3)
    for row in rows[3:]:
        row[0] = {}  # Inserted image labels do not exist in the API response.
    footer = [{} for _ in rows[0]]
    footer[0] = cell('Resists')
    rows.append(footer)
    return rows


def test_image_label_stats_use_complete_template_and_preserve_empty_level():
    block, = parsers.parse_role_tab(_make_role_sheet(_image_stat_rows()), gid=519845584)
    assert block.warnings == []
    assert {(s['level'], s['stat_name']): s['stat_value'] for s in block.stats} == {
        (120, 'HP'): 4612, (120, 'SP'): 376, (120, 'ATK'): 669, (120, 'DEF'): 443,
        (120, 'MAG'): 302, (120, 'MDEF'): 326, (120, 'CRIT'): 478, (120, 'SPD'): 280,
    }


@pytest.mark.parametrize('damage', ['footer', 'missing_value', 'conflicting_label', 'text_value'])
def test_image_label_stats_reject_ambiguous_grids(damage):
    rows = _image_stat_rows()
    if damage == 'footer':
        rows[-1][0] = cell('Other info')
    elif damage == 'missing_value':
        rows[5][3] = {}
    elif damage == 'conflicting_label':
        rows[4][0] = cell(formula='=HP')
    else:
        rows[5][3] = cell('???')
    block, = parsers.parse_role_tab(_make_role_sheet(rows), gid=519845584)
    assert not any(s['stat_name'] == 'SPD' for s in block.stats)
    assert block.warnings


def _heroes_payload():
    payload = enemy_payload()
    catalog = [[cell(), cell('Difficulty'), cell('Shields'), cell('HP'), cell('Speed')]]
    for name, values in [('Bokhohn', ('100', '200', '300')), ('Large Puppet', ('', '400', '500'))]:
        for i, difficulty in enumerate((2, 4, 5)):
            catalog.append([cell(name if i == 0 else ''), cell(f'Difficulty {difficulty}'),
                            cell('30' if values[i] else ''), cell(values[i]), cell('500' if values[i] else '')])
    display = [[cell() for _ in range(13)] for _ in range(16)]
    display[3][1], display[3][4] = cell('Bokhohn'), cell('Difficulty 5')
    display[3][12] = cell(bg='#222222')
    display[7][2] = cell('300', "=VLOOKUP(E4,'Seven Heroes Data'!B2:E4,3,FALSE)")
    display[7][3] = cell('500', "=VLOOKUP(E4,'Seven Heroes Data'!B5:E7,3,FALSE)")
    display[6][8], display[7][8] = cell(formula='=Bow'), cell(formula='=Sword')
    replacements = {116895487: sheet(116895487, 'Seven Heroes', display),
                    959055652: sheet(959055652, 'Seven Heroes Data', catalog)}
    payload['sheets'] = [replacements.get(s['properties']['sheetId'], s) for s in payload['sheets']]
    return payload


def test_bokhohn_difficulty_two_has_only_bokhohn():
    result = enemies.parse_all(_heroes_payload(), config.ENEMY_DATA_TAB_GIDS)
    hero = next(e for e in result.enemies if e.category == 'Seven Heroes')
    assert result.unmatched == []
    assert list(hero.rank_stats) == ['Difficulty2', 'Difficulty4', 'Difficulty5']
    assert [(r['member_name'], r['stat_value']) for r in hero.rank_stats['Difficulty2'] if r['stat_name'] == 'HP'] == [
        ('Bokhohn', '100')]
    assert [(r['member_name'], r['stat_value']) for r in hero.rank_stats['Difficulty5'] if r['stat_name'] == 'HP'] == [
        ('Bokhohn', '300'), ('Large Puppet', '500')]
    assert hero.weaknesses_by_position == [['Bow'], ['Sword']]
    assert hero.weaknesses_for_rank('Difficulty2') == [['Bow'], []]
    assert result.warnings == []


def test_bokhohn_difficulty_one_has_no_puppet_even_if_placeholder_stats_exist():
    members = [enemies.MemberRanks('Bokhohn', {'Difficulty1': {'HP': '100'}}),
               enemies.MemberRanks('Large Puppet', {'Difficulty1': {'HP': '999'}})]
    result = enemies.ParseResult()
    rows = enemies._member_stats(members, 'Bokhohn', result, data_key='Seven Heroes')['Difficulty1']
    assert [(r['position'], r['member_name']) for r in rows] == [(0, 'Bokhohn')]


def test_unrelated_fights_keep_extra_source_weakness_rows():
    payload = enemy_payload(canonical='Gonska the Clown')
    rows = iter_rows(sheet_by_gid(payload, config.ENEMIES_TABS[0].gid))
    rows[7][8] = cell(formula='=Spear')
    rows[8][8] = cell(formula='=Fire')
    enemy, = enemies.parse_all(payload, config.ENEMY_DATA_TAB_GIDS).enemies
    assert enemy.weaknesses_for_rank('EX3') == [['Sword'], ['Spear'], ['Fire']]


@pytest.mark.parametrize('name,data_key', [('Other fight', 'Seven Heroes'), ('Bokhohn', 'Osterra')])
def test_bokhohn_exception_does_not_hide_missing_members_elsewhere(name, data_key):
    members = [enemies.MemberRanks('Bokhohn', {'Difficulty2': {'HP': '100'}}),
               enemies.MemberRanks('Large Puppet', {'Difficulty2': {}})]
    result = enemies.ParseResult()
    assert enemies._member_stats(members, name, result, data_key=data_key) == {}
    assert any('incomplete member HP' in warning for warning in result.warnings)


def test_invalid_difficulty_catalog_rolls_back_existing_mirror(tmp_db_path, monkeypatch, offline_sprite_refresh):
    _sync_with(monkeypatch, tmp_db_path, enemy_payload())
    conn = repo.connect(tmp_db_path)
    repo.upsert_character(conn, 'Preserve character on rollback', 'warrior', 'sword')
    before = [tuple(r) for r in conn.execute('SELECT * FROM enemy_member_stats')]
    payload = _heroes_payload()
    catalog = sheet_by_gid(payload, 959055652)
    rows = iter_rows(catalog)
    rows.append([cell(), cell('Difficulty 5'), cell('30'), cell('999'), cell('500')])
    catalog['data'][0]['rowData'] = [{'values': row} for row in rows]
    with pytest.raises(ValueError, match='Duplicate difficulty'):
        _sync_with(monkeypatch, tmp_db_path, payload)
    assert [tuple(r) for r in conn.execute('SELECT * FROM enemy_member_stats')] == before
    assert conn.execute("SELECT 1 FROM characters WHERE canonical_name='Preserve character on rollback'").fetchone()
    conn.close()


@pytest.mark.parametrize('raw,expected', [(' Difficulty 5 ', 'Difficulty5'), ('difficulty10', 'Difficulty10'),
                                         ('Difficulty 0', None), ('Difficulty 01', None), ('Difficulty -1', None)])
def test_difficulty_normalization(raw, expected):
    assert normalize_rank(raw) == expected


def test_difficulty_dropdown_and_embed_use_highest_available(tmp_db_path, monkeypatch, offline_sprite_refresh):
    _sync_with(monkeypatch, tmp_db_path, _heroes_payload())
    conn = repo.connect(tmp_db_path)
    enemy_id = conn.execute("SELECT id FROM enemies WHERE canonical_name='Bokhohn'").fetchone()[0]
    ranks = enemy_embeds.available_ranks(conn, enemy_id)
    assert ranks == ['Difficulty5', 'Difficulty4', 'Difficulty2']
    assert enemy_embeds.default_rank(ranks) == 'Difficulty5'
    assert enemy_embeds.build_enemy_embed(conn, enemy_id, 'Difficulty4').title.endswith('Difficulty 4')
    assert rank_order('Difficulty10') > rank_order('Difficulty5')
    assert rank_label('Difficulty5') == 'Difficulty 5'
    lower = repo.get_enemy_form_by_rank(conn, enemy_id, 'Difficulty2')
    assert {r['member_name'] for r in repo.get_enemy_member_stats(conn, lower['id'])} == {'Bokhohn'}
    assert {r['position'] for r in repo.get_enemy_weaknesses(conn, lower['id'])} == {0}
    async def labels():
        view = EnemyView(enemy_id, ranks, 'Difficulty5')
        assert [o.label for o in view.children[0].options] == ['Difficulty 5', 'Difficulty 4', 'Difficulty 2']
        view.stop()
    asyncio.run(labels())
    conn.close()


def _indirect_npc_payload():
    payload = enemy_payload()
    display = [[cell() for _ in range(13)] for _ in range(16)]
    display[3][0], display[3][1], display[3][12] = cell(bg='#222222'), cell('Otherworldly Traveler'), cell(bg='#222222')
    display[7][0] = cell('D')
    for pos, target_row in enumerate((3, 4)):
        display[7][2 + pos] = cell('138500000' if pos == 0 else '99999',
                                  f'=INDIRECT("\'140 NPCs Data\'!"&CONCAT($A8,"{target_row}"))')
        display[6 + pos][8] = cell(formula='=Spear' if pos == 0 else '=Dark')
    catalog = [[], [cell(), cell('NPC Name'), cell('Shields'), cell('HP')],
               [cell(), cell('Al'), cell('55'), cell('138500000')],
               [cell(), cell('Soul of Protection'), cell('99'), cell('99999')]]
    replacements = {1358661359: sheet(1358661359, '140 NPCs', display),
                    493800596: sheet(493800596, '140 NPCs Data', catalog)}
    payload['sheets'] = [replacements.get(s['properties']['sheetId'], s) for s in payload['sheets']]
    return payload


def test_indirect_npc_uses_catalog_stats_for_both_positions():
    result = enemies.parse_all(_indirect_npc_payload(), config.ENEMY_DATA_TAB_GIDS)
    npc = next(e for e in result.enemies if e.is_npc)
    assert result.unmatched == []
    assert [(r['position'], r['member_name'], r['stat_value']) for r in npc.rank_stats['Default'] if r['stat_name'] == 'HP'] == [
        (0, 'Al', '138500000'), (1, 'Soul of Protection', '99999')]
    assert npc.weaknesses_by_position == [['Spear'], ['Dark']]


@pytest.mark.parametrize('formula', [
    '=INDIRECT("other"&RAND())', '=INDIRECT("\'140 NPCs Data\'!"&CONCAT($A999,"3"))',
    '=INDIRECT("https://example.com/"&CONCAT($A8,"3"))',
])
def test_indirect_resolver_leaves_unknown_expressions_unresolved(formula):
    rows = [[cell('D')] for _ in range(8)]
    assert enemies._resolved_member_formula(formula, rows) == formula


def test_new_enemy_data_tab_fails_inventory_verification():
    payload = enemy_payload()
    payload['sheets'].append(sheet(12345, 'New fights Data', []))
    assert not check_tab_inventory(payload)[0]
    assert any('New fights Data' in w for w in enemies.parse_all(payload, config.ENEMY_DATA_TAB_GIDS).warnings)


def test_spoiler_tab_uses_orsterra_catalog():
    payload = enemy_payload(canonical='Brigand Leader Slevy')
    widget = sheet_by_gid(payload, config.ENEMIES_TABS[0].gid)
    replacements = {601882043: sheet(601882043, '(SPOILERS) Lvl 75', iter_rows(widget)),
                    config.ENEMIES_TABS[0].gid: sheet(config.ENEMIES_TABS[0].gid, 'Lvl 1', [])}
    payload['sheets'] = [replacements.get(s['properties']['sheetId'], s) for s in payload['sheets']]
    result = enemies.parse_all(payload, config.ENEMY_DATA_TAB_GIDS)
    enemy, = result.enemies
    assert result.unmatched == []
    assert enemy.canonical_name == 'Brigand Leader Slevy'
    assert enemy.category == '(SPOILERS) Lvl 75'
    assert enemy.region == 'Osterra'


def test_refresh_reply_exposes_sprite_failure(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    import discord
    from discord import app_commands
    from bot import commands

    monkeypatch.setattr(commands, '_refresh_lock', None)
    monkeypatch.setattr(commands, '_is_admin', lambda _: True)
    monkeypatch.setattr(config, 'get_setting', lambda *args: 'test-key')
    monkeypatch.setattr(runner, 'run_sync', lambda key: {'sprite_warnings': ['Sprite refresh skipped']})
    async def invoke():
        client = discord.Client(intents=discord.Intents.none())
        tree = app_commands.CommandTree(client)
        commands.register(tree)
        interaction = SimpleNamespace(user=SimpleNamespace(id=123),
                                      response=SimpleNamespace(defer=AsyncMock()),
                                      followup=SimpleNamespace(send=AsyncMock()))
        await tree.get_command('refresh').callback(interaction)
        message = interaction.followup.send.call_args.args[0]
        assert 'sprite_warnings=1' in message and 'sprites incomplete' in message
        assert interaction.followup.send.call_args.kwargs['ephemeral'] is True
        await client.close()
    asyncio.run(invoke())


def test_sprite_failure_is_reported_even_without_progress_callback(tmp_db_path, monkeypatch, offline_sprite_refresh, caplog):
    def fail(conn):
        raise RuntimeError('unreviewed wiki sprite collision')
    monkeypatch.setattr('scripts.refresh_sprite_urls.refresh_sprite_urls', fail)
    result = _sync_with(monkeypatch, tmp_db_path, enemy_payload())
    assert result['status'] == 'ok'
    assert result['sprite_warnings'] == ['Sprite refresh skipped: unreviewed wiki sprite collision']
    assert 'Sprite refresh skipped' in caplog.text
