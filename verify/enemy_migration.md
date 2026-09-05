# Updated enemy source: migration verification

Observed 2026-09-05. Counts describe the inspected snapshot, not permanent
acceptance thresholds; future EX additions are discovered on refresh.

## Source and coverage

The [replacement sheet](https://docs.google.com/spreadsheets/d/1zcc5VqORiplxZ0tnff8wxuvPDJ0AUneljT16RwMEOa8/edit)
opens anonymously and is readable with the bot's existing API key, including
hidden catalogs. Compared with the old source and its parser on `main`:

| Coverage | Old | Replacement |
| --- | ---: | ---: |
| Encounters | 95 | 128 |
| Rank entries | 515 | 773 |
| EX4 encounters | 0 | 62 |
| EX5 encounters | 0 | 3 |
| NPC encounters | 8 | 10 |

All current display encounters resolve. These nine old names were replaced
at the same source widgets; none represents an unexplained encounter loss:

| Previous display name | Replacement display name |
| --- | --- |
| Blood Pact Bishop Jafford | Order of Blood Bishop Jafford |
| New Delsta | Noble Tutor |
| Toto'haha | Well-trained Beastling |
| Canalbrine | Devout Cleric |
| Cape Cold | Timid Ex-Mercenary |
| Ryu | Thrill-seeking Man |
| Cropdale | Monster-enticing Woman |
| Flamechurch | Senior Knight |
| Unfinished Tunnel | Ouma Researcher |

The new source's NPC names describe the encounters instead of their locations.
All eight old NPC widgets remain represented; 140 NPCs adds Twin Knights and
Eastern Trio. Lykaon and Lutiya remain available even while their display
dropdowns are set to EX5.

Manual source spot-checks matched the imported values:

- Captain Tristan EX4: Captain HP 38,379,559 / shields 39; Minion
  HP 10,076,266 / shields 14.
- Divine Beast Lykaon EX5: HP 99,999,999 / shields 69.
- Divine Beast Lutiya EX5: Lutiya HP 73,260,000 / shields 39;
  Mellow HP 43,251,965 / shields 61.
- Twin Knights: Erhardt HP 66,000,000 / shields 38; Olberic
  HP 88,000,000 / shields 50.
- Eastern Trio: Hasumi HP 66,000,000 / shields 45; Kouren
  HP 94,500,000 / shields 51; Tsugemaru HP 74,580,000 / shields 40.

Aelfric combines a data-backed member with a manually entered pillar. It
imports only the currently displayed EX3 rank and emits a warning. Inferring
the pillar's stats at other ranks would be unsupported. Conditional wave
formulas elsewhere use the maintainer's current wave selection, including
member-specific weakness formulas for the larger NPC encounters.

## Character correction required for the live refresh

The initial all-sources-live refresh exposed a character Index conflict:
EX Rinyuu appears in row 25 as an apothecary and in row 43 as a dancer.
Both source role blocks also use that name. The existing unique form key
rejected the second insert; the unchanged runner from `main` reproduced it.

The user confirmed that the dancer must be aliased to **EX2 Rinyuu**. The
migration therefore includes a job-scoped alias on the Index and role-tab
parsers. The apothecary's EX identity and Global Unique Kit precedence stay
intact. A regression test imports both entries together and checks their
separate roles, variant kinds, and skill descriptions.

The wiki has two different sprite files under the same `Rinyuu EX` link text.
The reviewed EX and EX2 overrides select the Apothecary and Dancer files
respectively; both filenames were observed in the live wiki response and
are validated by its file API during sprite refresh. No duplicate entry or
sprite error is suppressed to make validation pass.

The full live character verifier also found EX Auguste missing from the wiki
index. Its `Auguste_EX_Sprite.png` file was verified through the wiki API and
added as a curated override; the verifier continues to require full coverage.

## Validation and rollout

- Hermetic tests cover future ranks, new EX4 data on a subsequent refresh,
  incomplete HP, renamed formula-linked encounters, NPC catalogs, conditional
  wave references, inline shields, Discord selection/pagination, and rollback
  with community state preserved. Historical screenshot values are fixed
  offline fixtures. Rinyuu tests cover the separate kits and sprites.
- Final validation uses a fresh temporary database and all three live Sheets
  sources through the actual sync runner, including live sprite refresh.
  `python -m verify.check` passes all 101 checks;
  `python -m verify.check_enemies` passes all six checks. Sprite coverage is
  288/288 characters; the enemy mirror contains 128 encounters / 773 forms.
- All 13 seeded Arena Fight notes resolve to the replacement source's current
  encounter names; no seed changes are needed.

After explicit merge approval and green CI, confirm Railway deploys
successfully, then run the existing refresh path and verify the enemy mirror.
No production database, source spreadsheet, or deployment configuration was
changed during this validation.
