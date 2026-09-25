import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from accessories import effect_matches, text_query
from bot.accessory_commands import ResultsView, autocomplete, info_embeds, register, resolve, send_info
from db import accessories as catalog, repo
from sync.accessory_parsers import HEADERS, parse_accessories
from tests.accessory_fixtures import accessory_payload


def item(**kwargs):
    return dict(dict(name='Cap charm', accessory_id='cap', tier='S',
                     description='Raise Dmg. Limit by 50,000.', verification='verified_text',
                     comments='Tier rationale', gacha_a4='No'), **kwargs)


def seed(conn, items):
    run_id = repo.start_sync_run(conn)
    parsed, _ = parse_accessories(accessory_payload(items))
    with repo.transaction(conn):
        catalog.replace(conn, parsed, run_id)
    repo.finish_sync_run(conn, run_id, status='ok', accessories_count=len(parsed))
    return parsed


@pytest.fixture
def conn(tmp_db_path):
    c = repo.connect(tmp_db_path)
    yield c
    c.close()


def test_parser_reordered_columns_and_verbatim():
    text = '  Raise Dmg. Limit by 50,000.\n When in back row: Grant a bonus. '
    result, warnings = parse_accessories(accessory_payload([item(description=text, is_a4='Yes')], list(reversed(HEADERS))))
    assert result[0]['description'] == text
    assert result[0]['is_a4'] == 'Yes'
    assert result[0]['source_row'] == 2
    assert not warnings


@pytest.mark.parametrize('entries', [[], [item(), item()], [item(accessory_id='')],
    [item(name='')], [item(description='')], [item(verification='not_verified')],
    [item(verification='invalid')], [item(gacha_a4='Yes', is_a4='No')]])
def test_parser_rejects_invalid_import(entries):
    with pytest.raises(ValueError):
        parse_accessories(accessory_payload(entries))


def test_missing_required_header():
    with pytest.raises(ValueError, match='headers missing'):
        parse_accessories(accessory_payload(headers=[h for h in HEADERS if h != 'accessory_id']))


def test_optional_a4_fallback_and_unknown_tier():
    rows, warnings = parse_accessories(accessory_payload([
        item(gacha_a4='Yes', tier='?'), item(accessory_id='other', gacha_a4='No')],
        [h for h in HEADERS if h != 'is_a4']))
    assert [r['is_a4'] for r in rows] == ['Yes', 'Unverified']
    assert rows[0]['tier'] == 'Unrated'
    assert any('is_a4' in w for w in warnings)


@pytest.mark.parametrize('flag,exchange,gacha,expected', [
    ('', 'N/A', 'No', 'No'),
    ('Unverified', 'N/A', 'No', 'No'),
    ('Yes', 'N/A', 'No', 'Yes'),  # Audited non-gacha A4 overrides exchange eligibility.
    ('Unverified', 'N/A', 'Yes', 'Yes'),
    ('Unverified', 'No', 'No', 'Unverified'),
    ('Unverified', '', 'No', 'Unverified'),
])
def test_a4_exchange_na_fallback(flag, exchange, gacha, expected):
    rows, _ = parse_accessories(accessory_payload([item(is_a4=flag, exchange=exchange, gacha_a4=gacha)]))
    assert rows[0]['is_a4'] == expected


@pytest.mark.parametrize('text,expected', [
    ('Raise Dmg. Limit by 50,000.', {'damage_cap'}),
    ('When switching: Raise Elem. Atk. of Paired Allies by 15% and their Dmg. Limit by 25,000 (turns: 1).', {'damage_cap'}),
    ('Grant Damage Limit Up (effect: 100000).', {'damage_cap'}),
    ("Raise the elemental damage limit of the equipping character's Paired Ally by 100,000.", {'damage_cap'}),
    ('When consuming 3 or more BP, raise Dmg. Limit by 100,000.', {'damage_cap'}),
    ('Limit from support skills and equipment effects: 30%.', set()),
    ('Start of battle: Prevent own BP recovery at the start of the turn (turns: 3).', set()),
    ('Raise BP Recovery by 1.', {'bp_recovery'}),
    ('Start of battle: Recover 5 BP.', {'bp_restore'}),
    ('Grant automatic HP recovery (amount: 80).', {'hp_regen'}),
    ('Grant automatic SP recovery (amount: 6) and HP recovery (amount: 100).', {'hp_regen', 'sp_regen'}),
    ('Grant SP regeneration (amount: 100) to All Allies who have an Evil Ward.', {'sp_regen'}),
    ('Start of battle: Fill own ultimate technique gauge by 100.0%.', {'ultimate'}),
    ('Once per battle, after using their Ultimate Technique, recover 1 use of it and have their Ultimate Technique gauge filled by 200.0% at the end of the turn.', {'ultimate'}),
    ('Ultimate Technique gauge increased by 200.0% (can only be used once in battle).', {'ultimate'}),
    ('Gain an HP barrier.', {'barrier'}),
    ('Grant Dead Aim.', {'dead_aim'}),
    ('Gain the ability to trigger critical hits with elemental attacks.', {'elemental_crit'}),
    ('Allows the equipping character to deal critical damage with elemental attacks.', {'elemental_crit'}),
    ('When breaking an enemy, lower their Polearm Res. by 15%.', {'res_down'}),
    ('Lower Fire, Ice, Lightning, Wind, and Dark Res. by 30%.', set()),
    ('When breaking an enemy, lower their Phys. Def. by 10%.\nand lower their Phys. Res. by 10%.', {'res_down'}),
    ('The Light Res. of All Foes will be reduced by 10% while in the front row.', {'res_down'}),
    ('Extend the duration of augmenting and enfeebling effects granted by self by 1 turn.', {'buff_duration','debuff_duration'}),
    ('Shortens duration of enfeebling effects inflicted on self by 1 turn.', set()),
])
def test_effect_rules(text, expected):
    assert set(effect_matches(text)) == expected


def test_search_verified_effects_only_filters_and_synonyms(conn):
    seed(conn, [item(is_a4='Yes', tier='S+', gacha_a4='No'),
                item(name='Arcanist mask', accessory_id='mask', description='', verification='not_verified',
                     comments='Does not provide damage cap up.'),
                item(name='Jet-Black Anklet', accessory_id='anklet', description='Prevent own BP recovery.'),
                item(name='Stat charm', accessory_id='stat', description='', verification='verified_no_effect', stats='HP 500')])
    for query in ('damage cap up', 'damage limit', 'Dmg. Limit'):
        assert [r['accessory_id'] for r in catalog.search(conn, text=query)] == ['cap']
    assert [r['accessory_id'] for r in catalog.search(conn, effect='damage_cap', tier='S+', a4='Yes', gacha_a4='No')] == ['cap']
    assert not catalog.search(conn, effect='damage_cap', gacha_a4='Yes')
    assert not catalog.search(conn, text='BP recovery')
    assert [r['accessory_id'] for r in catalog.search(conn, text='mask')] == ['mask']
    assert [r['accessory_id'] for r in catalog.search(conn, text='HP 500')] == ['stat']
    for query in ('" OR *', "'); DROP TABLE accessories; --", '*', '"', 'NOT'):
        catalog.search(conn, text=query)
    assert conn.execute('SELECT COUNT(*) FROM accessories').fetchone()[0] == 4


def test_identity_collisions_and_refresh(conn):
    items = [item(name='Stylish Scarf', in_game_name='Stylish Scarf', accessory_id='base', owner='Tiziano'),
             item(name='Stylish Scarf (Tiziano EX)', in_game_name='Stylish Scarf', accessory_id='ex', owner='Tiziano EX'),
             item(name="Traveler's Cape", in_game_name='Cape of Remembrance', accessory_id='shana'),
             item(name="Traveler's Cape (Sail)", in_game_name="Traveler's Cape", accessory_id='sail')]
    seed(conn, items)
    assert len(resolve(conn, 'stylish scarf')) == 2
    assert len(resolve(conn, 'TRAVELER’S CAPE')) == 2
    assert resolve(conn, 'Cape of Remembrance')[0]['accessory_id'] == 'shana'
    choice = autocomplete(conn, 'Stylish')[1]
    assert choice.value.startswith('id:')
    seed(conn, list(reversed(items)))
    assert resolve(conn, choice.value)[0]['accessory_id'] == choice.value[3:]
    assert catalog.get(conn, 'base')['source_row'] == 5


def test_rollback_preserves_catalog_and_fts(conn):
    parsed = seed(conn, [item()])
    with pytest.raises(Exception):
        with repo.transaction(conn):
            catalog.replace(conn, parsed * 2, 1)
    assert catalog.get(conn, 'cap')
    assert len(catalog.search(conn, effect='damage_cap')) == 1
    assert len(catalog.search(conn, text='damage cap')) == 1


def test_info_fields_exclusive_and_verbatim(conn):
    desc = 'When equipped by an Apothecary: gain Dead Aim.\n  Preserve spaces.'
    seed(conn, [item(description=desc, is_a4='Yes', owner='', restriction='Elvis only')])
    embeds = info_embeds(catalog.get(conn, 'cap'), 'today')
    fields = {f.name: f.value for e in embeds for f in e.fields}
    assert embeds[0].title == 'Cap charm'
    assert fields['Tier'] == 'S'
    assert fields['A4 accessory'] == 'Yes'
    assert fields['Description'] == desc
    assert fields['Tier justification'] == 'Tier rationale'
    assert fields['Exclusive to'] == 'Elvis only'
    seed(conn, [item(description=desc)])
    assert not any(f.name == 'Exclusive to' for e in info_embeds(catalog.get(conn, 'cap'), 'today') for f in e.fields)


def test_long_descriptions_are_not_lost(conn):
    text = ('Verbatim text with spaces.\n' * 500)
    seed(conn, [item(description=text, comments=text)])
    embeds = info_embeds(catalog.get(conn, 'cap'), 'today')
    assert len(embeds) > 1
    assert all(len(e) <= 6000 and len(e.fields) <= 25 for e in embeds)
    assert all(len(f.value) <= 1024 for e in embeds for f in e.fields)
    assert ''.join(f.value for e in embeds for f in e.fields if f.name.startswith('Description')) == text
    assert ''.join(f.value for e in embeds for f in e.fields if f.name.startswith('Tier justification')) == text


def test_autocomplete_limit_and_empty_unknown_states(conn):
    seed(conn, [item(name=f'Item {i}', accessory_id=str(i), description='', verification='verified_no_effect') for i in range(30)])
    assert len(autocomplete(conn, '')) == 25
    row = dict(catalog.get(conn, '0'))
    assert any(f.value == 'No additional effect.' for f in info_embeds(row, 'today')[0].fields)
    row['verification'] = 'not_verified'
    assert any(f.value == 'Exact effect text not yet verified.' for f in info_embeds(row, 'today')[0].fields)


def test_commands_and_view(conn):
    async def run():
        client = discord.Client(intents=discord.Intents.none())
        tree = discord.app_commands.CommandTree(client)
        register(tree)
        group = tree.get_command('accessory')
        assert {c.name for c in group.commands} == {'info', 'search'}
        assert {p.name for p in group.get_command('search').parameters} == {'text','effect','tier','a4','gacha_a4','exchange'}
        seed(conn, [item(name=f'Item {i}', accessory_id=str(i)) for i in range(21)])
        view = ResultsView(catalog.search(conn), 1, summary='Test', synced_at='today', pending=16)
        assert len(view.embed().fields) == 10
        long_rows = [dict(r, name='N' * 500, description='D' * 2000) for r in view.rows]
        long_view = ResultsView(long_rows, 1, summary='S' * 2000, synced_at='today', pending=16)
        assert len(long_view.embed()) <= 6000
        long_view.stop()
        interaction = SimpleNamespace(user=SimpleNamespace(id=1), response=SimpleNamespace(edit_message=AsyncMock(), send_message=AsyncMock()))
        await view.next_page.callback(interaction)
        assert view.page == 1
        await view.next_page.callback(interaction)
        assert len(view.embed().fields) == 1 and view.next_page.disabled
        await view.previous.callback(interaction)
        assert view.page == 1
        interaction.user.id = 2
        assert not await view.interaction_check(interaction)
        await view.on_timeout()
        assert all(c.disabled for c in view.children)
        view.stop()
        await client.close()
    asyncio.run(run())


def test_removed_selection_during_refresh(conn, monkeypatch):
    monkeypatch.setattr('bot.accessory_commands.bot_db.conn', lambda: conn)
    response = SimpleNamespace(send_message=AsyncMock())
    asyncio.run(send_info(SimpleNamespace(response=response), 'removed'))
    assert response.send_message.call_args.kwargs['ephemeral']
    assert 'removed' in response.send_message.call_args.args[0]


def test_invalid_live_source_preserves_existing_mirror(conn, tmp_db_path, monkeypatch):
    import config
    from sync import runner
    seed(conn, [item()])
    monkeypatch.setattr(repo, 'DB_PATH', tmp_db_path)
    def fetch(key, spreadsheet_id=None):
        if spreadsheet_id == config.ACCESSORIES_SPREADSHEET_ID:
            return accessory_payload([item(), item()])
        return {'sheets': []}
    monkeypatch.setattr(runner, 'fetch_spreadsheet', fetch)
    with pytest.raises(ValueError, match='duplicate ID'):
        runner.run_sync('test-key')
    assert catalog.get(conn, 'cap')['description'] == 'Raise Dmg. Limit by 50,000.'
    assert len(catalog.search(conn, text='damage cap')) == 1
    assert repo.latest_sync_run(conn)['status'] == 'error'
    assert catalog.synced_at(conn) != 'unknown'
