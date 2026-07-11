# cotc-info-bot

A Discord bot that mirrors the community-maintained *Octopath Traveler:
Champions of the Continent* spreadsheets into a local SQLite database
and surfaces them as filterable slash commands.

![tests](https://github.com/douglas125/cotc-info-bot/actions/workflows/tests.yml/badge.svg)

## Why this exists

The community sheets are excellent reference material but encode key
information visually:

- **Rarity** lives in the cell *text colour* (red = 5★, green = free 3→5★,
  yellow = 4★, blue = 3★).
- **Weakness / weapon / element** are shown as *inline icons*.
- Cross-references to skill text are *hyperlinks* that point at specific
  cells in other tabs.

Fast to read in a browser; painful to filter or cross-reference. This
bot pulls a single Sheets v4 snapshot, decodes those visual cues, indexes
the result with SQLite FTS5, and lets you ask Discord:

> `/search role:Warrior weakness:Fire text:break`

## Slash commands

| Command | Who | What it does |
|---|---|---|
| `/character name:<auto>` | anyone | Full kit, A4 accessories, profile, affinities. Section dropdown swaps among kit / equipment / profile. EX/EX2 variants resolve via prefix↔suffix swap and an alias map. |
| `/enemy name:<auto>` | anyone | Stats grid, per-position break shields, weakness labels for one encounter. Rank dropdown swaps among the available ranks (Rank 1–3 / EX1–3 for ranked enemies; single-rank for NPCs). |
| `/pet name:<auto>` | anyone | Single-screen embed: ability text, Max Boost, Turn Preparation (base / Lv10), Turn Cooldown (base / Lv5), the eight fixed stats, and the obtain string. Ambiguous English names (e.g. two "White Rabbit" entries) are disambiguated in the autocomplete via a short source hint. |
| `/search role weapon rarity weakness text` | anyone | Top-10 results. All five parameters are optional, all use live-DB autocomplete. `text` is FTS over skills, equipment, and names. |
| `/refresh` | admin | Re-syncs character, enemy, and pet spreadsheets in one transaction. Refuses if a refresh is already in flight. |
| `/feedback text:<≤2000>` | anyone | Logs a correction or inconsistency report. Rate-limited to 3 submissions / 60 s per user (persisted in SQLite, survives restarts). Reply is ephemeral. |
| `/feedback_list [limit:1-25]` | admin | Ephemeral embed of the newest feedback rows plus a per-day `/character` and `/enemy` usage breakdown for the last 10 days. |
| `/feedback_clear confirm:bool` | admin | Deletes all feedback rows. No-op unless `confirm:true`. |

Admin gating is by Discord user ID — see `BOT_ADMIN_USER_IDS` below.

### Dormant features

`/analyze_team` is implemented in source but **not** registered with
Discord — the UX needed more iteration before it was worth shipping to
users, so the slash command is currently un-hooked. The implementation
is preserved in case we revisit it:

- `bot/team_commands.py` — slash-command registration entry point
  (currently uncalled).
- `bot/team_embeds.py`, `bot/team_views.py` — embed + interactive view
  that swap between the matrix and the analysis report.
- `analysis/` — pure-Python team-analysis package
  (`aggregator`, `coverage`, `damage_estimate`, `survivability`,
  `matrix_image`, `classifier`, `insights`, `patterns`, `resolve`,
  `types`).

Even with the slash command un-hooked, `python -m analysis.audit` still
runs the same analysis offline against the local SQLite mirror. The
team-related tests under `tests/` (`test_team_analyze_integration.py`,
`test_team_views.py`, `test_matrix_image.py`) keep running as a
regression net so the dormant code does not bit-rot.

To re-enable `/analyze_team`: add `team_commands.register(tree)` (and
re-import `team_commands`) to `bot/commands.py`, redeploy. Discord
picks up the new command on the next CommandTree sync.

`/ask_ai` is also un-hooked — it was a Sonnet-4.6 SQL-tool agent that
queried the SQLite mirror via a `query_sqlite` tool and embedded the
canonical `buff_debuff/*.md` mechanics docs in its cached system
prompt. Preserved across the repo so the next iteration can pick up
where this one left off:

- `bot/ask_ai/` — agent loop, prompt assembly, SQL tool with safety
  guards (read-only URI, SELECT-only parser, row + byte caps),
  Discord-budget-aware embed renderer, constants (model, caps,
  pricing).
- `db.ai_queries` table + `repo.insert_ai_query` /
  `recent_ai_query_count` / `ai_queries_today_count` helpers.
- `tests/test_bot_ask_ai.py` — 43 tests covering SQL guard, tool-use
  loop, prompt embedding, embed chunking, rate-limit counters.
- `anthropic` in `requirements.txt` and `environment.yml`.

To re-enable `/ask_ai`: re-add the `@tree.command(name="ask_ai", …)`
block (and the matching imports) to `bot/commands.py` — the easiest
reference is the PR #63 merge commit. Set `ANTHROPIC_API_KEY` on
Railway, redeploy.

## Quickstart (local)

```bash
conda env create -f environment.yml
conda activate cotc-search
python -m sync.cli --api-key "$GOOGLE_API_KEY"   # one-time bootstrap
python -m bot
```

The first `sync.cli` run creates `data/cotc.sqlite` (~5–20 MB) from a
single `spreadsheets.get?includeGridData=true` call. Subsequent runs are
a transactional full replace — no incremental diffing.

## Configuration

Settings come from environment variables, or from a TOML file at
`~/.cotc-search/config.toml`. Env vars win when both are set.

| Variable | Required | TOML key | Purpose |
|---|---|---|---|
| `GOOGLE_API_KEY` | yes | `api_key` | Sheets v4 API key. Create one at <https://console.cloud.google.com>: enable the Sheets API, create an API key, restrict it to the Sheets API. |
| `DISCORD_BOT_TOKEN` | yes | `discord_token` | Bot token from the Discord Developer Portal. |
| `BOT_ADMIN_USER_IDS` | for admin commands | `bot_admin_user_ids` | Comma-separated Discord user IDs (e.g. `12345,67890`). Required for `/refresh`, `/feedback_list`, `/feedback_clear`. |
| `DISCORD_TEST_GUILD_ID` | optional | — | Mirrors slash commands to one guild on startup for instant propagation during dev. Global sync also runs and propagates within ~1 hour. |
| `COTC_DB_PATH` | optional | — | SQLite path. Defaults to `data/cotc.sqlite`; set to `/data/cotc.sqlite` on Railway. |

Example `~/.cotc-search/config.toml`:

```toml
api_key            = "AIza..."
discord_token      = "..."
bot_admin_user_ids = "111111111111111111,222222222222222222"
```

### Discord application setup

**Quick invite:** [Add the CotC Info Bot to a server](https://discord.com/oauth2/authorize?client_id=1498428376984322149&permissions=51200&scope=bot%20applications.commands)

The invite requests only `Send Messages`, `Embed Links`, and `Attach Files`
(used by generated `/enemy` image panels), plus the `bot` and
`applications.commands` scopes needed to install the bot and its slash
commands.

1. Create an application at <https://discord.com/developers/applications>.
2. **Bot** tab → reset token → put it in `DISCORD_BOT_TOKEN`.
3. **OAuth2 → URL Generator** → scopes `bot` + `applications.commands`,
   permissions `Send Messages` + `Embed Links` + `Attach Files`. Open the
   generated URL to invite the bot to your guild.

## Deploy (Docker / Railway)

The repo ships with a `Dockerfile` (`python:3.11-slim`,
`CMD ["python", "-m", "bot"]`) and `railway.json`.

For Railway specifically:

1. Connect the repo; Railway auto-detects the Dockerfile.
2. Add a **Volume** mounted at `/data`.
3. Set env vars: `DISCORD_BOT_TOKEN`, `GOOGLE_API_KEY`,
   `BOT_ADMIN_USER_IDS`, `COTC_DB_PATH=/data/cotc.sqlite`.
4. First boot runs a cold-start sync if `character_forms` is empty.
   Afterwards only `/refresh` mutates the DB.

Logs go to stdout via Python `logging` (configured in `bot/__main__.py`).

## Project layout

```
bot/             Discord client + slash commands + embeds + views
                 (bot/team_*.py is dormant — see "Dormant features")
sync/            Sheets fetch · parse · transactional persist
db/              SQLite schema (incl. FTS5) + repo helpers
analysis/        Team-analysis package — dormant on Discord, still
                 runnable as `python -m analysis.audit`
verify/          Live verifier — re-reads the latest raw_snapshots payload
tests/           Hermetic unit + integration tests (no network)
config.py        Sheet IDs, gid → tab map, color → rarity, env helper
environment.yml  conda env spec (Python 3.11 + deps + pytest)
requirements.txt pip manifest used by the Dockerfile
Dockerfile       python:3.11-slim image
railway.json     Railway build/deploy config
```

Schema notes and parser internals: see [`CLAUDE.md`](CLAUDE.md).

## Tests

```bash
conda activate cotc-search
pytest tests/             # hermetic — runs against synthetic payloads
python -m verify.check    # ~57 assertions vs. the latest sync snapshot
```

`verify.check` reads the most recent `raw_snapshots` row and asserts
roster completeness, parser-block coverage, plausible rarity
distribution, FTS searchability, and 8 named-character spot checks. It
needs a successful sync first (it does not call the Sheets API itself).

CI runs `pytest tests/` on Python 3.11 / 3.12 / 3.13 across Ubuntu and
Windows on every push and PR.

## Data sources

The bot mirrors three community spreadsheets — see
[`INFO_SOURCES.md`](INFO_SOURCES.md) for sheet IDs and tab inventory.

- **Character data** is community-maintained. Spelling drift, EX-variant
  layout, and merged cells are all observed in the wild; the parser
  reconciles names via an alias map plus a Levenshtein-2 fallback.
- **Enemy data** comes from the *Adversary Log CotC* sheet maintained by
  `:/Silence` and contributors.
- **Pet data** comes from the *Seed Story Content* sheet — ability text,
  base/Lv10 turn-prep, base/Lv5 cooldown, the eight fixed stats, and an
  obtain string per pet.
- **Inserted character artwork** is *not* exposed by the Sheets API and is
  intentionally not mirrored — the bot links back to the source sheet
  instead.

If you spot wrong data in the bot's output, the fastest fix is `/feedback`
(it goes to the bot admins). Corrections to the underlying data should be
proposed upstream to the spreadsheet maintainers.

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md). TL;DR: branch off `main`, run
`pytest tests/`, run `python -m verify.check` if you touched parsers, open
a PR.

## License

[MIT](LICENSE).
