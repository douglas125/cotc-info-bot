"""Shared weakness icon loading and inline text tokenization."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import re
from typing import Iterator

from PIL import Image


ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
ICON_SIZE = 20

ICON_FILES: dict[str, str] = {
    "Sword": "weakness_sword.png",
    "Spear": "weakness_spear.png",
    "Polearm": "weakness_spear.png",
    "Dagger": "weakness_dagger.png",
    "Axe": "weakness_axe.png",
    "Bow": "weakness_bow.png",
    "Tome": "weakness_tome.png",
    "Staff": "weakness_staff.png",
    "Fan": "weakness_fan.png",
    "Fire": "weakness_fire.png",
    "Ice": "weakness_ice.png",
    "Lightning": "weakness_lightning.png",
    "Wind": "weakness_wind.png",
    "Light": "weakness_light.png",
    "Dark": "weakness_dark.png",
}

_CANONICAL_BY_LOWER = {label.lower(): label for label in ICON_FILES}
_LABEL_PATTERN = re.compile(
    r"(?<![A-Za-z])("
    + "|".join(re.escape(label) for label in sorted(ICON_FILES, key=len, reverse=True))
    + r")(?![A-Za-z])",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class IconToken:
    label: str


@dataclass(frozen=True)
class TextToken:
    text: str


InlineToken = IconToken | TextToken


def canonical_label(label: str) -> str | None:
    return _CANONICAL_BY_LOWER.get((label or "").strip().lower())


def has_icon(label: str) -> bool:
    return canonical_label(label) is not None


@lru_cache(maxsize=128)
def _load_icon_cached(label: str, size: int) -> Image.Image | None:
    canonical = canonical_label(label)
    if canonical is None:
        return None
    path = ASSETS_DIR / ICON_FILES[canonical]
    if not path.exists():
        return None
    with Image.open(path) as image:
        return image.convert("RGBA").resize((size, size), Image.Resampling.NEAREST)


def load_icon(label: str, size: int = ICON_SIZE) -> Image.Image | None:
    icon = _load_icon_cached(label, size)
    return icon.copy() if icon is not None else None


def inline_tokens(text: str) -> Iterator[InlineToken]:
    """Yield text/icon tokens for whole weakness labels in a string."""
    pos = 0
    for match in _LABEL_PATTERN.finditer(text or ""):
        if match.start() > pos:
            yield TextToken(text[pos:match.start()])
        canonical = canonical_label(match.group(1))
        if canonical is None:
            yield TextToken(match.group(1))
        else:
            yield IconToken(canonical)
        pos = match.end()
    if pos < len(text or ""):
        yield TextToken((text or "")[pos:])
