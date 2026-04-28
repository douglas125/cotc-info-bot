# CotC Character Search

A Discord bot over a community-maintained Google Sheet for *Octopath
Traveler: Champions of the Continent*. Mirrors the sheet into SQLite so
slash commands can filter by role, rarity, weakness, and search across
skill text and equipment without scrolling 200+ columns.

## Setup

1. **Create the conda env (one-time):**

   ```bash
   conda env create -f environment.yml
   conda activate cotc-search
   ```

2. **Get a Google Sheets API key (one-time, free):**
   - Go to <https://console.cloud.google.com/>.
   - Create or select a project.
   - APIs & Services → Library → enable **Google Sheets API**.
   - APIs & Services → Credentials → **Create credentials → API key**.
   - Recommended: restrict the key to "Sheets API" only.

3. **First sync:**

   ```bash
   conda activate cotc-search
   python -m sync.cli --api-key YOUR_KEY
   ```

   The first run creates `data/cotc.sqlite` and populates it from the live
   sheet. ~5–20 MB single API request. Subsequent re-runs do a transactional
   replace — no incremental diffing.

4. **Run the Discord bot:**

   ```bash
   conda activate cotc-search
   python -m bot
   ```

   See `CLAUDE.md` for the one-time Discord app setup (token, OAuth invite,
   admin user IDs for `/refresh`).

## What gets imported

| Tab kind         | Count | Role in the data model                          |
|------------------|-------|--------------------------------------------------|
| Characters Index | 1     | Canonical roster: name, role, weapon, rarity, link to role-tab cell |
| Role tabs        | 16    | Per-character skills, equipment, profile         |
| SEA/GL Unique    | 1     | Flags characters with SEA-only kit variants      |
| Release History  | 1     | Captured raw; not yet exposed in the bot         |

Rarity is decoded from the cell's text color in the Index (red=5★,
green=free 3→5★, yellow=4★, blue=3★). EX/EX2 variants are stored as
separate forms of the same canonical character.

## Files

```
config.py            sheet ID, gid → tab map, color→rarity, paths
bot/                 Discord bot (entry point: python -m bot)
sync/                fetch.py · parsers.py · runner.py · cli.py
db/                  schema.sql · repo.py
data/cotc.sqlite     created at runtime
~/.cotc-search/      saved API key (not in project tree)
```

See `CLAUDE.md` for deeper architecture notes.

## Tests

```bash
conda activate cotc-search
pytest tests/                     # hermetic unit + integration tests
python -m verify.check            # live-data assertions vs the latest sync
```

The first runs offline against synthetic payloads; the second reads the
raw API snapshot from the most recent sync and diffs it against the
SQLite mirror.

## Updating

When the source sheet changes (new characters, new tabs):

```bash
conda activate cotc-search
python -m sync.cli --api-key $KEY    # or use /refresh in Discord
```

Re-run is a full replace inside one SQL transaction; if it fails partway,
the previous data stays intact.
