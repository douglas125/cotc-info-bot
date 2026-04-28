# Information sources

Canonical references for everything this project depends on.

## Primary source — community Google Sheet

- **URL**: <https://docs.google.com/spreadsheets/d/1LF2NbjnMsq8Jo2TSpocu6NN-o9dsUlmd8xCMZpKUHNw/>
- **Spreadsheet ID**: `1LF2NbjnMsq8Jo2TSpocu6NN-o9dsUlmd8xCMZpKUHNw`
- **Access**: public, read-only via the Google Sheets v4 API.
- **Maintained by**: the *Octopath Traveler: Champions of the Continent* community.
- **Why we mirror it**: the sheet encodes critical information through cell
  color (rarity), inline icons (weakness / weapon / element), and hyperlinks
  to anchor cells. That is fast to *read* but painful to filter or
  cross-reference inside Sheets.

### Tab inventory (19 tabs)

- 1 × master `Characters Index` (canonical roster)
- 1 × `Release History`
- 1 × `SEA/GL Unique Kits`
- 8 × ⭐5 role tabs (one per role)
- 8 × 3✯ & 4✯ role tabs (one per role)

The full `gid → tab-name` map lives in `config.py::TABS`. Tabs are referenced
by `gid` everywhere because tab names contain ⭐ and ✯ unicode characters that
don't survive cleanly in shell pipelines or SQL string literals.

### What the API exposes vs. doesn't

The `spreadsheets.get?includeGridData=true` endpoint returns:

- ✅ Cell text (`formattedValue`)
- ✅ Hyperlinks (`hyperlink`)
- ✅ Foreground / background colors (`effectiveFormat.*`)
- ✅ Formula source (`userEnteredValue.formulaValue`) — including `=IMAGE("url")`
- ✅ Rich-text runs (`textFormatRuns`) — for color-keyed substrings

It does **not** expose:

- ❌ Inserted images (the kind added via *Insert > Image > In cell* or as
  a floating drawing). These remain visible in Sheets but are absent from
  every API response shape. The community sheet uses inserted images for
  character pixel art — which is why `splash_art_url` stays empty after
  every sync. Capturing the art needs a separate source.

References:
- [Sheet resource](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets#resource:-sheet)
- [CellData reference](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/cells)

## Hyperlinks back to the source

Every form's `hyperlink_url` (column on `character_forms`) points to a
specific cell on the role tab — `…#gid=<gid>&range=B5` — so the embed
title in `/character` is clickable and lands the user directly on that
character's block in the spreadsheet, where the inserted-image artwork is
visible. This is the supported way to "see the art" until/unless we add
a separate art source.

## Secondary source — Adversary Log CotC (enemy spreadsheet)

- **URL**: <https://docs.google.com/spreadsheets/d/1Of4zz3rlV973Rt2kzHqoSWjiJmfhb77iMnAYofCT3Gs/>
- **Spreadsheet ID**: `1Of4zz3rlV973Rt2kzHqoSWjiJmfhb77iMnAYofCT3Gs`
- **Access**: public, read-only via the same Google Sheets v4 API key.
- **Maintained by**: `:/Silence` (and contributors) in the CotC community.
- **Why we mirror it**: each enemy encounter has stats for 6 difficulty
  ranks (Rank 1/2/3, EX1/2/3) plus per-position break-shield counts,
  weakness icons, and fight notes. Filtering across ranks and pulling one
  rank's stats inline in Discord is what `/enemy` exists for.

### Tab inventory (15 tabs)

- 1 × `Guide` (instructional, parser skips)
- 4 × Osterra difficulty tabs: `Lvl 1`, `Lvl 25`, `Lvl 50`, `Lvl 75`
- 4 × Solistia difficulty tabs: `Solistia Lvl 1`/`25`/`50`/`75`
- 1 × `120 NPCs` (single-rank stat blocks)
- 3 × `*Data` lookup tabs: `Osterra Data`, `Solistia Data`, `120 NPCs Data`
  — these are the canonical per-rank source of truth, **not** skipped
  even though they're never displayed to users
- 1 × `Template` (layout scaffold, parser skips)
- 1 × `Images` (image asset storage, parser skips)

The `gid → EnemyTabSpec` map lives in `config.py::ENEMIES_TABS`. The
`*Data` tab gids are in `config.py::ENEMY_DATA_TAB_GIDS`.

### Layout: display tabs vs. data tabs

The visible Lvl-N display tabs hold **only the rank the maintainer last
selected** (every block currently shows EX3). The rank cell is a Google
Sheets dropdown (`dataValidation`), and the API returns whatever value
is currently set — not the dropdown options. So:

- **Display tabs** = source for: enemy canonical name (full lore name like
  "Sly Leader Lloris"), category (which Lvl-N tab), region, and the
  hyperlink anchor that points users back to the visual block.
- **Data tabs** = source for: all 6 ranks of stats per encounter member,
  indexed by an internal short name (e.g. "Lloris" rather than the
  full display name). Block layout: a header cell + 'Shields' + 9 stat
  headers in one row, then 6 rank rows × N members stacked vertically.

Display→data name reconciliation runs in `sync.enemy_parsers`: exact
match → alias (`config.ENEMY_NAME_ALIASES`) → article-stripped substring
match. Frequent unmatched display blocks should be promoted into the
alias map.

### What the API exposes vs. doesn't (enemy sheet)

Same v4 endpoint, same field mask. Two notable enemy-specific gaps:

- ❌ Weakness icons in the visible blocks are inserted images (Insert >
  Image > In cell), **not** `=IMAGE("url")` formulas. The Sheets API does
  not return them. The `/enemy` embed surfaces the per-position break-
  shield count and a "see sheet" link instead of decoded icons.
- ❌ Fight notes (the "/Silence" timestamped strategy text in the
  screenshot) are Google Sheets **comments** — a separate threaded
  discussion API (`spreadsheets.comments.list`), not returned by
  `spreadsheets.get`. We intentionally don't fetch them in v1.

## Git remote

- Repo: <https://github.com/douglas125/cotc-character-search>
- Branch: `main` (protected; PR-only).
