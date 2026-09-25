"""Accessory lookup, filtered search and short-lived result navigation."""
from __future__ import annotations

import logging

import discord
from discord import app_commands

from accessories import EFFECTS, TIERS, effect_matches, text_query
from bot import db as bot_db
from config import ACCESSORIES_SPREADSHEET_URL, ACCESSORIES_LIST_GID
from db import accessories as catalog, repo

log = logging.getLogger(__name__)
FLAGS = [app_commands.Choice(name=v, value=v) for v in ('Yes', 'No', 'Unverified')]
EMPTY = 'Accessory data has not been synced yet. An admin can run `/refresh`.'


def source_url(row):
    return f'{ACCESSORIES_SPREADSHEET_URL}/edit#gid={ACCESSORIES_LIST_GID}&range=A{row["source_row"]}'


def choice_label(row):
    label = row['name']
    if row['owner']:
        label += ' — ' + row['owner']
    return label[:100]


def info_embeds(row, synced_at):
    """Never discard game text or comments, even if future cells grow long."""
    title = row['name'][:256]
    def new_embed(continued=False):
        e = discord.Embed(title=(title[:244] + ' (continued)') if continued else title,
                          url=source_url(row), color=discord.Color.blurple())
        e.set_footer(text=f'Accessory sync: {synced_at}')
        return e
    result = [new_embed()]
    def field(name, value, inline=False):
        value = value or '—'
        for offset in range(0, len(value), 1024):
            part = value[offset:offset + 1024]
            label = name if offset == 0 else name + ' (continued)'
            if len(result[-1]) + len(part) + len(label) > 5500 or len(result[-1].fields) >= 25:
                result.append(new_embed(True))
            result[-1].add_field(name=label, value=part, inline=inline)
    tier = row['tier'] + (' (proposed)' if row['tier_status'] == 'proposed' else '')
    a4 = row['is_a4']
    if a4 == 'Yes' and row['owner']:
        a4 += ' — ' + row['owner']
    field('Tier', tier, True)
    field('A4 accessory', a4, True)
    if row['restriction']:
        field('Exclusive to', row['restriction'])
    if row['verification'] == 'verified_text':
        description = row['description']
    elif row['verification'] == 'verified_no_effect':
        description = 'No additional effect.'
    else:
        description = 'Exact effect text not yet verified.'
    field('Description', description)
    field('Tier justification', row['comments'] or 'No tier justification provided.')
    if row['stats']:
        field('Stats', row['stats'])
    if row['in_game_name'] and row['in_game_name'] != row['name']:
        field('In-game name', row['in_game_name'])
    field('Gacha 5★ A4', row['gacha_a4'], True)
    field('Eventually in Awakening Shard Exchange', row['exchange'], True)
    if row['audit_notes']:
        field('Source notes', row['audit_notes'])
    return result


async def send_info(interaction, accessory_id, *, ephemeral=False):
    conn = bot_db.conn()
    row = catalog.get(conn, accessory_id)
    if row is None:
        await interaction.response.send_message('This item was removed by a refresh. Please search again.', ephemeral=True)
        return
    pages = info_embeds(row, catalog.synced_at(conn))
    await interaction.response.send_message(embed=pages[0], ephemeral=ephemeral, allowed_mentions=discord.AllowedMentions.none())
    for page in pages[1:]:
        await interaction.followup.send(embed=page, ephemeral=ephemeral, allowed_mentions=discord.AllowedMentions.none())


def autocomplete(conn, current):
    return [app_commands.Choice(name=choice_label(r), value='id:' + r['accessory_id'])
            for r in catalog.choices(conn, current)[:25]]


def resolve(conn, text):
    if text.startswith('id:'):
        row = catalog.get(conn, text[3:])
        return [row] if row else []
    exact = catalog.choices(conn, text, exact=True)
    return exact or catalog.choices(conn, text)


class ItemSelect(discord.ui.Select):
    def __init__(self, rows):
        super().__init__(placeholder='Open accessory details', options=[
            discord.SelectOption(label=choice_label(r), value=r['accessory_id']) for r in rows])

    async def callback(self, interaction):
        await send_info(interaction, self.values[0], ephemeral=True)


class ResultsView(discord.ui.View):
    def __init__(self, rows, user_id, *, summary, synced_at, pending, effect=None, text=None):
        super().__init__(timeout=180)
        self.rows = [dict(r) for r in rows]
        self.user_id = user_id
        self.summary = summary
        self.synced_at = synced_at
        self.pending = pending
        self.effect = effect
        self.text = text
        self.page = 0
        self.message = None
        self.update_controls()

    def update_controls(self):
        self.clear_items()
        self.previous.disabled = self.page == 0
        self.next_page.disabled = (self.page + 1) * 10 >= len(self.rows)
        self.add_item(self.previous)
        self.add_item(self.next_page)
        shown = self.rows[self.page * 10:(self.page + 1) * 10]
        if shown:
            self.add_item(ItemSelect(shown))

    def embed(self):
        e = discord.Embed(title=f'Accessories — {len(self.rows)} matches',
                          description=self.summary[:500], color=discord.Color.blurple())
        for row in self.rows[self.page * 10:(self.page + 1) * 10]:
            matches = effect_matches(row['description']) if row['verification'] == 'verified_text' else {}
            excerpt = matches.get(self.effect, '')
            if not excerpt and self.text:
                query = text_query(self.text)
                excerpt = next((v for k, v in matches.items() if 'effect' + k.replace('_', '') in query), '')
            excerpt = excerpt or (row['description'] if row['verification'] == 'verified_text' else row['stats'])
            excerpt = excerpt or ('Exact effect text not yet verified.' if row['verification'] == 'not_verified' else 'No additional effect.')
            if len(excerpt) > 180:
                excerpt = excerpt[:177] + '…'
            proposed = ' · proposed' if row['tier_status'] == 'proposed' else ''
            e.add_field(name=(row['name'] + ' · ' + row['tier'] + proposed)[:140],
                        value=f'{excerpt}\n[Source]({source_url(row)})', inline=False)
        if not self.rows:
            e.add_field(name='No matches', value='Try fewer filters or a different keyword.', inline=False)
        e.set_footer(text=f'Page {self.page + 1}/{max(1, (len(self.rows) + 9) // 10)} · '
                          f'{self.pending} items lack verified effects · Accessory sync: {self.synced_at}')
        return e

    async def interaction_check(self, interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message('Run `/accessory search` to browse your own results.', ephemeral=True)
            return False
        return True

    @discord.ui.button(label='Previous', style=discord.ButtonStyle.secondary)
    async def previous(self, interaction, button):
        self.page = max(0, self.page - 1)
        self.update_controls()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label='Next', style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction, button):
        self.page = min(max(0, (len(self.rows) - 1) // 10), self.page + 1)
        self.update_controls()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


async def send_results(interaction, rows, summary, **kwargs):
    conn = bot_db.conn()
    pending = conn.execute("SELECT COUNT(*) FROM accessories WHERE verification='not_verified'").fetchone()[0]
    view = ResultsView(rows, interaction.user.id, summary=summary, synced_at=catalog.synced_at(conn), pending=pending, **kwargs)
    await interaction.response.send_message(embed=view.embed(), view=view if rows else None,
                                            allowed_mentions=discord.AllowedMentions.none())
    if rows:
        view.message = await interaction.original_response()


def record_usage(conn, name):
    try:
        repo.increment_command_usage(conn, name)
    except Exception:
        log.exception('Could not record /accessory usage')


def register(tree):
    group = app_commands.Group(name='accessory', description='Find CotC accessories by name or effect.')

    @group.command(name='info', description='Show accessory tier, A4 status, exact effects and tier justification.')
    @app_commands.describe(name='Start typing an accessory name.')
    async def info(interaction: discord.Interaction, name: str):
        conn = bot_db.conn()
        record_usage(conn, 'accessory info')
        if not conn.execute('SELECT 1 FROM accessories LIMIT 1').fetchone():
            await interaction.response.send_message(EMPTY, ephemeral=True)
            return
        rows = resolve(conn, name)
        if len(rows) == 1:
            await send_info(interaction, rows[0]['accessory_id'])
        elif rows:
            await send_results(interaction, rows, 'Several items match. Select the accessory you mean.')
        else:
            await interaction.response.send_message('No matching accessory. Try autocomplete or `/accessory search`.', ephemeral=True)

    @info.autocomplete('name')
    async def name_autocomplete(interaction: discord.Interaction, current: str):
        return autocomplete(bot_db.conn(), current)

    @group.command(name='search', description='Search accessory names, verified effects and stats.')
    @app_commands.describe(text='Keywords, e.g. damage cap up or dagger.', effect='A verified positive effect.',
                           tier='Exact sheet tier.', a4='Any Awakening IV accessory.',
                           gacha_a4='Gacha 5-star A4 status.', exchange='Eventually obtainable from Awakening Shard Exchange.')
    @app_commands.choices(effect=[app_commands.Choice(name=v, value=k) for k, v in EFFECTS.items()],
                          tier=[app_commands.Choice(name=t, value=t) for t in TIERS],
                          a4=FLAGS, gacha_a4=FLAGS,
                          exchange=[app_commands.Choice(name=v, value=v) for v in ('Yes', 'No', 'N/A', 'Unverified')])
    async def search(interaction: discord.Interaction, text: app_commands.Range[str, 1, 200] | None = None,
                     effect: str | None = None, tier: str | None = None, a4: str | None = None,
                     gacha_a4: str | None = None, exchange: str | None = None):
        conn = bot_db.conn()
        record_usage(conn, 'accessory search')
        if not conn.execute('SELECT 1 FROM accessories LIMIT 1').fetchone():
            await interaction.response.send_message(EMPTY, ephemeral=True)
            return
        filters = dict(text=text, effect=effect, tier=tier, a4=a4, gacha_a4=gacha_a4, exchange=exchange)
        rows = catalog.search(conn, **filters)
        summary = ' · '.join(f'{k}: {EFFECTS.get(v, v)}' for k, v in filters.items() if v) or 'All accessories'
        await send_results(interaction, rows, summary, effect=effect, text=text)

    tree.add_command(group)
