"""Build the offensive coverage matrix from a BucketedTeam.

The coverage matrix is the embed-ready view of which sub-buckets the
team has populated, organised by group AND by source kind (active /
passive / ultimate / pet) so the Phys / Elem / per-weapon / per-element
matrix is visible at a glance — that's what answers "does this team
have Sword DMG Up across active / passive / ultimate?"

Capping (the 30%-per-sub-bucket rule and any cap-raise overrides) is
**not** applied here — the embed prints the raw additive sum so the
user can see when two characters together stack 45% in a sub-bucket
that would normally cap at 30%. The damage multiplier honours the cap;
the coverage matrix is descriptive.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from damage.types import ELEMENTS, WEAPONS

from .types import BucketedTeam, CoverageMatrix


# Source kinds we render in the matrix, in display order. Other kinds
# (ex, divine, latent, tp_passive) collapse into 'active' or 'passive'
# at bucket-binning time per ``aggregator._bucket_source``.
RENDER_SOURCES: tuple[str, ...] = ("active", "passive", "ultimate")

# Pseudo-target labels used to summarise umbrella coverage. After
# aggregation umbrella effects have been fanned out into per-type
# sub-buckets, but if every weapon (or every element) sub-bucket holds
# the same magnitude in the same source kind, the matrix surfaces it
# as a single 'umbrella' line.
UMBRELLA_PHYSICAL = "physical"
UMBRELLA_ELEMENTAL = "elemental"


def build(bucketed: BucketedTeam) -> CoverageMatrix:
    """Group ``raw_sub_bucket_sums`` by their G-prefix into the matrix."""
    g1: dict[str, float] = {}
    g2: dict[str, float] = {}
    g3: dict[str, float] = {}
    g4: dict[str, float] = {}
    g5: dict[str, float] = {}
    for key, val in bucketed.raw_sub_bucket_sums.items():
        if val <= 0:
            continue
        bucket = key.split(".", 1)[0]
        if bucket == "g1":
            g1[key] = val
        elif bucket == "g2":
            g2[key] = val
        elif bucket == "g3":
            g3[key] = val
        elif bucket == "g4":
            g4[key] = val
        elif bucket == "g5":
            g5[key] = val
    return CoverageMatrix(
        g1=g1, g2=g2, g3=g3, g4=g4, g5=g5,
        g6_active=bucketed.divine_beast,
    )


def is_empty(matrix: CoverageMatrix) -> bool:
    """True when no sub-bucket has any contribution and G6 is off."""
    return (
        not matrix.g1 and not matrix.g2 and not matrix.g3
        and not matrix.g4 and not matrix.g5
        and not matrix.g6_active
    )


# ---------------------------------------------------------------------------
# Matrix views — flattened per-source-kind layouts for the embed.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MatrixCell:
    """One sub-bucket's magnitude in the matrix, plus its render label."""

    key: str            # raw sub-bucket key, e.g. 'g2.passive.sword_dmg_up'
    label: str          # human label, e.g. 'Sword'
    magnitude: float    # raw additive sum (may exceed sub-bucket cap)


@dataclass(frozen=True)
class MatrixRow:
    """One (group × source kind) row with its cells in display order."""

    group: str
    source: str
    cells: tuple[MatrixCell, ...]


def matrix_rows_for_group(
    raw_sums: Mapping[str, float], group: str, suffix: str | None,
) -> list[MatrixRow]:
    """Build per-source-kind rows for one group.

    For G2/G3, ``suffix`` is the bucket suffix (``'dmg_up'`` /
    ``'res_down'``); the helper returns one row per source kind in
    ``RENDER_SOURCES`` containing only the populated weapon/element
    sub-buckets, in canonical order (weapons first, then elements).
    For G1, pass ``suffix=None`` and the function returns rows of
    stat-direction sub-buckets (atk_up, def_down, ...).
    """
    rows: list[MatrixRow] = []
    for source in RENDER_SOURCES:
        cells: list[MatrixCell] = []
        if suffix is None:
            for k, v in sorted(raw_sums.items()):
                if not k.startswith(f"{group}.{source}."):
                    continue
                token = k.split(".", 2)[2]
                cells.append(MatrixCell(
                    key=k, label=_pretty_stat(token), magnitude=v,
                ))
        else:
            for canonical in WEAPONS + ELEMENTS:
                key = f"{group}.{source}.{canonical}_{suffix}"
                v = raw_sums.get(key, 0.0)
                if v <= 0:
                    continue
                cells.append(MatrixCell(
                    key=key, label=canonical.title(), magnitude=v,
                ))
        if cells:
            rows.append(MatrixRow(
                group=group, source=source, cells=tuple(cells),
            ))
    return rows


def umbrella_summary(
    raw_sums: Mapping[str, float], group: str, source: str, suffix: str,
) -> tuple[float, float] | None:
    """Detect umbrella coverage in a (group, source) row.

    Returns ``(physical_floor, elemental_floor)`` — the minimum
    magnitude across every weapon sub-bucket and every element
    sub-bucket respectively. When both floors are positive, the team
    has "Phys + Elem umbrella" coverage in that row. Returns ``None``
    when neither floor is positive.
    """
    phys_vals = [
        raw_sums.get(f"{group}.{source}.{w}_{suffix}", 0.0) for w in WEAPONS
    ]
    elem_vals = [
        raw_sums.get(f"{group}.{source}.{e}_{suffix}", 0.0) for e in ELEMENTS
    ]
    phys = min(phys_vals) if phys_vals else 0.0
    elem = min(elem_vals) if elem_vals else 0.0
    if phys <= 0 and elem <= 0:
        return None
    return phys, elem


def label_for_key(key: str) -> str:
    """Human label for a sub-bucket key, e.g. 'Passive Sword DMG Up'."""
    parts = key.split(".")
    if len(parts) != 3:
        return key
    _group, source, suffix = parts
    return f"{source.title()} {suffix.replace('_', ' ').title()}"


def render_pct(value: float) -> str:
    return f"+{round(value * 100)}%"


def top_n(d: Mapping[str, float], n: int = 5) -> list[tuple[str, float]]:
    return sorted(d.items(), key=lambda kv: kv[1], reverse=True)[:n]


# ---------------------------------------------------------------------------
# Per-DPS coverage filter.
# ---------------------------------------------------------------------------

def filter_for_dps(
    raw_sums: Mapping[str, float],
    *,
    weapon: str | None,
    element: str | None,
) -> dict[str, float]:
    """Subset of ``raw_sums`` that matches this DPS's weapon+element.

    Keys for typed sub-buckets (G2/G3) only survive when the type
    matches ``weapon`` or ``element``. Untyped keys (G1 stats) and
    pet/ultimate keys pass through unchanged.
    """
    types = {t for t in (weapon, element) if t}
    out: dict[str, float] = {}
    for k, v in raw_sums.items():
        if v <= 0:
            continue
        parts = k.split(".")
        if len(parts) != 3:
            continue
        group, _src, suffix = parts
        if group in {"g2", "g3"}:
            for canonical in WEAPONS + ELEMENTS:
                if suffix.startswith(canonical + "_") and canonical in types:
                    out[k] = v
                    break
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# Internal — pretty-printers.
# ---------------------------------------------------------------------------

_STAT_RENAMES = {
    "atk_up": "Atk Up", "atk_down": "Atk Down",
    "mag_up": "Mag Up", "mag_down": "Mag Down",
    "def_up": "Def Up", "def_down": "Def Down",
    "mdef_up": "MDef Up", "mdef_down": "MDef Down",
    "crit_up": "Crit Up",
}


def _pretty_stat(token: str) -> str:
    return _STAT_RENAMES.get(token, token.replace("_", " ").title())
