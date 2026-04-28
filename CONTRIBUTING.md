# Contributing

Thanks for your interest. This is a small project, so the process is
deliberately light.

## Reporting things

- **Wrong data in `/character` or `/enemy` output** (bad stats, missing
  skill, mis-rendered weakness): use the in-bot `/feedback` command. It
  logs straight into the admin queue. Note that data corrections
  themselves should be proposed upstream to the community spreadsheet
  maintainers — see [`INFO_SOURCES.md`](INFO_SOURCES.md).
- **Bot bugs, crashes, suggestions**: open an issue. For non-trivial
  changes, please open an issue *before* writing code so we can agree on
  scope.

## Development setup

```bash
conda env create -f environment.yml
conda activate cotc-search
```

The conda env pins Python 3.11. CI also runs the test suite on 3.12 and
3.13, so prefer code that's compatible with all three.

Settings (Sheets API key, Discord token, admin user IDs) go in
`~/.cotc-search/config.toml` or environment variables — see the
[Configuration section of the README](README.md#configuration).

## Pull request flow

1. **Branch off `main`.** Never push directly to `main`.
2. Make your change.
3. Run `pytest tests/`. All tests must pass.
4. **If you touched anything in `sync/parsers.py`, `sync/runner.py`, or
   the schema:** also run `python -m verify.check`. It re-reads the
   latest `raw_snapshots` payload and asserts roster coverage, parser
   block matching, FTS searchability, and named-character spot checks.
   The community sheet has irregularities (merged cells, JP↔EN drift,
   EX-variant placement) and unit tests can't see them.
5. Push your branch and open a PR. Include a short test plan and call
   out any parser changes explicitly.

## Code style

- No comments unless they explain a non-obvious *why*. Names should carry
  the *what*.
- No emojis in source files.
- Prefer editing existing modules over creating new ones.

## Scope

This bot reads from the community sheets — it does not edit them. Please
don't propose features that write back to the spreadsheets.
