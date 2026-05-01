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

Same v4 endpoint, same field mask. One enemy-specific gap:

- ✅ Weakness icons look like inserted images visually but are actually
  **named-range formulas** (`=Sword`, `=Wind`, `=Dark`, …) — fully
  API-readable through `userEnteredValue.formulaValue`. The parser strips
  the leading `=` and whitelists the result against
  `sync.enemy_parsers._WEAKNESS_NAMES`. `Polearm` and `Spear` reference
  the same icon — collapse to `Spear` via `_WEAKNESS_ALIASES`.
- ❌ Fight notes (the "/Silence" timestamped strategy text in the
  screenshot) are Google Sheets **comments** — a separate threaded
  discussion API (`spreadsheets.comments.list`), not returned by
  `spreadsheets.get`. We intentionally don't fetch them in v1.

## Tertiary source — Pet sheet (Seed Story Content)

- **URL**: <https://docs.google.com/spreadsheets/d/1pApYNOrKWliMn_25Fs23Lhc8wlSDwutxCYJzyufSwUY/>
- **Spreadsheet ID**: `1pApYNOrKWliMn_25Fs23Lhc8wlSDwutxCYJzyufSwUY`
- **Title**: *Seed Story Content*
- **Access**: public, read-only via the same Google Sheets v4 API key.
- **Why we mirror it**: every pet has 8 fixed stats, an ability block
  (effect text + optional Max Boost + Turn Preparation + Turn Cooldown),
  and a "how to obtain" string. `/pet` surfaces all of that in one
  Discord embed (no rank dropdown — pet info is small enough to fit on
  a single screen). When pets reach Lv10 their first-use turn drops by
  1, and at Lv5 their cooldown drops by 1; the source sheet prints
  both base and reduced values inline, and the parser stores both.

### Tab inventory

- 1 × `Pet List` (gid in `config.py::PETS_LIST_GID = 243040141`) —
  4-rows-per-pet block layout: row r₀ holds name + HP/SP + ability +
  source; rows r₀+1..r₀+3 carry the remaining six stats in
  (Patk/Pdef), (Matk/Mdef), (Crit/Speed) pairs. Other tabs in the
  workbook (`Overview`, `Pet System`, etc.) are not parser inputs.

### Layout notes

- Pet names are formatted `<JP> (<English>)`. The English part of the
  LAST `(...)` group is the canonical name; the raw cell is preserved
  as `display_name_jp` so the original is searchable. Nested example:
  `ルールー (紫) (Purple Lulu )` → `Purple Lulu`. One real-world entry
  uses a tab character between JP and English (`黒茶\t(Black Brown
  Dog)`); whitespace is normalized before regex matching.
- The ability cell packs effect text plus optional `Max Boost: …`,
  `Turn Preparation: N (Lv10: N-1)`, and `Turn Cooldown: M (Lv5: M-1)`
  lines, separated by newlines. Source-side typos (`Lv.10`, `Lv:`,
  missing inner digits) are tolerated by the regexes in
  `sync/pet_parsers.py`. Christmas Dog's "two `Turn Preparation` lines,
  no `Turn Cooldown`" typo auto-heals (the second occurrence becomes
  cooldown) AND the runner emits a warning so upstream gets a nudge
  to fix the source.
- Stats are read by *label* (find the cell whose text equals "Patk",
  take its right neighbor) so a column-position drift in the source
  sheet does not silently corrupt values.
- Duplicate English names exist (`White Rabbit` appears as both a
  Login-event reward and a Quest reward). The DB uses
  `UNIQUE(canonical_name, source_row)` and `/pet`'s autocomplete shows
  a source-text hint to disambiguate when a typed prefix matches more
  than one row.

## Quaternary source — community damage calculator (V1.1 spreadsheet)

- **File**: `buff_debuff/COTC Effective Damage Calculator V1.1.xlsx`
- **Title**: *MeowDB's COTC Effective Damage Calculator V1.1*
- **Authors**: original by Meow; Ult/Pet expansions by Wigglytuff.
- **Source**: posted on the meowdb / Wigglytuff Discord
  (`discord.gg/Ah3xSgtkgd`).
- **Tabs**:
  - `Master` (hidden) — globals: damage type (Physical/Elemental),
    enemy PDEF/EDEF.
  - `Public1` (visible) — main two-column comparison calculator. This
    is the canonical entry point; its formulas in `C15`/`C38`/`C39`
    are the source of truth that `damage/spreadsheet_calc.py` mirrors.
  - `Copy of Public1`, `Copy of Public1 1` — duplicates of Public1.
- **Why we keep a local copy**: it's the canonical formula source for
  `damage/spreadsheet_calc.py`. The parity tests in
  `tests/test_damage.py` load it via stdlib `zipfile` and assert
  cell-by-cell equality. If upstream regenerates the file, drop in the
  new copy and re-run the parity suite — failures flag formula drift.
- **Limitations** (from the cell text): does not model crit damage,
  weapon-grade differences, Divine Beast (G6), per-type sub-buckets,
  sub-bucket auto-caps, defensive bucket math, or any of the four
  final multipliers (Crit, Hell/Heaven/Living World, Soul Potency,
  Skill Potency). Those live in `damage/full_calc.py` and the prose
  spec in `buff_debuff/`.

## Git remote

- Repo: <https://github.com/douglas125/cotc-info-bot>
- Branch: `main` (protected; PR-only).
