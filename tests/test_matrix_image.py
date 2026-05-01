"""Smoke tests for the team bucket-matrix image renderer."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image

from analysis import aggregator, matrix_image
from analysis.types import AssumptionProfile
from db import repo


def _seed_form(
    conn, *, name: str, weapon: str, element: str | None = None,
    skills: list[dict] | None = None,
):
    cid = repo.upsert_character(conn, name, base_role="warrior", base_weapon=weapon.lower())
    fid = repo.insert_form(
        conn, character_id=cid, display_name=name,
        rarity="5*", variant_kind="base", server="global",
    )
    affs = [("weapon", weapon, None)]
    if element:
        affs.append(("element", element, None))
    repo.insert_affinities(conn, fid, affs)
    if skills:
        repo.insert_skills(conn, fid, skills)
    return fid


def test_render_produces_valid_png(tmp_db_path: Path) -> None:
    """A minimal seeded team renders to a valid RGBA PNG."""
    conn = repo.connect(tmp_db_path)
    try:
        fid = _seed_form(
            conn, name="Hero", weapon="Sword",
            skills=[{
                "slot_order": 1, "name": "Slash", "kind": "active",
                "power_min": 80, "power_max": 80, "hits": 5,
                "description": "5x AoE Sword (5x 80 Power)",
            }],
        )
        bucketed = aggregator.aggregate_team(
            conn, frontrow_form_ids=[fid],
            profile=AssumptionProfile(boost_level=3),
        )
    finally:
        conn.close()

    rendered = matrix_image.render(bucketed)
    assert rendered.filename == "team_matrix.png"
    image = Image.open(BytesIO(rendered.data))
    assert image.format == "PNG"
    assert image.mode == "RGBA"
    # Width is set by the physical matrix (8 weapon columns).
    expected_w = (
        2 * matrix_image.PAD_X
        + matrix_image.LABEL_WIDTH
        + 8 * matrix_image.CELL_WIDTH
    )
    assert image.width == expected_w
    # Height varies with how many rows survive the all-zero filter, but
    # should always include the title strip + header + final mult row
    # for both matrices, plus the gap.
    min_h = (
        2 * matrix_image.PAD_Y
        + 2 * (matrix_image.TITLE_HEIGHT + matrix_image.HEADER_HEIGHT
               + matrix_image.CELL_HEIGHT + 2 * matrix_image.FINAL_ROW_EXTRA)
        + matrix_image.GAP_BETWEEN_MATRICES
    )
    assert image.height >= min_h
    # Many distinct colors — text + icons + crit/strikethrough markers.
    colors = image.convert("RGBA").getcolors(maxcolors=50_000)
    assert colors is not None
    assert len(colors) > 50


def test_render_overcap_cell_has_red_pixels(tmp_db_path: Path) -> None:
    """Sub-bucket sums above 30% must render with a red strikethrough."""
    conn = repo.connect(tmp_db_path)
    try:
        # Two passives stacking 25% + 25% = 50% Sword DMG Up — over the
        # 30% sub-bucket cap.
        fid = _seed_form(
            conn, name="Hero", weapon="Sword",
            skills=[
                {
                    "slot_order": 1, "name": "Slash", "kind": "active",
                    "power_min": 80, "power_max": 80, "hits": 5,
                    "description": "5x AoE Sword (5x 80 Power)",
                },
                {
                    "slot_order": 2, "name": "Stance A", "kind": "passive",
                    "description": "Self 25% Sword Damage Up",
                },
                {
                    "slot_order": 3, "name": "Stance B", "kind": "passive",
                    "description": "Self 25% Sword Damage Up",
                },
            ],
        )
        bucketed = aggregator.aggregate_team(
            conn, frontrow_form_ids=[fid],
            profile=AssumptionProfile(boost_level=3),
        )
    finally:
        conn.close()

    # Verify the underlying sum is over cap before checking pixels.
    assert bucketed.raw_sub_bucket_sums.get("g2.passive.sword_dmg_up", 0) > 0.30

    rendered = matrix_image.render(bucketed)
    image = Image.open(BytesIO(rendered.data)).convert("RGBA")
    # Scan for pixels in the OVERCAP_COLOR family (the red strikethrough).
    target = matrix_image.OVERCAP_COLOR
    matches = 0
    for r, g, b, a in image.getdata():
        if abs(r - target[0]) < 30 and abs(g - target[1]) < 30 and abs(b - target[2]) < 30:
            matches += 1
            if matches > 10:
                break
    assert matches > 10, "expected over-cap strikethrough pixels"


def test_render_overcap_cell_shows_capped_value_in_green(tmp_db_path: Path) -> None:
    """Over-cap cells render the effective ``→ 30%`` in CAPPED_COLOR
    so the reader sees both the wasted raw sum AND the contribution
    that actually feeds the multiplier."""
    conn = repo.connect(tmp_db_path)
    try:
        fid = _seed_form(
            conn, name="Hero", weapon="Sword",
            skills=[
                {
                    "slot_order": 1, "name": "Slash", "kind": "active",
                    "power_min": 80, "power_max": 80, "hits": 5,
                    "description": "5x AoE Sword (5x 80 Power)",
                },
                {
                    "slot_order": 2, "name": "Stance A", "kind": "passive",
                    "description": "Self 25% Sword Damage Up",
                },
                {
                    "slot_order": 3, "name": "Stance B", "kind": "passive",
                    "description": "Self 25% Sword Damage Up",
                },
            ],
        )
        bucketed = aggregator.aggregate_team(
            conn, frontrow_form_ids=[fid],
            profile=AssumptionProfile(boost_level=3),
        )
    finally:
        conn.close()

    rendered = matrix_image.render(bucketed)
    image = Image.open(BytesIO(rendered.data)).convert("RGBA")
    target = matrix_image.CAPPED_COLOR
    matches = 0
    for r, g, b, a in image.getdata():
        if abs(r - target[0]) < 30 and abs(g - target[1]) < 30 and abs(b - target[2]) < 30:
            matches += 1
            if matches > 5:
                break
    assert matches > 5, "expected green capped-value pixels in over-cap cell"


def test_render_crit_column_differs_from_baseline(tmp_db_path: Path) -> None:
    """A team with Self Guaranteed Crit on the DPS produces a different
    final-multiplier row than the same team without it."""
    def _build(with_crit: bool):
        conn = repo.connect(tmp_db_path)
        try:
            # Reset DB between renders.
            for table in ("character_forms", "characters", "skills",
                          "character_affinities"):
                conn.execute(f"DELETE FROM {table}")
            conn.commit()
            skills = [{
                "slot_order": 1, "name": "Slash", "kind": "active",
                "power_min": 80, "power_max": 80, "hits": 5,
                "description": "5x AoE Sword (5x 80 Power)",
            }]
            if with_crit:
                skills.append({
                    "slot_order": 2, "name": "Sword Mastery", "kind": "passive",
                    "description": "Self Guaranteed Crit while in frontrow",
                })
            fid = _seed_form(conn, name="Hero", weapon="Sword", skills=skills)
            bucketed = aggregator.aggregate_team(
                conn, frontrow_form_ids=[fid],
                profile=AssumptionProfile(boost_level=3),
            )
        finally:
            conn.close()
        return matrix_image.render(bucketed).data

    no_crit = _build(False)
    with_crit = _build(True)
    assert no_crit != with_crit, (
        "rendered PNGs should differ when guaranteed crit is added"
    )


def test_build_rows_appends_guaranteed_crit_row_for_matching_columns() -> None:
    """The matrix builder appends one ``Guaranteed Crit`` marker row
    when ``bucketed.crit_types`` overlaps the column set, with 1.0
    in the crit column(s) and 0.0 elsewhere."""
    from analysis.types import AssumptionProfile, BucketedTeam
    from damage.types import ELEMENTS, WEAPONS

    bucketed = BucketedTeam(
        frontrow_form_ids=(1,), backrow_form_ids=(), pet_id=None,
        divine_beast=False, cap_orbs=0,
        raw_sub_bucket_sums={}, team_damage_cap_up=0.0,
        team_skill_potency_up=0.0, team_soul_potency_up=0.0,
        classified=(), unparsed=(),
        profile=AssumptionProfile(),
        crit_types=frozenset({"sword"}),
    )

    physical_rows = matrix_image._build_rows(
        bucketed, columns=WEAPONS, stat_up="atk_up", stat_down="def_down",
    )
    crit_rows = [r for r in physical_rows if r.label == matrix_image.GUARANTEED_CRIT_LABEL]
    assert len(crit_rows) == 1
    assert crit_rows[0].values == tuple(
        1.0 if w == "sword" else 0.0 for w in WEAPONS
    )

    elemental_rows = matrix_image._build_rows(
        bucketed, columns=ELEMENTS, stat_up="mag_up", stat_down="mdef_down",
    )
    # No element matches a sword crit — the row must NOT be appended.
    assert not any(
        r.label == matrix_image.GUARANTEED_CRIT_LABEL for r in elemental_rows
    )


def test_build_rows_omits_crit_row_when_crit_types_empty() -> None:
    """No ``Guaranteed Crit`` row when no team member has guaranteed crit."""
    from analysis.types import AssumptionProfile, BucketedTeam
    from damage.types import WEAPONS

    bucketed = BucketedTeam(
        frontrow_form_ids=(1,), backrow_form_ids=(), pet_id=None,
        divine_beast=False, cap_orbs=0,
        raw_sub_bucket_sums={}, team_damage_cap_up=0.0,
        team_skill_potency_up=0.0, team_soul_potency_up=0.0,
        classified=(), unparsed=(),
        profile=AssumptionProfile(),
        crit_types=frozenset(),
    )

    rows = matrix_image._build_rows(
        bucketed, columns=WEAPONS, stat_up="atk_up", stat_down="def_down",
    )
    assert not any(
        r.label == matrix_image.GUARANTEED_CRIT_LABEL for r in rows
    )


def test_render_empty_team_returns_minimal_image(tmp_db_path: Path) -> None:
    """A team with no buffs still renders a valid PNG (header + footer)."""
    conn = repo.connect(tmp_db_path)
    try:
        fid = _seed_form(conn, name="Hero", weapon="Sword")
        bucketed = aggregator.aggregate_team(
            conn, frontrow_form_ids=[fid],
            profile=AssumptionProfile(boost_level=3),
        )
    finally:
        conn.close()

    rendered = matrix_image.render(bucketed)
    image = Image.open(BytesIO(rendered.data))
    assert image.format == "PNG"
    assert image.mode == "RGBA"
