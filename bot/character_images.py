"""Render character section panels with inline weakness icons."""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import re

from PIL import Image, ImageDraw, ImageFont

from bot import weakness_icons


WIDTH = 680
MAX_HEIGHT = 6000
PAD_X = 16
PAD_Y = 14
SECTION_GAP = 14
LINE_GAP = 5
BLOCK_GAP = 8
ICON_TEXT_GAP = 3
INDENT = 18

BG = (43, 45, 49, 255)
SECTION_BG = (49, 51, 57, 255)
HEADER_BG = (56, 58, 64, 255)
TEXT = (242, 243, 245, 255)
MUTED = (181, 186, 193, 255)
ACCENT = (88, 166, 255, 255)


@dataclass(frozen=True)
class PanelSection:
    title: str
    lines: list[str]


@dataclass(frozen=True)
class RenderedCharacterImage:
    filename: str
    data: bytes


@dataclass(frozen=True)
class _DrawToken:
    kind: str
    value: str


def _font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def _atomic_tokens(text: str) -> list[_DrawToken]:
    out: list[_DrawToken] = []
    for token in weakness_icons.inline_tokens(text):
        if isinstance(token, weakness_icons.IconToken):
            out.append(_DrawToken("icon", token.label))
            continue
        for part in re.findall(r"\s+|\S+", token.text):
            out.append(_DrawToken("text", part))
    return out


def _token_width(draw: ImageDraw.ImageDraw, token: _DrawToken, font: ImageFont.ImageFont) -> int:
    if token.kind == "icon":
        return weakness_icons.ICON_SIZE
    return int(draw.textlength(token.value, font=font))


def _wrap_tokens(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_w: int,
    *,
    indent: int = 0,
) -> list[list[_DrawToken]]:
    lines: list[list[_DrawToken]] = [[]]
    line_w = 0
    cur_max_w = max_w

    for token in _atomic_tokens(text):
        if token.kind == "text" and token.value.isspace():
            if not lines[-1]:
                continue
            token = _DrawToken("text", " ")

        width = _token_width(draw, token, font)
        if lines[-1] and line_w + width > cur_max_w:
            while lines[-1] and lines[-1][-1].kind == "text" and lines[-1][-1].value.isspace():
                lines[-1].pop()
            lines.append([])
            line_w = 0
            cur_max_w = max(1, max_w - indent)
            if token.kind == "text" and token.value.isspace():
                continue

        lines[-1].append(token)
        line_w += width

    return [line for line in lines if line]


def _draw_wrapped_text(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    text: str,
    x: int,
    y: int,
    max_w: int,
    font: ImageFont.ImageFont,
    *,
    fill: tuple[int, int, int, int] = TEXT,
    indent: int = 0,
) -> int:
    font_size = getattr(font, "size", 15)
    line_h = max(font_size + 4, weakness_icons.ICON_SIZE + 2)
    for i, line in enumerate(_wrap_tokens(draw, text, font, max_w, indent=indent)):
        line_x = x + (indent if i else 0)
        cursor = line_x
        for token in line:
            if token.kind == "icon":
                icon = weakness_icons.load_icon(token.value)
                if icon is not None:
                    image.alpha_composite(icon, (cursor, y + (line_h - weakness_icons.ICON_SIZE) // 2))
                cursor += weakness_icons.ICON_SIZE + ICON_TEXT_GAP
            else:
                draw.text((cursor, y + 1), token.value, font=font, fill=fill)
                cursor += int(draw.textlength(token.value, font=font))
        y += line_h + LINE_GAP
    return y


def _measure_lines(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    font: ImageFont.ImageFont,
    max_w: int,
    *,
    indent: int = 0,
) -> int:
    font_size = getattr(font, "size", 15)
    line_h = max(font_size + 4, weakness_icons.ICON_SIZE + 2)
    total = 0
    for text in lines:
        wrapped = _wrap_tokens(draw, text, font, max_w, indent=indent)
        total += len(wrapped) * line_h + max(0, len(wrapped)) * LINE_GAP
    return total


def render_character_panel(
    *,
    filename: str,
    header_lines: list[str],
    sections: list[PanelSection],
) -> RenderedCharacterImage:
    title_font = _font(16, bold=True)
    body_font = _font(15)
    small_font = _font(13)
    scratch = Image.new("RGBA", (1, 1), BG)
    scratch_draw = ImageDraw.Draw(scratch)

    content_w = WIDTH - PAD_X * 2
    y = PAD_Y
    y += _measure_lines(scratch_draw, header_lines, body_font, content_w) + PAD_Y
    for section in sections:
        y += title_font.size + 10
        y += _measure_lines(scratch_draw, section.lines, body_font, content_w - PAD_X, indent=INDENT)
        y += SECTION_GAP
    height = min(MAX_HEIGHT, max(PAD_Y * 2 + 80, y + PAD_Y))

    image = Image.new("RGBA", (WIDTH, height), BG)
    draw = ImageDraw.Draw(image)
    y = PAD_Y

    header_bottom = y + _measure_lines(draw, header_lines, body_font, content_w) + PAD_Y
    draw.rounded_rectangle((PAD_X // 2, y - 4, WIDTH - PAD_X // 2, header_bottom), radius=5, fill=HEADER_BG)
    for line in header_lines:
        y = _draw_wrapped_text(image, draw, line, PAD_X, y, content_w, body_font, fill=TEXT)
    y = header_bottom + SECTION_GAP

    for section in sections:
        if y >= MAX_HEIGHT - PAD_Y:
            break
        section_top = y
        section_h = (
            title_font.size
            + 12
            + _measure_lines(draw, section.lines, body_font, content_w - PAD_X, indent=INDENT)
            + BLOCK_GAP
        )
        section_bottom = min(height - PAD_Y, section_top + section_h)
        draw.rounded_rectangle((PAD_X // 2, section_top, WIDTH - PAD_X // 2, section_bottom), radius=5, fill=SECTION_BG)
        draw.text((PAD_X, y + 7), section.title, font=title_font, fill=ACCENT)
        y += title_font.size + 14
        if section.lines:
            for line in section.lines:
                y = _draw_wrapped_text(
                    image,
                    draw,
                    line,
                    PAD_X + 8,
                    y,
                    content_w - PAD_X,
                    body_font,
                    fill=TEXT,
                    indent=INDENT,
                )
        else:
            y = _draw_wrapped_text(
                image,
                draw,
                "No data recorded.",
                PAD_X + 8,
                y,
                content_w - PAD_X,
                small_font,
                fill=MUTED,
            )
        y = section_bottom + SECTION_GAP

    used = min(height, max(PAD_Y * 2, y))
    image = image.crop((0, 0, WIDTH, used))
    out = BytesIO()
    image.save(out, format="PNG", optimize=True)
    return RenderedCharacterImage(filename=filename, data=out.getvalue())
