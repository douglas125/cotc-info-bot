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

## Git remote

- Repo: <https://github.com/douglas125/cotc-character-search>
- Branch: `main` (protected; PR-only).
