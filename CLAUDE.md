# CLAUDE.md — CotC Character Sheet (local search tool)

## What this project is

A Discord bot that mirrors a community-maintained Google Sheet for
*Octopath Traveler: Champions of the Continent* into a SQLite database, so
users can filter and search characters by role, rarity, weakness/element,
weapon, and free-text against skills/equipment via slash commands.

- **Canonical data source**: see [`INFO_SOURCES.md`](INFO_SOURCES.md) for
  the spreadsheet URL/ID, tab inventory, and a list of what the Sheets v4
  API exposes (and notably what it does *not* — inserted-image artwork).
- **Why a local copy**: the live sheet uses cell color (rarity), inline icons
  (weakness/weapon/element), and hyperlinks to encode information that is fast
  to read but painful to filter or cross-reference inside Sheets.

## Git workflow (mandatory)

Repo: `douglas125/cotc-info-bot` (default branch `main`).

For every change, follow this flow:

1. **Branch from main** — `git checkout main && git pull && git checkout -b <branch>`. Never work directly on `main`.
2. **Do the work** — edit / add / test.
3. **Commit and push** — `git commit` then `git push -u origin <branch>`.
4. **Open a PR** — `gh pr create` with a clear summary and test plan.
5. **WAIT for the user's confirmation.** Do not merge on your own initiative even if CI is green and tests pass. Pause and report the PR URL.
6. **Merge only after BOTH explicit confirmation AND green CI** — once the user authorizes the merge, poll `gh pr checks <PR>` until every check passes, then run `gh pr merge --squash --delete-branch` (or as the user specifies). If CI fails, fix the issue and push a new commit to the same branch — never merge a red PR. The `--delete-branch` flag removes the *remote* branch.
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

The env is defined in `environment.yml` (Python 3.11 + discord.py,
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
├── bot/                         # Discord bot (`python -m bot`)
│   ├── __main__.py              # entrypoint; cold-start sync if DB empty
│   ├── client.py                # discord.Client + CommandTree wiring
│   ├── commands.py              # /character, /enemy, /pet, /search, /refresh, /feedback
│   ├── embeds.py                # /character embed builders (no runtime)
│   ├── enemy_embeds.py          # /enemy embed builders (no runtime)
│   ├── pet_embeds.py            # /pet embed builder (single screen, no view)
│   ├── views.py                 # /character dropdown view (sections)
│   ├── enemy_views.py           # /enemy dropdown view (rank tiers)
│   └── db.py                    # per-call SQLite connection helper
├── sync/
│   ├── fetch.py                 # one Sheets API v4 call, all tabs
│   ├── parsers.py               # Index parser + role-tab parser + SEA/GL
│   ├── enemy_parsers.py         # enemy data + display tab parsers
│   ├── pet_parsers.py           # Pet List parser (Seed Story Content sheet)
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
# or, with the key already saved at ~/.cotc-search/config.toml:
python -m sync.cli --api-key "$(grep api_key ~/.cotc-search/config.toml | cut -d'"' -f2)"

# Run the Discord bot:
python -m bot
```

The `/refresh` slash command (admin-gated) re-runs the same sync as the CLI.

## Discord bot

The SQLite mirror is surfaced through Discord slash commands. Code lives
in `bot/`; entry point is `python -m bot`.

**Commands:**
- `/character name:<autocomplete>` — full embed (kit, affinities, A4
  accessories, profile, sync footer).
- `/enemy name:<autocomplete>` — stats grid + per-position break-shield
  count + weakness labels for one encounter at a chosen rank. Dropdown
  swaps among the enemy's available ranks (Rank1..EX3 for ranked
  enemies; NPCs are single-rank with no dropdown). Source: the second
  enemy sheet (Adversary Log CotC), see `INFO_SOURCES.md`.
- `/pet name:<autocomplete>` — single-screen embed: ability text, Max
  Boost, Turn Preparation (base / Lv10), Turn Cooldown (base / Lv5),
  the 8 fixed stats (HP/SP/Patk/Pdef/Matk/Mdef/Crit/Speed), and the
  "how to obtain" string. Source: the third *Seed Story Content* sheet,
  see `INFO_SOURCES.md`. When a typed prefix matches more than one pet
  with the same English name (e.g. "White Rabbit" exists as both a
  Login reward and a Quest reward), the autocomplete labels show a
  short source-text hint so the user can disambiguate.
- `/search role weapon rarity weakness text` — top-10 list, all params
  optional, all autocompletes pull from the live DB.
- `/refresh` — admin-gated; re-runs `sync.runner.run_sync` off the event
  loop, syncing the character, enemy, AND pet spreadsheets in one
  transaction. Refuses if a refresh is already in flight.
- `/feedback text:<≤2000 chars>` — anyone; logs a correction/inconsistency
  report into `feedback_submissions`. Per-user rate limit (3/60s) is
  enforced by counting recent rows in the same table — survives bot
  restarts. Reply is ephemeral.
- `/feedback_list [limit:1-25]` — admin-gated; ephemeral embed of the
  newest submissions, with a per-day usage breakdown of every tracked
  slash command (`/character`, `/enemy`, `/pet`, `/ask_ai`, …) for the
  last 10 days (UTC) prepended.
- `/feedback_clear confirm:bool` — admin-gated; deletes all rows from
  `feedback_submissions`. Refuses unless `confirm:true`.
- `/ask_ai` — **currently un-hooked from Discord** (the registration
  block is removed from `bot/commands.py`). Implementation preserved in
  `bot/ask_ai/`, schema in `db.ai_queries`, tests in
  `tests/test_bot_ask_ai.py`. See `README.md` "Dormant features" for
  re-enable steps.

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
  `DISCORD_TEST_GUILD_ID`, `ANTHROPIC_API_KEY` (only used if `/ask_ai`
  is re-hooked — see "Dormant features" in `README.md`).
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
6. Persist inside `BEGIN IMMEDIATE`: `clear_character_tables` /
   `clear_enemy_tables` / `clear_pet_tables` then re-insert.
   `rebuild_fts` / `rebuild_enemy_fts` / `rebuild_pet_fts` repopulate the
   FTS indexes. `sync_runs` row is finalized with character, enemy, and
   pet counts.

### What `/refresh` wipes vs preserves

The wipe loops in `repo.clear_character_tables`,
`repo.clear_enemy_tables`, and `repo.clear_pet_tables` are intentional
and the contents matter — adding or removing a table here changes
whether community state survives a re-sync. Treat it as a policy
decision, not a maintenance chore. Each pipeline's wipe is narrow:
adding a sheet-derived table goes in its OWN clear function, never
in another pipeline's.

**Wiped on every refresh** (sheet-derived, regenerated from the snapshot):

- Character side: `characters_fts`, `character_profile`, `unique_effects`,
  `equipment_stats`, `equipment`, `skills`, `character_affinities`,
  `character_forms`, `characters`
- Enemy side: `enemies_fts`, `enemy_weaknesses`, `enemy_member_stats`,
  `enemy_forms`, `enemies`
- Pet side: `pets_fts`, `pets`

**Preserved across refreshes** (must NOT be added to ANY wipe loop):

- `sync_runs` / `raw_snapshots` — sync history & raw payloads, used by the
  verifier and by parser re-runs. `raw_snapshots` carries one row per
  `(sync_run_id, kind)` (`'characters'`, `'enemies'`, `'pets'`) so all
  three pipelines are auditable from one run.
- `feedback_submissions` — community-submitted corrections (`/feedback`).
  Wiping it would silently delete user reports on every re-sync. Cleared
  explicitly via the admin-only `/feedback_clear` slash command.
- `command_usage_daily` — per-day counter of `/character`, `/enemy`, and
  `/pet` invocations. Wiping it would erase usage history that no other
  source can reconstruct. No admin-clear command yet — drop manually if
  needed.
- `character_sprites` — wiki-curated sprite URL per canonical character,
  rendered as the embed thumbnail by `/character`. Seeded by
  `scripts.refresh_sprite_urls.refresh_sprite_urls`, called both by the
  standalone CLI (`python -m scripts.refresh_sprite_urls`) and as a
  non-fatal post-step at the end of every `/refresh` (after the main
  transaction commits — see `sync/runner.py`). Wiping it would silently
  strip thumbnails until the next `/refresh` re-seeds, and a wiki
  outage during `/refresh` only logs `WARN: sprite refresh skipped: …`
  while leaving existing rows intact. The table is keyed by
  `canonical_name`; `repo.get_form` LEFT JOINs it so the embed code is
  a single guarded `set_thumbnail` call.
- `ai_queries` — `/ask_ai` invocation log. Drives the per-user 3/hour
  rate limit and the global 100/day circuit breaker (counted from this
  table — survives bot restarts). Also captures token usage
  (`input_tokens` / `output_tokens` / `cache_read` / `cache_write`) for
  cost accounting. Wiping it would reset every user's quota AND lose
  the audit trail. No admin-clear command — drop manually if needed.

### Reading usage stats

There is no slash command for this; query SQLite directly. All counters
are keyed UTC.

**Locally** (where the `sqlite3` CLI is on $PATH):

```bash
# Grand total invocations across all time and commands
sqlite3 data/cotc.sqlite "SELECT SUM(count) AS total FROM command_usage_daily;"

# Totals per command
sqlite3 data/cotc.sqlite \
  "SELECT command_name, SUM(count) AS total
     FROM command_usage_daily
    GROUP BY command_name;"

# Daily breakdown, newest first
sqlite3 -header -column data/cotc.sqlite \
  "SELECT usage_date, command_name, count
     FROM command_usage_daily
    ORDER BY usage_date DESC, command_name;"
```

**On Railway** (the deployed bot). Two gotchas that aren't obvious — both
hit during initial verification, and the recipe below is what actually
worked:

1. The `python:3.11-slim` image has no `sqlite3` CLI. Use the Python
   `sqlite3` module instead.
2. `railway ssh <args>` joins argv and re-parses through the remote
   `sh -c`, so passing SQL with `COUNT(*)` directly blows up the remote
   shell on the unmatched paren. Piping a script via stdin
   (`cat q.py | railway ssh python3 -`) doesn't help either — the remote
   end allocates a TTY and Python lands in the REPL instead of reading
   stdin.

The pattern that survives both layers: base64-encode the script locally
and pass a single quoted arg to `python3 -c`:

```bash
SCRIPT='import sqlite3
c = sqlite3.connect("/data/cotc.sqlite")
print("total:", c.execute(
    "SELECT COALESCE(SUM(count), 0) FROM command_usage_daily"
).fetchone()[0])
for r in c.execute(
    "SELECT command_name, SUM(count) FROM command_usage_daily "
    "GROUP BY command_name ORDER BY command_name"
):
    print(r)
'
B64=$(printf '%s' "$SCRIPT" | base64 -w0)
railway ssh "python3 -c 'import base64; exec(base64.b64decode(\"$B64\"))'"
```

The outer double quotes are bash; the inner single quotes are the remote
`sh`. Together they keep parens, semicolons, and quotes inside the
script intact across both shells.

When you add a new table that holds user/community state (anything not
derivable from a sheet), default to *not* listing it in either wipe loop,
and add a one-line entry above so the policy stays discoverable.

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
selection (alias / fuzzy / band-aware), and bot-side embed builders /
view wiring / autocomplete / admin gating. There is also an explicit
regression test for the SQLite cross-thread bug —
`tests/test_repo_threadsafety.py` documents the failure mode and asserts
the per-thread-connection pattern `bot/db.py` uses.

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
   `"Fior" → "Fiore"`, `"Krauser" → "Clauser"`, `"Alaune" → "Araune"`,
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
