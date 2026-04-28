"""One-shot probe of the Adversary Log CotC enemy sheet.

Run before writing the enemy parser so that column offsets, rank-badge
encoding, stat row labels, and per-tab GIDs come from the *live* sheet
rather than guesses. WebFetch can't read Google Sheets cells (JS-rendered);
this script makes the same `spreadsheets.get?includeGridData=true` call the
character pipeline uses, then dumps human-readable artifacts under
`verify/out/` for manual inspection.

Outputs:
  verify/out/enemy_tabs.json       — per-tab metadata (gid, title, dims)
  verify/out/<tab_title>.txt       — first ~80 rows × ~20 cols of each
                                     non-skip tab, with text + bg color +
                                     hyperlink + image-formula presence
  stdout                           — structured guesses at first 3 blocks
                                     per tab (rank-badge cell, stat labels,
                                     position labels)

Usage:
  conda activate cotc-search
  python -m verify.probe_enemies --api-key "$(grep api_key ~/.cotc-search/config.toml | cut -d'"' -f2)"
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from config import ENEMIES_SPREADSHEET_ID, ENEMY_SKIP_TABS
from sync.fetch import fetch_spreadsheet, iter_rows


_OUT_DIR = Path(__file__).resolve().parent / "out"
_RANK_RE = re.compile(r"^\s*(rank\s*[123]|ex\s*[123])\s*$", re.IGNORECASE)


def _color_to_hex(c: dict[str, float] | None) -> str:
    if not c:
        return ""
    r = int(round(c.get("red", 0.0) * 255))
    g = int(round(c.get("green", 0.0) * 255))
    b = int(round(c.get("blue", 0.0) * 255))
    return f"#{r:02x}{g:02x}{b:02x}"


def _bg_hex(cell: dict[str, Any]) -> str:
    fmt = cell.get("effectiveFormat") or {}
    style = fmt.get("backgroundColorStyle") or {}
    return _color_to_hex(style.get("rgbColor")) or _color_to_hex(fmt.get("backgroundColor"))


def _is_image_formula(cell: dict[str, Any]) -> bool:
    uev = cell.get("userEnteredValue") or {}
    f = uev.get("formulaValue") or ""
    return f.upper().startswith("=IMAGE(")


def _safe_filename(s: str) -> str:
    out = re.sub(r"[^\w\-. ]+", "_", s)
    return out.strip().replace(" ", "_") or "tab"


def _truncate(s: str | None, n: int) -> str:
    if not s:
        return ""
    s = str(s)
    return s if len(s) <= n else s[: n - 1] + "…"


def _dump_tab_grid(out_path: Path, sheet: dict[str, Any], rows_cap: int = 200, cols_cap: int = 60) -> None:
    rows = iter_rows(sheet)
    title = sheet.get("properties", {}).get("title", "?")
    sheet_id = sheet.get("properties", {}).get("sheetId", "?")
    lines: list[str] = []
    lines.append(f"# {title} (gid={sheet_id})")
    lines.append(f"# rows total: {len(rows)} | dumping first {min(rows_cap, len(rows))} rows × {cols_cap} cols")
    lines.append("# per cell: TEXT bg=#hex hl=Y/N img=Y/N note=Y/N dv=Y/N")
    lines.append("")
    notes_log: list[str] = []
    for r_i, row in enumerate(rows[:rows_cap]):
        cells = []
        for c_i, cell in enumerate(row[:cols_cap]):
            text = _truncate(cell.get("formattedValue"), 40)
            bg = _bg_hex(cell)
            has_hl = "Y" if cell.get("hyperlink") else "N"
            img = "Y" if _is_image_formula(cell) else "N"
            note = cell.get("note")
            note_flag = "Y" if note else "N"
            dv = cell.get("dataValidation")
            dv_flag = "Y" if dv else "N"
            cells.append(f"[{c_i:>2}] {text!r:<22s} bg={bg or '-':<7s} hl={has_hl} img={img} note={note_flag} dv={dv_flag}")
            if note:
                notes_log.append(f"  r{r_i} c{c_i}: {note!r}")
            if dv:
                cond = dv.get("condition", {})
                t = cond.get("type", "?")
                vals = [v.get("userEnteredValue", "") for v in cond.get("values", [])]
                if vals or t != "?":
                    notes_log.append(f"  r{r_i} c{c_i} DV: type={t} values={vals}")
        lines.append(f"r{r_i:>3}: " + " | ".join(cells))
    if notes_log:
        lines.append("")
        lines.append("# === cell notes / data validation ===")
        lines.extend(notes_log)
    out_path.write_text("\n".join(lines), encoding="utf-8")


def _guess_blocks(sheet: dict[str, Any], max_blocks: int = 3) -> list[dict[str, Any]]:
    """Heuristic block detection for the probe summary only.

    Block start guess: any row where some cell's formattedValue matches a
    rank token (Rank1/2/3, EX1/2/3). The block name is taken from the cell
    immediately to the left of the rank cell on the same row.
    """
    rows = iter_rows(sheet)
    blocks: list[dict[str, Any]] = []
    for r_i, row in enumerate(rows):
        for c_i, cell in enumerate(row):
            text = (cell.get("formattedValue") or "").strip()
            if not text or not _RANK_RE.match(text):
                continue
            name = ""
            for left_i in range(c_i - 1, -1, -1):
                t = (row[left_i].get("formattedValue") or "").strip() if left_i < len(row) else ""
                if t:
                    name = t
                    break
            stat_labels: list[str] = []
            for offset in range(2, 14):
                if r_i + offset >= len(rows):
                    break
                stat_row = rows[r_i + offset]
                if not stat_row:
                    break
                lbl = (stat_row[0].get("formattedValue") or "").strip() if stat_row else ""
                if not lbl:
                    break
                stat_labels.append(lbl)
            blocks.append({
                "row": r_i + 1,
                "rank_cell": (r_i + 1, c_i + 1, text),
                "name_guess": name,
                "stat_labels_guess": stat_labels,
            })
            if len(blocks) >= max_blocks:
                return blocks
            break
    return blocks


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--api-key", default=os.environ.get("GOOGLE_API_KEY"))
    args = p.parse_args(argv)
    if not args.api_key:
        print("ERROR: --api-key or GOOGLE_API_KEY required", file=sys.stderr)
        return 2

    print(f"Fetching {ENEMIES_SPREADSHEET_ID}...")
    payload = fetch_spreadsheet(args.api_key, ENEMIES_SPREADSHEET_ID)
    print(f"Got payload: title={payload.get('properties', {}).get('title')!r}, "
          f"sheets={len(payload.get('sheets', []))}")

    _OUT_DIR.mkdir(parents=True, exist_ok=True)

    tabs_meta: list[dict[str, Any]] = []
    for sheet in payload.get("sheets", []):
        props = sheet.get("properties", {})
        title = props.get("title", "")
        gid = props.get("sheetId")
        grid = props.get("gridProperties", {}) or {}
        tabs_meta.append({
            "gid": gid,
            "title": title,
            "row_count": grid.get("rowCount"),
            "col_count": grid.get("columnCount"),
            "skipped": title in ENEMY_SKIP_TABS,
        })

    (_OUT_DIR / "enemy_tabs.json").write_text(
        json.dumps(tabs_meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"Wrote {_OUT_DIR / 'enemy_tabs.json'}: {len(tabs_meta)} tabs")

    print()
    print("=== Tab inventory ===")
    for t in tabs_meta:
        marker = " [SKIP]" if t["skipped"] else ""
        print(f"  gid={t['gid']:>11}  {t['title']!r:<28s} ({t['row_count']}r x {t['col_count']}c){marker}")

    print()
    print("=== Per-tab grid dumps + block guesses ===")
    # Probe everything including skip-tabs — we need to see where per-rank
    # source data actually lives (likely the *Data tabs).
    for sheet in payload.get("sheets", []):
        title = sheet.get("properties", {}).get("title", "")
        out_file = _OUT_DIR / f"{_safe_filename(title)}.txt"
        _dump_tab_grid(out_file, sheet)
        blocks = _guess_blocks(sheet)
        print(f"\n--- {title} (gid={sheet.get('properties', {}).get('sheetId')}) → {out_file.name}")
        if not blocks:
            print("    (no rank-badge cells detected — layout likely differs)")
            continue
        for b in blocks:
            print(f"    block@row{b['row']}: rank_cell={b['rank_cell']!r}")
            print(f"      name_guess={b['name_guess']!r}")
            print(f"      stat_labels_guess={b['stat_labels_guess']}")

    print()
    print("Probe complete. Inspect verify/out/*.txt before writing the parser.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
