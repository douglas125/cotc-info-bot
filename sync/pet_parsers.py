"""Parse the Seed Story Content "Pet List" tab into structured pet records.

The tab is a regular 4-rows-per-pet block layout:

  Row r₀:    [name] | … | "HP"  | hp_val | … | "SP"    | sp_val   | ABILITY (multi-line) | … | source
  Row r₀+1:        |   | "Patk"| patk   | … | "Pdef"  | pdef     |
  Row r₀+2:        |   | "Matk"| matk   | … | "Mdef"  | mdef     |
  Row r₀+3:        |   | "Crit"| crit   | … | "Speed" | speed    |

Stat values are read by *label* (find the cell whose text equals "Patk",
take its right neighbor) so column drift in the source sheet does not
silently corrupt values.

The ABILITY cell packs (separated by `\\n`):
  - effect text (1-3 lines)
  - optional `Max Boost: Lv2/3/4`
  - `Turn Preparation: N (Lv10: N-1)`
  - `Turn Cooldown: M (Lv5: M-1)`

The maintainer's hand-typed lines have real-world inconsistencies
(`Lv.10`, `Lv:` instead of `Lv5:`, missing inner digits, even one entry
where the maintainer typed "Turn Preparation" twice and meant the
second one as cooldown). The regexes are deliberately permissive and
the Christmas-Dog typo auto-heals while emitting a warning so the
upstream maintainer gets the nudge to fix the source.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sync.fetch import sheet_by_gid
from sync.parsers import _cell_color_hex, _cell_text


# Block stride and label/value column anchors. Reading by *label* is
# preferred over hard-coded indices, but the constants double as
# fallbacks if a block has only one of the two label cells.
BLOCK_HEIGHT = 4

_STAT_LABELS = {
    "HP", "SP", "Patk", "Pdef", "Matk", "Mdef", "Crit", "Speed",
}


@dataclass
class ParsedPet:
    canonical_name: str
    display_name_jp: str
    source_text: str | None = None
    ability_text: str = ""
    max_boost: str | None = None
    prep_base: int | None = None
    prep_lv10: int | None = None
    cooldown_base: int | None = None
    cooldown_lv5: int | None = None
    hp: int | None = None
    sp: int | None = None
    patk: int | None = None
    pdef: int | None = None
    matk: int | None = None
    mdef: int | None = None
    crit: int | None = None
    speed: int | None = None
    sheet_gid: int | None = None
    source_row: int | None = None
    name_color_hex: str | None = None
    hyperlink_url: str | None = None


_PARENS_RE = re.compile(r"\(([^()]*)\)\s*$")
_PREP_RE = re.compile(
    r"^[ \t]*Turn[ \t]+Preparation[ \t]*[:\-][ \t]*(\d+)"
    r"(?:[ \t]*\([ \t]*Lv[ \t]*\.?[ \t]*\d*[ \t]*[:.]?[ \t]*(\d+)?[ \t]*\))?",
    re.IGNORECASE | re.MULTILINE,
)
_COOLDOWN_RE = re.compile(
    r"^[ \t]*Turn[ \t]+Cooldown[ \t]*[:\-][ \t]*(\d+)"
    r"(?:[ \t]*\([ \t]*Lv[ \t]*\.?[ \t]*\d*[ \t]*[:.]?[ \t]*(\d+)?[ \t]*\))?",
    re.IGNORECASE | re.MULTILINE,
)
_MAX_BOOST_RE = re.compile(
    r"^[ \t]*Max[ \t]+Boost[ \t]*[:\-][ \t]*(\S.+?)[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)


def _extract_canonical_name(raw: str) -> tuple[str, str]:
    """Pull the English name from `<JP> (<English>)`.

    Returns `(canonical_english, raw_input)` — picks the LAST `(...)`
    group so nested cases like `'ルールー (紫) (Purple Lulu )'` yield
    `'Purple Lulu'`. Falls back to the raw string if no parens at all.
    The input may contain stray tabs/newlines (one upstream row uses
    `黒茶\\t(Black Brown Dog)`); whitespace is normalized before regex.
    """
    raw_clean = re.sub(r"\s+", " ", raw).strip()
    m = _PARENS_RE.search(raw_clean)
    if not m:
        return raw_clean, raw
    return m.group(1).strip(), raw


def _to_int(s: str | None) -> int | None:
    if s is None:
        return None
    s = s.strip().replace(",", "")
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        try:
            return int(float(s))
        except ValueError:
            return None


def _stat_pair_from_row(
    row: list[dict[str, Any]],
) -> dict[str, int | None]:
    """Read all label→value pairs visible in this row.

    Walks the row left-to-right; whenever it sees a known stat label it
    pairs it with the *next non-empty cell to the right*. The "non-empty
    to the right" hop handles the source layout where the label sits at
    col 2 and the value at col 3, with col 4 blank between pairs.
    """
    out: dict[str, int | None] = {}
    n = len(row)
    for i in range(n):
        label = _cell_text(row[i])
        if label in _STAT_LABELS:
            for j in range(i + 1, n):
                v = _cell_text(row[j])
                if v != "":
                    out[label] = _to_int(v)
                    break
    return out


# Marker regexes used to *identify* the ability cell. They look for the
# structured trailers anchored at line-start so prose elsewhere can't
# match. Only `Turn Preparation` / `Turn Cooldown` are used: every pet has
# at least one of these, and they don't appear in source-text cells like
# "Awakening Exchange" or "Quest".
_ABILITY_MARKER_RE = re.compile(
    r"^[ \t]*Turn[ \t]+(?:Preparation|Cooldown)\b",
    re.IGNORECASE | re.MULTILINE,
)


def _ability_field(row: list[dict[str, Any]]) -> str:
    """Pick the ability cell out of a name-row.

    Identification is by *content marker*: the cell whose text contains
    a `Turn Preparation:` or `Turn Cooldown:` line. A longest-multiline
    heuristic was used previously, but that swapped fields when a pet's
    source-text cell happened to be longer than the ability cell (e.g.
    `'Awakening Exchange\\n(6K Shards)\\n\\nRequire <long prerequisite>'`).
    Falling back to longest-multiline only if no cell carries the
    structured markers — covers pets without prep/cooldown lines, which
    aren't expected to exist in the live sheet today but shouldn't crash
    the parser if one ever does.
    """
    for cell in row:
        t = cell.get("formattedValue") or ""
        if t and _ABILITY_MARKER_RE.search(t):
            return t
    # Fallback: longest multi-line cell.
    best = ""
    for cell in row:
        t = cell.get("formattedValue") or ""
        if "\n" in t and len(t) > len(best):
            best = t
    if best:
        return best
    # Final fallback: longest non-stat-label, non-numeric cell.
    longest = ""
    for cell in row:
        t = cell.get("formattedValue") or ""
        if len(t) > len(longest) and t not in _STAT_LABELS and not t.isdigit():
            longest = t
    return longest


def _source_field(row: list[dict[str, Any]], ability_text: str) -> str | None:
    """Pick the source ('how to obtain') cell — the rightmost non-empty
    cell that isn't the ability cell."""
    for cell in reversed(row):
        t = cell.get("formattedValue") or ""
        if not t:
            continue
        if t == ability_text:
            continue
        if t in _STAT_LABELS or t.isdigit():
            continue
        return t.strip()
    return None


def _parse_ability_block(
    text: str, *, pet_label: str, warnings: list[str],
) -> tuple[str, str | None, int | None, int | None, int | None, int | None]:
    """Split an ability cell into (effect_text, max_boost, prep, prep_lv10, cd, cd_lv5).

    Lines above the earliest of the structured labels (`Max Boost:`,
    `Turn Preparation:`, `Turn Cooldown:`) are the effect text. Each
    structured field is then matched anywhere in the remainder. Christmas
    Dog's "two Turn Preparation lines, no Turn Cooldown" typo is
    auto-healed: the second `Turn Preparation:` is treated as cooldown,
    and a warning is appended to `warnings` for the runner to log.
    """
    if not text:
        return "", None, None, None, None, None

    # Find the start of the structured trailer (whichever label appears first).
    boundary = len(text)
    for label_re in ("^[ \\t]*Max[ \\t]+Boost",
                     "^[ \\t]*Turn[ \\t]+Preparation",
                     "^[ \\t]*Turn[ \\t]+Cooldown"):
        m = re.search(label_re, text, re.IGNORECASE | re.MULTILINE)
        if m and m.start() < boundary:
            boundary = m.start()

    effect_raw = text[:boundary].rstrip()
    trailer = text[boundary:]

    # Preserve internal blank lines so multi-paragraph effects render naturally.
    lines = effect_raw.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    effect_text = "\n".join(lines).rstrip()

    mb_match = _MAX_BOOST_RE.search(trailer)
    max_boost = mb_match.group(1).strip() if mb_match else None

    prep_matches = list(_PREP_RE.finditer(trailer))
    cd_matches = list(_COOLDOWN_RE.finditer(trailer))

    prep_base = prep_lv10 = cooldown_base = cooldown_lv5 = None
    if prep_matches:
        prep_base = _to_int(prep_matches[0].group(1))
        prep_lv10 = _to_int(prep_matches[0].group(2))
    if cd_matches:
        cooldown_base = _to_int(cd_matches[0].group(1))
        cooldown_lv5 = _to_int(cd_matches[0].group(2))

    # Christmas-Dog auto-heal: maintainer typed "Turn Preparation" twice
    # and meant the second one as cooldown. Apply only when we have two
    # prep matches and no cooldown match.
    if not cd_matches and len(prep_matches) >= 2:
        cooldown_base = _to_int(prep_matches[1].group(1))
        cooldown_lv5 = _to_int(prep_matches[1].group(2))
        warnings.append(
            f"{pet_label}: ambiguous 'Turn Preparation' (second occurrence "
            f"treated as Turn Cooldown — please fix upstream typo)"
        )

    return effect_text, max_boost, prep_base, prep_lv10, cooldown_base, cooldown_lv5


def _hyperlink_for_row(row_idx_zero_based: int, gid: int) -> str:
    """A1-style anchor pointing at the name cell of this block.

    The Pet List name lives in column A (index 0), row r_idx (0-based).
    Sheets uses 1-based rows in URL ranges, so add 1.
    """
    return f"#gid={gid}&range=A{row_idx_zero_based + 1}"


def _is_block_anchor(row: list[dict[str, Any]]) -> bool:
    """A row is the start of a pet block iff col 0 has non-empty text AND
    one of the cells in the row equals 'HP'."""
    if not row:
        return False
    name = _cell_text(row[0])
    if not name:
        return False
    return any(_cell_text(c) == "HP" for c in row)


def parse_pets(
    payload: dict[str, Any], gid: int,
) -> tuple[list[ParsedPet], list[str]]:
    """Extract every pet block from the named tab in a fresh sheets payload.

    Returns `(pets, warnings)`. `warnings` is a list of human-readable
    strings the runner should pass through `progress(...)` so messy
    upstream cells stay visible without breaking the sync.
    """
    pets: list[ParsedPet] = []
    warnings: list[str] = []

    if not gid:
        # Probe not yet run; non-fatal, just return empty.
        return pets, warnings

    sheet = sheet_by_gid(payload, gid)
    if sheet is None:
        warnings.append(f"Pet sheet gid={gid} not found in payload")
        return pets, warnings

    rows: list[list[dict[str, Any]]] = []
    for grid in sheet.get("data", []) or []:
        for r in grid.get("rowData", []) or []:
            rows.append(r.get("values", []) or [])

    r_i = 0
    while r_i < len(rows):
        if not _is_block_anchor(rows[r_i]):
            r_i += 1
            continue

        name_cell = rows[r_i][0] if rows[r_i] else {}
        name_raw = _cell_text(name_cell)
        canonical, display_jp = _extract_canonical_name(name_raw)
        if not canonical or len(canonical) < 2:
            warnings.append(
                f"row {r_i + 1}: skipped block with unparseable name {name_raw!r}"
            )
            r_i += 1
            continue

        # Collect stats from rows r_i .. r_i + 3 (block height).
        stats: dict[str, int | None] = {}
        for offset in range(BLOCK_HEIGHT):
            ri = r_i + offset
            if ri >= len(rows):
                break
            stats.update(_stat_pair_from_row(rows[ri]))

        ability_raw = _ability_field(rows[r_i])
        source = _source_field(rows[r_i], ability_raw)
        eff, mb, prep_b, prep_lv10, cd_b, cd_lv5 = _parse_ability_block(
            ability_raw,
            pet_label=canonical,
            warnings=warnings,
        )

        pet = ParsedPet(
            canonical_name=canonical,
            display_name_jp=display_jp.strip() if display_jp else display_jp,
            source_text=source,
            ability_text=eff,
            max_boost=mb,
            prep_base=prep_b,
            prep_lv10=prep_lv10,
            cooldown_base=cd_b,
            cooldown_lv5=cd_lv5,
            hp=stats.get("HP"),
            sp=stats.get("SP"),
            patk=stats.get("Patk"),
            pdef=stats.get("Pdef"),
            matk=stats.get("Matk"),
            mdef=stats.get("Mdef"),
            crit=stats.get("Crit"),
            speed=stats.get("Speed"),
            sheet_gid=gid,
            source_row=r_i,
            name_color_hex=_cell_color_hex(name_cell),
            hyperlink_url=name_cell.get("hyperlink") or _hyperlink_for_row(r_i, gid),
        )
        pets.append(pet)
        r_i += BLOCK_HEIGHT

    return pets, warnings
