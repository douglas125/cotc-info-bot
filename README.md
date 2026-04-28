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
| `/search role weapon rarity weakness text` | anyone | Top-10 results. All five parameters are optional, all use live-DB autocomplete. `text` is FTS over skills, equipment, and names. |
| `/refresh` | admin | Re-syncs character + enemy spreadsheets in one transaction. Refuses if a refresh is already in flight. |
| `/feedback text:<≤2000>` | anyone | Logs a correction or inconsistency report. Rate-limited to 3 submissions / 60 s per user (persisted in SQLite, survives restarts). Reply is ephemeral. |
| `/feedback_list [limit:1-25]` | admin | Ephemeral embed of the newest feedback rows. |
| `/feedback_clear confirm:bool` | admin | Deletes all feedback rows. No-op unless `confirm:true`. |

Admin gating is by Discord user ID — see `BOT_ADMIN_USER_IDS` below.

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

1. Create an application at <https://discord.com/developers/applications>.
2. **Bot** tab → reset token → put it in `DISCORD_BOT_TOKEN`.
3. **OAuth2 → URL Generator** → scopes `bot` + `applications.commands`,
   permissions `Send Messages` + `Embed Links`. Open the generated URL to
   invite the bot to your guild.

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
sync/            Sheets fetch · parse · transactional persist
db/              SQLite schema (incl. FTS5) + repo helpers
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

The bot mirrors two community spreadsheets — see
[`INFO_SOURCES.md`](INFO_SOURCES.md) for sheet IDs and tab inventory.

- **Character data** is community-maintained. Spelling drift, EX-variant
  layout, and merged cells are all observed in the wild; the parser
  reconciles names via an alias map plus a Levenshtein-2 fallback.
- **Enemy data** comes from the *Adversary Log CotC* sheet maintained by
  `:/Silence` and contributors.
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
