"""One-shot probe of the Seed Story Content pet sheet.

Run before locking in `config.PETS_LIST_GID` so the gid, tab inventory,
and block layout come from the *live* sheet rather than guesses. Mirrors
`verify.probe_enemies` — same `spreadsheets.get?includeGridData=true` call
the rest of the pipeline uses, then dumps human-readable artifacts under
`verify/out/` for manual inspection.

Outputs:
  verify/out/pet_tabs.json         — per-tab metadata (gid, title, dims)
  verify/out/<tab_title>.txt       — first ~200 rows × ~20 cols of each tab,
                                     with text + bg color + hyperlink + image
                                     formula presence
  stdout                           — first 5 detected pet blocks per tab
                                     (parsed name / hp / prep / cooldown), so
                                     the parser shape can be sanity-checked
                                     before wiring it into the runner.

Usage:
  conda activate cotc-search
  python -m verify.probe_pets --api-key "$(grep api_key ~/.cotc-search/config.toml | cut -d'"' -f2)"
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from config import PETS_SPREADSHEET_ID
from sync.fetch import fetch_spreadsheet, iter_rows

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass


_OUT_DIR = Path(__file__).resolve().parent / "out"


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


def _dump_tab_grid(out_path: Path, sheet: dict[str, Any], rows_cap: int = 200, cols_cap: int = 20) -> None:
    rows = iter_rows(sheet)
    title = sheet.get("properties", {}).get("title", "?")
    sheet_id = sheet.get("properties", {}).get("sheetId", "?")
    lines: list[str] = []
    lines.append(f"# {title} (gid={sheet_id})")
    lines.append(f"# rows total: {len(rows)} | dumping first {min(rows_cap, len(rows))} rows × {cols_cap} cols")
    lines.append("# per cell: TEXT bg=#hex hl=Y/N img=Y/N")
    lines.append("")
    for r_i, row in enumerate(rows[:rows_cap]):
        cells = []
        for c_i, cell in enumerate(row[:cols_cap]):
            text = _truncate(cell.get("formattedValue"), 40)
            bg = _bg_hex(cell)
            has_hl = "Y" if cell.get("hyperlink") else "N"
            img = "Y" if _is_image_formula(cell) else "N"
            cells.append(f"[{c_i:>2}] {text!r:<24s} bg={bg or '-':<7s} hl={has_hl} img={img}")
        lines.append(f"r{r_i:>3}: " + " | ".join(cells))
    out_path.write_text("\n".join(lines), encoding="utf-8")


_PARENS_RE = re.compile(r"\(([^()]*)\)\s*$")
_PREP_RE = re.compile(
    r"^\s*Turn\s+Preparation\s*[:\-]\s*(\d+)"
    r"(?:\s*\(\s*Lv\s*\.?\s*\d*\s*[:.]?\s*(\d+)?\s*\))?",
    re.IGNORECASE | re.MULTILINE,
)
_COOLDOWN_RE = re.compile(
    r"^\s*Turn\s+Cooldown\s*[:\-]\s*(\d+)"
    r"(?:\s*\(\s*Lv\s*\.?\s*\d*\s*[:.]?\s*(\d+)?\s*\))?",
    re.IGNORECASE | re.MULTILINE,
)


def _peek_first_blocks(sheet: dict[str, Any], max_blocks: int = 5) -> list[dict[str, Any]]:
    rows = iter_rows(sheet)
    out: list[dict[str, Any]] = []
    r_i = 0
    while r_i < len(rows) and len(out) < max_blocks:
        row = rows[r_i]
        if not row:
            r_i += 1
            continue
        name_cell = row[0] if row else {}
        name_raw = (name_cell.get("formattedValue") or "").strip()
        if not name_raw:
            r_i += 1
            continue
        # Look for "HP" label anywhere in this row to confirm a block anchor.
        hp_idx = None
        for ci, cell in enumerate(row):
            if (cell.get("formattedValue") or "").strip() == "HP":
                hp_idx = ci
                break
        if hp_idx is None:
            r_i += 1
            continue
        hp_val = ""
        if hp_idx + 1 < len(row):
            hp_val = (row[hp_idx + 1].get("formattedValue") or "").strip()
        # Find the ability cell — pick the longest formattedValue in the row
        # past the HP/SP labels (the multiline ability block dwarfs the rest).
        ability = ""
        for cell in row:
            t = cell.get("formattedValue") or ""
            if "\n" in t and len(t) > len(ability):
                ability = t
        m = _PARENS_RE.search(name_raw)
        canonical = m.group(1).strip() if m else name_raw
        prep = _PREP_RE.search(ability)
        cd = _COOLDOWN_RE.search(ability)
        out.append({
            "row": r_i + 1,
            "name_raw": name_raw,
            "canonical": canonical,
            "hp": hp_val,
            "prep": prep.groups() if prep else None,
            "cd": cd.groups() if cd else None,
        })
        r_i += 4
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--api-key", default=os.environ.get("GOOGLE_API_KEY"))
    args = p.parse_args(argv)
    if not args.api_key:
        print("ERROR: --api-key or GOOGLE_API_KEY required", file=sys.stderr)
        return 2

    print(f"Fetching {PETS_SPREADSHEET_ID}...")
    payload = fetch_spreadsheet(args.api_key, PETS_SPREADSHEET_ID)
    print(f"Got payload: title={payload.get('properties', {}).get('title')!r}, "
          f"sheets={len(payload.get('sheets', []))}")

    _OUT_DIR.mkdir(parents=True, exist_ok=True)

    tabs_meta: list[dict[str, Any]] = []
    for sheet in payload.get("sheets", []):
        props = sheet.get("properties", {})
        grid = props.get("gridProperties", {}) or {}
        tabs_meta.append({
            "gid": props.get("sheetId"),
            "title": props.get("title", ""),
            "row_count": grid.get("rowCount"),
            "col_count": grid.get("columnCount"),
        })
    (_OUT_DIR / "pet_tabs.json").write_text(
        json.dumps(tabs_meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"Wrote {_OUT_DIR / 'pet_tabs.json'}: {len(tabs_meta)} tabs")

    print()
    print("=== Tab inventory ===")
    for t in tabs_meta:
        print(f"  gid={t['gid']:>11}  {t['title']!r:<30s} ({t['row_count']}r x {t['col_count']}c)")

    print()
    print("=== Per-tab grid dumps + first-block previews ===")
    for sheet in payload.get("sheets", []):
        title = sheet.get("properties", {}).get("title", "")
        gid = sheet.get("properties", {}).get("sheetId")
        out_file = _OUT_DIR / f"{_safe_filename(title)}.txt"
        _dump_tab_grid(out_file, sheet)
        blocks = _peek_first_blocks(sheet)
        print(f"\n--- {title} (gid={gid}) → {out_file.name}")
        if not blocks:
            print("    (no pet-shaped blocks detected — layout likely differs)")
            continue
        for b in blocks:
            print(f"    block@row{b['row']}:  raw={b['name_raw']!r}")
            print(f"      canonical={b['canonical']!r} HP={b['hp']!r}")
            print(f"      prep={b['prep']} cd={b['cd']}")

    print()
    print("Probe complete. Inspect verify/out/*.txt and copy the 'Pet List'")
    print("gid into config.py::PETS_LIST_GID before running the parser.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
