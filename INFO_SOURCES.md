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

## Reviewed character form aliases

The Index and role tabs currently call both Rinyuu job variants `EX Rinyuu`.
Per the user's correction, `config.ROLE_NAME_ALIASES` maps the **dancer** entry
to `EX2 Rinyuu`; the apothecary and its Global Unique Kit remain `EX Rinyuu`.
This applies to both EX prefix and suffix spellings, only when the role is
known to be dancer. It is deliberately not a global name alias.

The wiki likewise labels both variants `Rinyuu EX`. Reviewed sprite overrides
use `Rinyuu_EX_Apothecary_Sprite.png` for EX and
`Rinyuu_EX_Dancer_Sprite.png` for EX2, verified through the wiki file API.

## Hyperlinks back to the source

Every form's `hyperlink_url` (column on `character_forms`) points to a
specific cell on the role tab — `…#gid=<gid>&range=B5` — so the embed
title in `/character` is clickable and lands the user directly on that
character's block in the spreadsheet, where the inserted-image artwork is
visible. This is the supported way to "see the art" until/unless we add
a separate art source.

## Secondary source — (New) Adversary Log CotC

- **URL**: <https://docs.google.com/spreadsheets/d/1zcc5VqORiplxZ0tnff8wxuvPDJ0AUneljT16RwMEOa8/>
- **Spreadsheet ID**: `1zcc5VqORiplxZ0tnff8wxuvPDJ0AUneljT16RwMEOa8`
- **Access**: public; anonymous viewing and the existing Sheets v4 API key
  were verified on 2026-09-05, including the hidden data tabs.
- **Maintained by**: `:/Silence` and community contributors; the Guide lists
  `@silence_ark` for source corrections.
- **Replaces**: `1Of4zz3rlV973Rt2kzHqoSWjiJmfhb77iMnAYofCT3Gs`.
- **Coverage**: base ranks 1–3, available numbered EX ranks (including EX4
  and EX5), and 120/140 NPC encounters. EX coverage grows as fights release.

### Tab inventory (17 tabs at migration)

- `Guide`, `Template`, `Images`: non-data tabs, skipped.
- Eight ranked display tabs: `Lvl 1/25/50/75` and `Solistia Lvl 1/25/50/75`.
- Two NPC display tabs: `120 NPCs` and `140 NPCs`.
- Four hidden data tabs: `Orsterra Data` (source spelling), `Solistia Data`,
  `120 NPCs Data`, and `140 NPCs Data`.

Gids are authoritative in `config.ENEMIES_TABS` and `ENEMY_DATA_TAB_GIDS`.
`Lvl 75` changed to gid `2066203872`; `140 NPCs` is `1358661359` and its
catalog is `493800596`. `ENEMY_NPC_DATA_KEYS` links each NPC display tab to
its own catalog; NPC encounters remain single-rank `Default` forms.

### Import behavior

Display tabs provide encounter names, category, source anchors, and weakness
icons. Their rank dropdown shows only the maintainer's current selection;
its options do not prove that usable stats exist for a rank. Data tabs
provide all populated ranks for each member. A refresh discovers new fights
and EX ranks within these configured tabs without a per-fight code change.

The shared `enemy_ranks` helpers normalize and numerically sort `Rank1`–`Rank3`
and `EX<n>` for positive integers. Only ranks with positive numeric HP for
every identified member are published. Blank future ranks are valid; partial
ranks and invalid optional stats produce sync warnings. `/enemy` defaults
to the highest available rank. No scheduled refresh is configured.

Display-to-data references take precedence over name reconciliation. Ranked
VLOOKUP ranges identify member blocks; direct NPC references identify catalog
rows. Exact names, explicit `ENEMY_NAME_ALIASES`, and article-stripped matching
remain fallbacks for blocks without references. The importer resolves simple
IF/IFS wave selectors using the current source selection; it does not add a
wave selector to Discord or combine stats from different displayed waves.

When an encounter combines data references and manually entered members,
only its usable displayed rank is imported, with a warning. At migration,
Aelfric and its manually entered pillar use this fallback. Inline shields
are included, and stats for other ranks are never inferred from the display.

Weakness icons are named-range formulas (`=Sword`, `=Wind`, etc.), including
conditional wave formulas; they are readable through the API. `Polearm`
normalizes to `Spear`. The selected display wave's weaknesses are associated
with that encounter's imported ranks, matching the existing data model.
Threaded source comments are not fetched by this importer; Arena Fight notes
continue to come from the separately maintained Game8 seed below.

### Verification and deployment

Run `pytest tests/`, then sync and run both `python -m verify.check` and
`python -m verify.check_enemies`. Use `COTC_DB_PATH` to isolate migration
validation from the local mirror. The enemy verifier compares ranks, member
stats, weaknesses, and anchors against the latest snapshot instead of requiring
exactly six ranks or a fixed count of EX4 encounters. Historical screenshot
values remain in offline regression tests.

Missing required tabs or an empty enemy import abort the transaction. Refresh
preserves feedback, usage counters, fight notes, and sync history. After the
approved deployment, run one `/refresh` (or the existing sync CLI on the deployed
volume); an ordinary restart does not refresh an already populated database.

## Supplemental source — Game8 Arena fight guides

- **Arena index URL**: <https://game8.jp/octopathtraveler-sp/383516>
- **Purpose**: source for the pre-parsed `/enemy` "Fight notes" dropdown
  section for Arena champions. These notes are short English paraphrases
  with source links, not copied article bodies.
- **Storage policy**: seeded into the refresh-safe `arena_fight_notes`
  table from `db/seed/arena_fight_notes.json` during DB bootstrap. This
  table is app-owned reference data and must **not** be added to any
  `/refresh` wipe loop.
- **Join policy**: notes join to `enemies` by normalized aliases rather
  than `enemy_id`, because enemy rows are rebuilt on each sheet sync.
  User-facing aliases such as `Kagemune` and `Rayme` should be included
  even when the current sheet spelling differs or the enemy row is not
  present yet.

Seeded guide pages:

- Tikilen: <https://game8.jp/octopathtraveler-sp/371120>
- Glossom: <https://game8.jp/octopathtraveler-sp/373532>
- Varkyn: <https://game8.jp/octopathtraveler-sp/378306>
- Ri'tu: <https://game8.jp/octopathtraveler-sp/386171>
- Gertrude: <https://game8.jp/octopathtraveler-sp/391040>
- Yunnie: <https://game8.jp/octopathtraveler-sp/396589>
- Yan Long: <https://game8.jp/octopathtraveler-sp/409368>
- Largo: <https://game8.jp/octopathtraveler-sp/420192>
- Hammy: <https://game8.jp/octopathtraveler-sp/493451>
- Mirgardi: <https://game8.jp/octopathtraveler-sp/583644>
- Kagemune: <https://game8.jp/octopathtraveler-sp/659640>
- Aoi: <https://game8.jp/octopathtraveler-sp/699639>
- Rayme: <https://game8.jp/octopathtraveler-sp/750802>

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

## Accessory source — community accessory tier list

- **URL**: <https://docs.google.com/spreadsheets/d/1UNvKQ-lHNPCcSrFl51SceM4cV2MoXihhm8iQ-mj7K20/edit?gid=245647410>
- **Spreadsheet ID**: `1UNvKQ-lHNPCcSrFl51SceM4cV2MoXihhm8iQ-mj7K20`.
- **Tier List gid**: `245647410`; Criteria gid `0` is explanatory, not an import input.
- **Access**: public through the existing Google Sheets v4 API key.
- **Verified baseline**: 669 rows, 551 verified descriptions, 102 stat-only
  items and 16 unverified descriptions on 2026-09-25. Counts may grow.

The importer resolves columns by header and uses `accessory_id` as stable
identity. Keep IDs unchanged when sorting, renaming or editing rows. Source
row links are rebuilt at refresh. The original `Name`, `Rank`, `Explanation`,
`Gacha 5* A4?` and exchange columns retain their meanings. `exact_effect_text`
is verbatim game text; `Explanation` is tier commentary and is never indexed
as an effect. `stats_text`, `in_game_name`, optional `character`,
`equip_restriction`, `verification_status`, `verified_on`, `tier_status` and
`audit_notes` supply display metadata. `equip_restriction` means an actual
equip restriction, not a conditional bonus or the character whose A4 it is.

Optional `is_a4` accepts `Yes`, `No`, or `Unverified`, covering free and low-star
A4s as well as gacha 5-star A4s. Without it, only a gacha A4 `Yes` establishes
general A4 status; `No` does not prove the item is not an A4. Owner is optional.
Proposed source corrections are reviewed and applied online separately.

Required headers: `Name`, `Rank`, `Explanation`, `accessory_id`,
`exact_effect_text`, `verification_status`. Empty imports, duplicate/missing
IDs, missing names, unknown verification states and contradictory text/status
or A4 flags abort the sync before replacement. `verified_text` requires text;
`verified_no_effect` and `not_verified` require blank effect text. Unknown
ranks become Unrated with a warning. Other optional values remain unknown.

Accessories participate in the full refresh transaction and retain a gzipped
`raw_snapshots` entry with kind `accessories`. Only the three sheet-derived
tables (`accessories`, `accessories_fts`, `accessory_effects`) are replaced.
The bot uses SQLite for name autocomplete, FTS5 text search and deterministic
positive-effect categories. Phrase aliases map damage cap/limit wording to
one search concept; ordinary passive percentage limits are excluded.

Validate with `python -m verify.check_accessories` after an isolated live sync.
Deploying on an existing volume requires one `/refresh` to populate this source.

## Git remote

- Repo: <https://github.com/douglas125/cotc-info-bot>
- Branch: `main` (protected; PR-only).
