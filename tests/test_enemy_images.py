"""Smoke tests for generated enemy weakness image panels."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image

from bot import enemy_images


def test_all_weakness_png_assets_exist() -> None:
    for filename in set(enemy_images.ICON_FILES.values()):
        assert (Path("assets") / filename).exists()


def test_render_weakness_panel_outputs_valid_png() -> None:
    assert enemy_images.ICON_SIZE == 20
    assert enemy_images.SHIELD_SIZE == enemy_images.ICON_SIZE
    assert enemy_images.SHIELD_W < 50
    rendered = enemy_images.render_weakness_panel(
        filename="weaknesses.png",
        stats_rows=[
            {"position": 0, "member_name": "Leader Lloris", "stat_name": "Shields", "stat_value": "30"},
            {"position": 1, "member_name": "Mini Lloris", "stat_name": "Shields", "stat_value": "18"},
        ],
        weakness_rows=[
            {"position": 0, "weakness_label": "Axe"},
            {"position": 0, "weakness_label": "Bow"},
            {"position": 0, "weakness_label": "Ice"},
            {"position": 0, "weakness_label": "Wind"},
            {"position": 0, "weakness_label": "Dark"},
            {"position": 1, "weakness_label": "Dagger"},
            {"position": 1, "weakness_label": "Bow"},
            {"position": 1, "weakness_label": "Ice"},
            {"position": 1, "weakness_label": "Lightning"},
            {"position": 1, "weakness_label": "Dark"},
        ],
    )

    assert rendered is not None
    assert rendered.filename == "weaknesses.png"
    image = Image.open(BytesIO(rendered.data))
    assert image.format == "PNG"
    assert image.width == (
        enemy_images.PAD_X * 2
        + enemy_images.LABEL_W
        + enemy_images.SHIELD_W
        + 5 * enemy_images.ICON_SIZE
        + 4 * enemy_images.ICON_GAP
    )
    assert image.height == (
        enemy_images.PAD_Y * 2
        + 2 * enemy_images.ROW_H
        + enemy_images.ROW_GAP
    )
    colors = image.convert("RGBA").getcolors(maxcolors=10_000)
    assert colors is not None
    assert len(colors) > 20


def test_render_weakness_panel_returns_none_without_icons() -> None:
    assert enemy_images.render_weakness_panel(
        filename="weaknesses.png",
        stats_rows=[
            {"position": 0, "member_name": "NPC", "stat_name": "Shields", "stat_value": "10"},
        ],
        weakness_rows=[],
    ) is None
