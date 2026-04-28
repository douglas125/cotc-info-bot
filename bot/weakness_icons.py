"""Shared weakness icon loading for generated enemy panels."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

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
