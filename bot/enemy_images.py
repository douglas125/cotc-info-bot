"""Render enemy weakness icon panels for Discord embeds."""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from bot import weakness_icons

ICON_SIZE = weakness_icons.ICON_SIZE
ICON_GAP = 4
ROW_GAP = 8
PAD_X = 12
PAD_Y = 10
LABEL_W = 132
SHIELD_W = 50
ROW_H = 30

BG = (43, 45, 49, 255)
ROW_BG = (56, 58, 64, 255)
TEXT = (242, 243, 245, 255)
MUTED = (181, 186, 193, 255)
SHIELD = (114, 137, 218, 255)
SHIELD_EDGE = (216, 222, 233, 255)

ICON_FILES = weakness_icons.ICON_FILES


@dataclass(frozen=True)
class RenderedEnemyImage:
    filename: str
    data: bytes


def _font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def _row_value(row: Any, key: str) -> Any:
    return row[key]


def _fit_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_w: int) -> str:
    if draw.textlength(text, font=font) <= max_w:
        return text
    ellipsis = "..."
    while text and draw.textlength(text + ellipsis, font=font) > max_w:
        text = text[:-1]
    return text + ellipsis if text else ellipsis


def _load_icon(label: str) -> Image.Image | None:
    return weakness_icons.load_icon(label, ICON_SIZE)


def _draw_shield(draw: ImageDraw.ImageDraw, x: int, y: int) -> None:
    points = [
        (x + 8, y + 2),
        (x + 20, y + 5),
        (x + 20, y + 14),
        (x + 14, y + 23),
        (x + 8, y + 27),
        (x + 2, y + 23),
        (x - 4, y + 14),
        (x - 4, y + 5),
    ]
    draw.polygon(points, fill=SHIELD, outline=SHIELD_EDGE)
    draw.line([(x + 8, y + 4), (x + 8, y + 25)], fill=SHIELD_EDGE)


def render_weakness_panel(
    *,
    filename: str,
    stats_rows: list[Any],
    weakness_rows: list[Any],
) -> RenderedEnemyImage | None:
    shields_by_pos: dict[int, str] = {}
    member_by_pos: dict[int, str] = {}
    for row in stats_rows:
        position = int(_row_value(row, "position"))
        if _row_value(row, "stat_name") == "Shields":
            shields_by_pos[position] = str(_row_value(row, "stat_value"))
        if position not in member_by_pos and _row_value(row, "member_name"):
            member_by_pos[position] = str(_row_value(row, "member_name"))

    weaknesses_by_pos: dict[int, list[str]] = {}
    for row in weakness_rows:
        position = int(_row_value(row, "position"))
        label = str(_row_value(row, "weakness_label"))
        if label in ICON_FILES:
            weaknesses_by_pos.setdefault(position, []).append(label)

    if not weaknesses_by_pos:
        return None

    positions = sorted(weaknesses_by_pos)
    max_icons = max(len(weaknesses_by_pos[p]) for p in positions)
    width = PAD_X * 2 + LABEL_W + SHIELD_W + max_icons * ICON_SIZE + max(0, max_icons - 1) * ICON_GAP
    height = PAD_Y * 2 + len(positions) * ROW_H + max(0, len(positions) - 1) * ROW_GAP

    image = Image.new("RGBA", (width, height), BG)
    draw = ImageDraw.Draw(image)
    label_font = _font(15, bold=True)
    count_font = _font(16, bold=True)

    y = PAD_Y
    for position in positions:
        draw.rounded_rectangle((PAD_X, y, width - PAD_X, y + ROW_H), radius=4, fill=ROW_BG)
        label = _fit_text(draw, member_by_pos.get(position, f"#{position + 1}"), label_font, LABEL_W - 8)
        draw.text((PAD_X + 8, y + 8), label, font=label_font, fill=TEXT)

        shield_x = PAD_X + LABEL_W + 8
        _draw_shield(draw, shield_x, y + 4)
        draw.text((shield_x + 25, y + 8), shields_by_pos.get(position, "?"), font=count_font, fill=MUTED)

        icon_x = PAD_X + LABEL_W + SHIELD_W
        for label in weaknesses_by_pos[position]:
            icon = _load_icon(label)
            if icon is not None:
                image.alpha_composite(icon, (icon_x, y + (ROW_H - ICON_SIZE) // 2))
            icon_x += ICON_SIZE + ICON_GAP
        y += ROW_H + ROW_GAP

    out = BytesIO()
    image.save(out, format="PNG", optimize=True)
    return RenderedEnemyImage(filename=filename, data=out.getvalue())
