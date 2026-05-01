"""PNG renderer for the team's bucket-matrix view.

The output is ONE PNG containing TWO matrices stacked vertically:

  - **Top matrix — Physical attacks:** 8 weapon columns
    (Sword / Dagger / Bow / Axe / Staff / Tome / Fan / Spear).
    G1 rows show Atk Up / Def Down (the stats that scale physical
    damage and break physical resistance).
  - **Bottom matrix — Elemental attacks:** 6 element columns
    (Fire / Ice / Lightning / Wind / Light / Dark).
    G1 rows show Mag Up / MDef Down (the elemental analogues).

Both matrices share G2 / G3 / G4 row taxonomy. Each ends with a
"Final multiplier" footer row that pulls from
:func:`analysis.damage_estimate.final_multiplier_for_type` so the
column total matches the audit-CLI text matrix line by line.

Cells exceeding the 30% sub-bucket cap render with a red strikethrough
at mid-text height. Crit-applied columns get a ``★`` glyph above the
multiplier (sourced from ``bucketed.crit_types``).

This module is pure logic: it imports PIL and ``bot.weakness_icons``
(both Discord-runtime-free), but never imports ``discord``.
"""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont

from analysis import damage_estimate
from analysis.types import BucketedTeam
from bot import weakness_icons
from damage.types import DEFAULT_SUB_BUCKET_CAP, ELEMENTS, WEAPONS


# ---------------------------------------------------------------------------
# Layout constants — tweak here when iterating on the visual output.
# ---------------------------------------------------------------------------

ICON_SIZE = 20
CELL_WIDTH = 64
CELL_HEIGHT = 32          # tall enough for two stacked lines on over-cap cells
LABEL_WIDTH = 220
HEADER_HEIGHT = 38
TITLE_HEIGHT = 28
PAD_X = 12
PAD_Y = 12
GAP_BETWEEN_MATRICES = 22
FOOTER_HEIGHT = 32
FINAL_ROW_EXTRA = 8       # extra padding so the ★ glyph fits above

# Colours (RGBA).
BG_COLOR = (250, 250, 250, 255)
GRID_COLOR = (215, 215, 220, 255)
ROW_BG_LIGHT = (250, 250, 250, 255)
ROW_BG_ALT = (240, 240, 244, 255)
TITLE_BG = (60, 70, 90, 255)
TITLE_TEXT = (240, 240, 240, 255)
TEXT_COLOR = (40, 40, 40, 255)
LABEL_COLOR = (60, 60, 70, 255)
OVERCAP_COLOR = (220, 60, 60, 255)
CAPPED_COLOR = (40, 130, 60, 255)     # green — effective contribution after cap
CRIT_COLOR = (220, 100, 30, 255)
FINAL_BG = (228, 240, 220, 255)
HEADER_BG = (235, 238, 245, 255)
FOOTER_BG = (245, 245, 250, 255)


# ---------------------------------------------------------------------------
# Public surface.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RenderedMatrixImage:
    filename: str
    data: bytes


@dataclass(frozen=True)
class _MatrixRow:
    label: str
    values: tuple[float, ...]  # one entry per column


def render(
    bucketed: BucketedTeam,
    *,
    type_multipliers: dict[str, float] | None = None,
) -> RenderedMatrixImage:
    """Render the team's bucket matrix to a PNG.

    ``type_multipliers`` is an optional precomputed
    ``{weapon|element: final multiplier}`` map (see
    :func:`damage_estimate.final_multipliers_for_team`). Pass it when
    the caller already computed the per-type multipliers for the embed
    so the renderer doesn't redo the work.
    """
    if type_multipliers is None:
        type_multipliers = damage_estimate.final_multipliers_for_team(bucketed)
    physical_rows = _build_rows(
        bucketed, columns=WEAPONS,
        stat_up="atk_up", stat_down="def_down",
    )
    elemental_rows = _build_rows(
        bucketed, columns=ELEMENTS,
        stat_up="mag_up", stat_down="mdef_down",
    )

    phys_block_w = LABEL_WIDTH + len(WEAPONS) * CELL_WIDTH
    elem_block_w = LABEL_WIDTH + len(ELEMENTS) * CELL_WIDTH
    canvas_w = max(phys_block_w, elem_block_w) + 2 * PAD_X

    phys_block_h = _matrix_block_height(len(physical_rows))
    elem_block_h = _matrix_block_height(len(elemental_rows))
    canvas_h = (
        PAD_Y
        + phys_block_h
        + GAP_BETWEEN_MATRICES
        + elem_block_h
        + (FOOTER_HEIGHT if bucketed.divine_beast else 0)
        + PAD_Y
    )

    image = Image.new("RGBA", (canvas_w, canvas_h), BG_COLOR)
    draw = ImageDraw.Draw(image)

    # Top: Physical matrix, left-aligned.
    phys_x = PAD_X
    phys_y = PAD_Y
    _render_matrix(
        image, draw,
        x=phys_x, y=phys_y,
        title="Physical attacks",
        columns=WEAPONS,
        rows=physical_rows,
        bucketed=bucketed,
        type_multipliers=type_multipliers,
    )

    # Bottom: Elemental matrix, centered horizontally under the physical.
    elem_x = PAD_X + (phys_block_w - elem_block_w) // 2
    elem_y = phys_y + phys_block_h + GAP_BETWEEN_MATRICES
    _render_matrix(
        image, draw,
        x=elem_x, y=elem_y,
        title="Elemental attacks",
        columns=ELEMENTS,
        rows=elemental_rows,
        bucketed=bucketed,
        type_multipliers=type_multipliers,
    )

    if bucketed.divine_beast:
        footer_y = elem_y + elem_block_h + 6
        draw.rectangle(
            (PAD_X, footer_y, canvas_w - PAD_X, footer_y + FOOTER_HEIGHT - 6),
            fill=FOOTER_BG,
        )
        draw.text(
            (PAD_X + 8, footer_y + 6),
            "G6 Divine Beast: ON   ×1.10",
            font=_font(13, bold=True),
            fill=TEXT_COLOR,
        )

    out = BytesIO()
    image.save(out, format="PNG", optimize=True)
    return RenderedMatrixImage(filename="team_matrix.png", data=out.getvalue())


# ---------------------------------------------------------------------------
# Row data builders.
# ---------------------------------------------------------------------------

def _build_rows(
    bucketed: BucketedTeam,
    *,
    columns: tuple[str, ...],
    stat_up: str,        # "atk_up" for physical, "mag_up" for elemental
    stat_down: str,      # "def_down" / "mdef_down"
) -> list[_MatrixRow]:
    """Build the per-(group, source) rows for one matrix sub-table.

    All-zero rows are dropped so empty G4 sub-pools don't clutter the
    image when no team member has Ultimate buffs of that shape.
    """
    sums = bucketed.raw_sub_bucket_sums
    rows: list[_MatrixRow] = []

    def replicated(label: str, key: str) -> None:
        v = float(sums.get(key, 0.0))
        if v > 0:
            rows.append(_MatrixRow(label=label, values=tuple(v for _ in columns)))

    def per_type(label: str, group: str, source: str, suffix: str) -> None:
        values = tuple(
            float(sums.get(f"{group}.{source}.{t}_{suffix}", 0.0))
            for t in columns
        )
        if any(v > 0 for v in values):
            rows.append(_MatrixRow(label=label, values=values))

    up_label = stat_up.replace("_", " ").title()        # "Atk Up" / "Mag Up"
    down_label = stat_down.replace("_", " ").title()    # "Def Down" / "Mdef Down"
    replicated(f"G1 Active {up_label}",     f"g1.active.{stat_up}")
    replicated(f"G1 Active {down_label}",   f"g1.active.{stat_down}")
    replicated(f"G1 Passive {up_label}",    f"g1.passive.{stat_up}")
    replicated(f"G1 Passive {down_label}",  f"g1.passive.{stat_down}")
    per_type("G2 Active DMG Up",            "g2", "active",   "dmg_up")
    per_type("G2 Passive DMG Up",           "g2", "passive",  "dmg_up")
    per_type("G3 Active Res Down",          "g3", "active",   "res_down")
    per_type("G3 Passive Res Down",         "g3", "passive",  "res_down")
    replicated(f"G4 Ult {up_label}",        f"g4.ultimate.{stat_up}")
    replicated(f"G4 Ult {down_label}",      f"g4.ultimate.{stat_down}")
    per_type("G4 Ult DMG Up",               "g4", "ultimate", "dmg_up")
    per_type("G4 Ult Res Down",             "g4", "ultimate", "res_down")
    return rows


# ---------------------------------------------------------------------------
# Renderer.
# ---------------------------------------------------------------------------

def _matrix_block_height(n_data_rows: int) -> int:
    """Vertical pixels for one matrix block (title + header + rows + final mult)."""
    return (
        TITLE_HEIGHT
        + HEADER_HEIGHT
        + n_data_rows * CELL_HEIGHT
        + CELL_HEIGHT + 2 * FINAL_ROW_EXTRA
    )


def _render_matrix(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    *,
    x: int,
    y: int,
    title: str,
    columns: tuple[str, ...],
    rows: list[_MatrixRow],
    bucketed: BucketedTeam,
    type_multipliers: dict[str, float],
) -> None:
    n_cols = len(columns)
    block_w = LABEL_WIDTH + n_cols * CELL_WIDTH

    # Title strip.
    draw.rectangle((x, y, x + block_w, y + TITLE_HEIGHT), fill=TITLE_BG)
    title_font = _font(14, bold=True)
    draw.text((x + 8, y + 6), title, font=title_font, fill=TITLE_TEXT)
    cy = y + TITLE_HEIGHT

    # Header row: blank label area + icon-and-label per column.
    draw.rectangle((x, cy, x + block_w, cy + HEADER_HEIGHT), fill=HEADER_BG)
    icon_font = _font(9)
    for i, col_name in enumerate(columns):
        cx = x + LABEL_WIDTH + i * CELL_WIDTH
        canonical = col_name.title()
        icon = weakness_icons.load_icon(canonical, ICON_SIZE)
        if icon is not None:
            ix = cx + (CELL_WIDTH - ICON_SIZE) // 2
            iy = cy + 4
            image.alpha_composite(icon, (ix, iy))
        label = canonical
        tw = draw.textlength(label, font=icon_font)
        if tw > CELL_WIDTH - 4:
            label = canonical[:6]
            tw = draw.textlength(label, font=icon_font)
        draw.text(
            (cx + (CELL_WIDTH - tw) // 2, cy + 4 + ICON_SIZE + 1),
            label,
            font=icon_font,
            fill=LABEL_COLOR,
        )
    cy += HEADER_HEIGHT

    # Body data rows. Cap is column-independent today (see _cap_for);
    # one lookup per row is enough.
    body_font = _font(11)
    small_font = _font(9)
    label_font = _font(11, bold=False)
    for ri, row in enumerate(rows):
        bg = ROW_BG_LIGHT if ri % 2 == 0 else ROW_BG_ALT
        draw.rectangle((x, cy, x + block_w, cy + CELL_HEIGHT), fill=bg)
        draw.text(
            (x + 6, cy + (CELL_HEIGHT - 14) // 2),
            row.label, font=label_font, fill=LABEL_COLOR,
        )
        cap = _cap_for(bucketed, row.label)
        for ci, val in enumerate(row.values):
            cx = x + LABEL_WIDTH + ci * CELL_WIDTH
            draw.line((cx, cy, cx, cy + CELL_HEIGHT), fill=GRID_COLOR)
            if val > 0:
                _draw_value_cell(
                    draw, cx=cx, cy=cy, val=val, cap=cap,
                    font=body_font, small_font=small_font,
                )
        # Right-edge gridline closing the row.
        draw.line(
            (x + block_w, cy, x + block_w, cy + CELL_HEIGHT),
            fill=GRID_COLOR,
        )
        cy += CELL_HEIGHT

    # Final multiplier row. Crit-applied columns get a polygon star
    # drawn to the left of the multiplier (font-independent — avoids
    # the missing-glyph tofu seen with `"★"` text on Windows Arial).
    final_height = CELL_HEIGHT + FINAL_ROW_EXTRA
    cy += FINAL_ROW_EXTRA // 2
    draw.rectangle((x, cy, x + block_w, cy + final_height), fill=FINAL_BG)
    final_font = _font(12, bold=True)
    draw.text(
        (x + 6, cy + (final_height - 13) // 2),
        "Final multiplier",
        font=final_font,
        fill=LABEL_COLOR,
    )
    for ci, col_name in enumerate(columns):
        cx = x + LABEL_WIDTH + ci * CELL_WIDTH
        draw.line((cx, cy, cx, cy + final_height), fill=GRID_COLOR)
        mult = type_multipliers[col_name]
        is_crit = damage_estimate.type_has_guaranteed_crit(bucketed, col_name)
        text = f"×{mult:.2f}"
        tw = draw.textlength(text, font=final_font)
        ty = cy + (final_height - 14) // 2
        if is_crit:
            star_size = 11
            gap = 4
            total_w = star_size + gap + tw
            tx = cx + (CELL_WIDTH - total_w) // 2
            star_cy = cy + final_height // 2
            _draw_star(
                draw,
                cx=tx + star_size // 2, cy=star_cy,
                radius=star_size // 2, fill=CRIT_COLOR,
            )
            draw.text((tx + star_size + gap, ty), text, font=final_font, fill=TEXT_COLOR)
        else:
            tx = cx + (CELL_WIDTH - tw) // 2
            draw.text((tx, ty), text, font=final_font, fill=TEXT_COLOR)
    draw.line(
        (x + block_w, cy, x + block_w, cy + final_height),
        fill=GRID_COLOR,
    )


def _draw_value_cell(
    draw: ImageDraw.ImageDraw,
    *,
    cx: int,
    cy: int,
    val: float,
    cap: float,
    font: ImageFont.ImageFont,
    small_font: ImageFont.ImageFont,
) -> None:
    """Draw the magnitude.

    If ``val <= cap`` (under the sub-bucket cap), render a single
    centered line in normal text colour.

    If ``val > cap``, render two stacked lines:
      - Top: the raw additive sum, struck through in red.
      - Bottom: the capped value (e.g. "→ 30%") in green so the
        reader sees the effective contribution that goes into the
        damage formula.
    """
    pct = round(val * 100)
    text = f"{pct}%"

    if val <= cap:
        tw = draw.textlength(text, font=font)
        tx = cx + (CELL_WIDTH - tw) // 2
        ty = cy + (CELL_HEIGHT - 14) // 2
        draw.text((tx, ty), text, font=font, fill=TEXT_COLOR)
        return

    # Over-cap path: stacked two-line cell.
    cap_pct = round(cap * 100)
    cap_text = f"→ {cap_pct}%"  # "→ 30%"
    raw_w = draw.textlength(text, font=small_font)
    cap_w = draw.textlength(cap_text, font=small_font)
    raw_tx = cx + (CELL_WIDTH - raw_w) // 2
    cap_tx = cx + (CELL_WIDTH - cap_w) // 2
    raw_ty = cy + 3
    cap_ty = cy + CELL_HEIGHT - 14
    draw.text((raw_tx, raw_ty), text, font=small_font, fill=OVERCAP_COLOR)
    # Strikethrough the raw value at mid-text height.
    line_y = raw_ty + 6
    draw.line(
        (raw_tx - 2, line_y, raw_tx + raw_w + 2, line_y),
        fill=OVERCAP_COLOR,
        width=2,
    )
    draw.text((cap_tx, cap_ty), cap_text, font=small_font, fill=CAPPED_COLOR)


def _cap_for(bucketed: BucketedTeam, row_label: str) -> float:
    """The sub-bucket cap that applies to one row.

    Today every sub-bucket caps at ``DEFAULT_SUB_BUCKET_CAP`` (30%).
    When per-sub-bucket cap-raise effects land in BucketedTeam (e.g.
    Black Knight EX raising Self's Atk Up + Sword Damage Up cap to
    50%), this helper is the seam where the override gets surfaced —
    extend the signature with a column once cap-raises become
    column-specific.
    """
    return DEFAULT_SUB_BUCKET_CAP


def _draw_star(
    draw: ImageDraw.ImageDraw,
    *,
    cx: int,
    cy: int,
    radius: int,
    fill: tuple[int, int, int, int],
) -> None:
    """Draw a 5-pointed star centered at (cx, cy) — font-independent.

    Used for the crit marker on final-multiplier cells; works on every
    host because it doesn't depend on a font's glyph coverage.
    """
    import math
    inner = max(2, radius // 2)
    points: list[tuple[float, float]] = []
    # 10 vertices alternating outer/inner radius around 360°.
    for i in range(10):
        angle = -math.pi / 2 + i * math.pi / 5
        r = radius if i % 2 == 0 else inner
        points.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    draw.polygon(points, fill=fill)


# ---------------------------------------------------------------------------
# Font loading.
# ---------------------------------------------------------------------------

_FONT_CACHE: dict[tuple[int, bool], ImageFont.ImageFont] = {}


def _font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    """Multi-platform font loader with a bundled Linux fallback.

    Tries Windows Arial first (dev), then a bundled DejaVuSans copy in
    ``assets/`` (Railway python:3.11-slim has no fonts pre-installed),
    then DejaVu paths on Debian-derived hosts. Falls back to PIL's
    bitmap default if nothing else loads.
    """
    cache_key = (size, bold)
    if cache_key in _FONT_CACHE:
        return _FONT_CACHE[cache_key]
    bundled = (
        Path(__file__).resolve().parent.parent
        / "assets"
        / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")
    )
    candidates: Iterable[str] = (
        "C:/Windows/Fonts/" + ("arialbd.ttf" if bold else "arial.ttf"),
        str(bundled),
        "/usr/share/fonts/truetype/dejavu/" + (
            "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
        ),
    )
    for path in candidates:
        try:
            font = ImageFont.truetype(path, size=size)
        except (OSError, ValueError):
            continue
        _FONT_CACHE[cache_key] = font
        return font
    fallback = ImageFont.load_default()
    _FONT_CACHE[cache_key] = fallback
    return fallback
