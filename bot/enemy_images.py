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
SHIELD_SIZE = 20
SHIELD_COUNT_GAP = 4
SHIELD_W = 44
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
        (x + 10, y + 1),
        (x + 18, y + 4),
        (x + 18, y + 10),
        (x + 15, y + 15),
        (x + 10, y + 19),
        (x + 5, y + 15),
        (x + 2, y + 10),
        (x + 2, y + 4),
    ]
    draw.polygon(points, fill=SHIELD, outline=SHIELD_EDGE)
    draw.line([(x + 10, y + 3), (x + 10, y + 17)], fill=SHIELD_EDGE)


def _centered_text_y(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    top: int,
    height: int,
) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return top + (height - (bbox[3] - bbox[1])) // 2 - bbox[1]


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
        draw.text(
            (PAD_X + 8, _centered_text_y(draw, label, label_font, y, ROW_H)),
            label,
            font=label_font,
            fill=TEXT,
        )

        shield_x = PAD_X + LABEL_W + 8
        shield_y = y + (ROW_H - SHIELD_SIZE) // 2
        _draw_shield(draw, shield_x, shield_y)
        shield_count = shields_by_pos.get(position, "?")
        draw.text(
            (
                shield_x + SHIELD_SIZE + SHIELD_COUNT_GAP,
                _centered_text_y(draw, shield_count, count_font, y, ROW_H),
            ),
            shield_count,
            font=count_font,
            fill=MUTED,
        )

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
