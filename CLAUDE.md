# CLAUDE.md — CotC Character Sheet (local search tool)

## What this project is

A local Streamlit app that mirrors a community-maintained Google Sheet for
*Octopath Traveler: Champions of the Continent* into a SQLite database, so
the user can filter and search characters by role, rarity, weakness/element,
weapon, and free-text against skills/equipment.

- **Canonical data source**: see [`INFO_SOURCES.md`](INFO_SOURCES.md) for
  the spreadsheet URL/ID, tab inventory, and a list of what the Sheets v4
  API exposes (and notably what it does *not* — inserted-image artwork).
- **Why a local copy**: the live sheet uses cell color (rarity), inline icons
  (weakness/weapon/element), and hyperlinks to encode information that is fast
  to read but painful to filter or cross-reference inside Sheets.

## Git workflow (mandatory)

Repo: `douglas125/cotc-character-search` (private, default branch `main`).

For every change, follow this flow:

1. **Branch from main** — `git checkout main && git pull && git checkout -b <branch>`. Never work directly on `main`.
2. **Do the work** — edit / add / test.
3. **Commit and push** — `git commit` then `git push -u origin <branch>`.
4. **Open a PR** — `gh pr create` with a clear summary and test plan.
5. **WAIT for the user's confirmation.** Do not merge on your own initiative even if CI is green and tests pass. Pause and report the PR URL.
6. **Merge only after explicit confirmation** — `gh pr merge --squash --delete-branch` (or as the user specifies). The `--delete-branch` flag removes the *remote* branch.
7. **Sync local main and clean up the local branch** — `git checkout main && git pull --ff-only && git branch -d <branch>`. The merged feature branch is no longer needed locally.
8. **Confirm the Railway deployment succeeded** — Railway auto-deploys on push to `main`. Poll until the latest deployment for the merge commit shows `SUCCESS`:

   ```bash
   railway deployment list --limit 3 --json | python -c "import json,sys; d=json.load(sys.stdin)[0]; print(d['status'], d['meta']['commitHash'][:7], d['meta']['commitMessage'].splitlines()[0])"
   ```

   Statuses: `BUILDING` / `DEPLOYING` → keep polling; `SUCCESS` → done; `FAILED` / `CRASHED` → investigate via `railway logs --deployment <id>`. A previous deployment showing `REMOVED` is normal — that just means it was superseded by a newer build (e.g., back-to-back PR merges).

Treat `main` as protected. Never push directly to it, never merge without confirmation, never force-push to it.

## Conda environment

**Always activate before running anything in this project:**

```bash
conda activate cotc-search
```

The env is defined in `environment.yml` (Python 3.11 + streamlit,
google-api-python-client, google-auth, tenacity, pillow). Recreate with:

```bash
conda env create -f environment.yml      # first time
conda env update -f environment.yml --prune   # after editing the yml
```

## Layout

```
character_sheet/
├── environment.yml              # conda env definition
├── requirements.txt             # pip-only deps for Railway / Docker
├── Dockerfile                   # python:3.11-slim image, runs the bot
├── railway.json                 # Railway build/deploy config
├── config.py                    # sheet ID, tab map, color→rarity, paths,
│                                # env-var-aware get_setting helper
├── app.py                       # Streamlit UI (entry point)
├── bot/                         # Discord bot (`python -m bot`)
│   ├── __main__.py              # entrypoint; cold-start sync if DB empty
│   ├── client.py                # discord.Client + CommandTree wiring
│   ├── commands.py              # /character, /search, /affinities, /refresh
│   ├── embeds.py                # pure embed builders (testable, no runtime)
│   └── db.py                    # per-call connection (mirrors app.py)
├── sync/
│   ├── fetch.py                 # one Sheets API v4 call, all tabs
│   ├── parsers.py               # Index parser + role-tab parser + SEA/GL
│   ├── runner.py                # orchestrates fetch + parse + transactional persist
│   └── cli.py                   # `python -m sync.cli --api-key ...`
├── db/
│   ├── schema.sql               # SQLite schema (FTS5 virtual table included)
│   └── repo.py                  # connection, bootstrap, upserts, search queries
├── data/                        # created at runtime
│   └── cotc.sqlite              # the local mirror
└── verify/                      # verification scripts (see below)
```

User-level state (so it stays out of the project directory):

- API key:        `~/.cotc-search/config.toml`  (`api_key = "..."`)
- Discord token:  same file, `discord_token = "..."` (or env `DISCORD_BOT_TOKEN`)
- Admin user IDs: same file, `bot_admin_user_ids = "12345,67890"` (or env
  `BOT_ADMIN_USER_IDS`) — only listed Discord user IDs can run `/refresh`

## Running

```bash
conda activate cotc-search

# one-time: get a Google Sheets API key from console.cloud.google.com
# (enable Sheets API on a project, create an API key, restrict to Sheets API)

# Sync from CLI:
python -m sync.cli --api-key $GOOGLE_API_KEY
# or, with the key already saved by the UI:
python -m sync.cli --api-key "$(grep api_key ~/.cotc-search/config.toml | cut -d'"' -f2)"

# Run the UI (browser opens at http://localhost:8501):
streamlit run app.py
```

The Refresh button in the UI sidebar performs the same sync as the CLI.

## Discord bot

Same SQLite mirror, surfaced through Discord slash commands. Code lives
in `bot/`; entry point is `python -m bot`.

**Commands:**
- `/character name:<autocomplete>` — full embed (kit, affinities, A4
  accessories, profile, sync footer).
- `/search role weapon rarity weakness text` — top-10 list, all params
  optional, all autocompletes pull from the live DB.
- `/affinities name:<autocomplete>` — quick weakness check.
- `/refresh` — admin-gated; re-runs `sync.runner.run_sync` off the event
  loop. Refuses if a refresh is already in flight.

**One-time Discord setup:**
1. Create an app at https://discord.com/developers/applications.
2. Bot tab → reset token. Either set `DISCORD_BOT_TOKEN` env var, or add
   `discord_token = "..."` to `~/.cotc-search/config.toml`.
3. OAuth2 → URL Generator → scopes `bot` + `applications.commands`,
   permissions `Send Messages` + `Embed Links` → invite to your guild.
4. For instant command propagation while iterating, set
   `DISCORD_TEST_GUILD_ID=<your guild id>` (commands are mirrored to that
   guild on startup; global sync also runs and propagates within ~1 h).
5. To allow `/refresh`, set `BOT_ADMIN_USER_IDS=12345,67890` (comma list of
   Discord user IDs).

**Local run:**
```bash
conda activate cotc-search
python -m bot
```

**Railway deploy (the host this repo targets):**
- Connect the repo; Railway auto-detects `Dockerfile` (or reads
  `railway.json`).
- Add a Volume mounted at `/data`.
- Env vars: `DISCORD_BOT_TOKEN`, `GOOGLE_API_KEY`, `BOT_ADMIN_USER_IDS`,
  `COTC_DB_PATH=/data/cotc.sqlite`. Optional:
  `DISCORD_TEST_GUILD_ID`.
- First boot runs a cold-start sync if `character_forms` is empty;
  thereafter only `/refresh` mutates the DB.
- Logs: stdout via `logging` (already configured in `bot/__main__.py`).

The bot is **read-only** to SQLite for everything except `/refresh`. It does
not keep per-guild state, so install count doesn't bloat storage.

## Schema notes (read before adding queries)

A canonical character has multiple **forms** across rarity / EX variants /
server (global vs SEA). All per-form data hangs off `character_forms.id`,
not `characters.id`. So:

- **Search/filter by rarity, level cap, hyperlink, color** → `character_forms`.
- **Search by canonical name, role, weapon** → `characters` (joined to forms).
- **Skills, equipment, profile, affinities** → keyed by `form_id`.
- **Free-text** (skill descriptions, equipment names) → use the
  `characters_fts` virtual table (FTS5). `repo.search_forms(text=...)` already
  does this; don't roll your own LIKE.

Color → rarity mapping lives in `config.color_family` and
`config.rarity_from_color`. The sheet's legend (row 5) is:
red=5★, green=free 3→5★, yellow=4★, blue=3★.

## Sync flow

1. `fetch_spreadsheet(api_key)` — single `spreadsheets.get?includeGridData=true`
   call, ~5–20 MB. The field mask is in `sync/fetch.py::_FIELDS`. Wrapped in
   `tenacity` retry.
2. Raw response is gzipped and stored in `raw_snapshots(sync_run_id, payload_json)`
   so parsers can be re-run offline if the schema evolves — no need to refetch.
3. Index parser produces the canonical roster (name, role, weapon, rarity,
   hyperlink anchor → role-tab cell).
4. Role-tab parser uses Index hyperlink anchors to slice each role tab into
   per-character blocks; extracts skills, equipment, profile.
5. SEA/GL parser flags characters with SEA-only kit variants (creates a second
   form row with `server='sea'`).
6. Persist inside `BEGIN IMMEDIATE`: `clear_data_tables` then re-insert.
   `rebuild_fts` repopulates the FTS index. `sync_runs` row is finalized.

## Tests

Two layers run before claiming any change is safe:

```bash
conda activate cotc-search

# 1. Unit + integration tests (no network) — fast, hermetic.
pytest tests/

# 2. Live verifier (reads the latest raw_snapshots payload back).
python -m verify.check
```

`tests/` covers: schema bootstrap and FTS5 readiness, repo CRUD and search
filters, config inventory (19 tabs, 16 role tabs), parser internals
(color→rarity, anchor parsing, role-tab block detection on synthetic
payloads, alias table consistency, Levenshtein distances), runner block
selection (alias / fuzzy / band-aware), and a Streamlit `AppTest` smoke
test. There is also an explicit regression test for the SQLite cross-thread
bug — `tests/test_repo_threadsafety.py` documents the failure mode and
asserts the per-thread-connection pattern app.py uses.

A lint-style guard in `tests/test_app_smoke.py` fails on any reintroduction
of the deprecated `use_container_width=` kwarg.

## Important: do not assume parser correctness

The sheet is community-maintained and the role-tab layout has irregularities
(merged cells, varying block sizes, EX variants slotted between rarities,
JP↔EN transliteration drift in names). **Always verify against the live
sheet** when changing parsers, by running:

```bash
conda activate cotc-search && python -m verify.check
```

`verify/check.py` reads the latest `raw_snapshots` payload and runs ~57
assertions: 19-tab inventory, Index roster present in DB, every role-tab
character block mapped to a DB form with skills (with alias + fuzzy
fallback), plausible rarity distribution, FTS searchable, and 8 spot-checks
of named characters. Exits non-zero on any failure.

Run sync, then `verify.check`, and never claim "import worked" without it
passing.

## Name reconciliation: aliases > fuzzy

When a role-tab block name disagrees with the Index spelling (community-
sheet typos or JP↔EN drift), the runner reconciles in this order:

1. **Exact match** on the Index canonical name (most cases).
2. **`config.NAME_ALIASES`** — explicit alias map. Add to it whenever
   verify reports an unmatched live block. Examples already in place:
   `"Fior" → "Fiore"`, `"Krauser" → "Clauser"`, `"Araune" → "Alaune"`,
   `"Elrica" → "Erika"`. Aliases are preferred over fuzzy because they're
   self-documenting and don't depend on edit distance.
3. **Levenshtein distance ≤ 2** within the same role+rarity-band tab —
   safety net for typos. Won't catch large transliteration differences
   (use the alias map for those).

The verify script reports which blocks were resolved by which mechanism so
you can promote frequent fuzzy hits into explicit aliases.

## Common pitfalls when modifying

- The Sheets API returns colors as `{red, green, blue}` floats 0..1, not hex.
  Always go through `parsers._color_dict_to_hex` and then
  `config.color_family` for bucketing — exact-match comparisons will fail
  because the sheet uses small color drift across cells.
- Index hyperlinks point at specific cells in role tabs (e.g.,
  `#gid=519845584&range=B5`). Don't try to match characters between Index
  and role tabs by name fuzzy-matching — use `parsers.parse_anchor`.
- The sheet contains tabs whose names contain `⭐` and `✯` — keep those
  unicode characters out of file paths and SQL string literals; reference
  tabs by gid.
- SQLite FTS5 needs an external rebuild after data churn. `repo.rebuild_fts`
  does this; call it after any bulk insert.

## Gotchas

- Tab names use `⭐5` not `5⭐`; gid is the only stable identifier.
- "EX Cyrus" / "EX2 Erika" are *forms* of the same canonical character (case-
  sensitive; we keep the display name intact). Variant kind is parsed from
  the display-name prefix in `runner._variant_kind_for`.
