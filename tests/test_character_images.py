from __future__ import annotations

from io import BytesIO

from PIL import Image

from bot import character_images, weakness_icons


def test_render_character_panel_outputs_valid_png_with_smaller_icons() -> None:
    rendered = character_images.render_character_panel(
        filename="character.png",
        header_lines=["Scholar - Tome - 5 star", "SEA only"],
        sections=[
            character_images.PanelSection(
                "Active",
                [
                    "- 36 SP - 2x single-target tome, also hits fire weakness",
                    "- 50 SP - 5x AoE Polearm/Lightning",
                ],
            ),
            character_images.PanelSection("Info", ["Weapon: Tome", "Weakness: Fire, Ice"]),
        ],
    )

    assert rendered.filename == "character.png"
    image = Image.open(BytesIO(rendered.data))
    assert image.format == "PNG"
    assert image.width == character_images.WIDTH
    assert image.height > 100
    assert weakness_icons.ICON_SIZE == 20
    colors = image.convert("RGBA").getcolors(maxcolors=100_000)
    assert colors is not None
    assert len(colors) > 20
