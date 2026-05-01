"""Tests for the damage calculator port.

Three groups:

A. Modified Base Stat parity — direct formula checks.
B. End-to-end parity vs the V1.1 .xlsx fixture — load the workbook
   directly via stdlib ``zipfile``, walk Public1's input cells, run
   them through :mod:`damage.spreadsheet_calc`, assert against the
   cached ``<v>`` of the formula cells.
C. Full model invariants — caps, umbrella expansion, the example
   damage walk from ``buff_debuff/examples.md``, defender division,
   and the reduction of :mod:`damage.full_calc` to
   :mod:`damage.spreadsheet_calc` on Public1's lumped inputs.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

import pytest

from damage import (
    DEFAULT_SUB_BUCKET_CAP,
    ELEMENTAL,
    PHYSICAL,
    V11Inputs,
    WEAPONS,
    damage_difference,
    effective_value,
    full_calc,
    modified_base_stat,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
V11_XLSX = REPO_ROOT / "buff_debuff" / "COTC Effective Damage Calculator V1.1.xlsx"


# --------------------------------------------------------------------- A.


class TestModifiedBaseStat:
    def test_physical_patk_dominant_returns_patk(self) -> None:
        assert modified_base_stat(PHYSICAL, 500.0, 400.0) == 500.0

    def test_physical_eatk_dominant_returns_blend(self) -> None:
        assert modified_base_stat(PHYSICAL, 400.0, 500.0) == pytest.approx(
            0.75 * 500.0 + 0.25 * 400.0
        )

    def test_elemental_eatk_dominant_returns_eatk(self) -> None:
        assert modified_base_stat(ELEMENTAL, 400.0, 500.0) == 500.0

    def test_elemental_patk_dominant_returns_blend(self) -> None:
        assert modified_base_stat(ELEMENTAL, 500.0, 400.0) == pytest.approx(
            0.75 * 500.0 + 0.25 * 400.0
        )

    def test_v11_calc1_cached_value(self) -> None:
        # Public1 Calculator 1: Elemental, PATK=493, EATK=400 → 469.75
        assert modified_base_stat(ELEMENTAL, 493.0, 400.0) == pytest.approx(469.75)

    def test_v11_calc2_cached_value(self) -> None:
        # Public1 Calculator 2: Elemental, PATK=516, EATK=337 → 471.25
        assert modified_base_stat(ELEMENTAL, 516.0, 337.0) == pytest.approx(471.25)

    def test_damage_type_is_case_insensitive(self) -> None:
        assert modified_base_stat("Physical", 500.0, 400.0) == modified_base_stat(
            "physical", 500.0, 400.0
        )
        assert modified_base_stat("ELEMENTAL", 400.0, 500.0) == modified_base_stat(
            "elemental", 400.0, 500.0
        )

    def test_unknown_damage_type_raises(self) -> None:
        with pytest.raises(ValueError):
            modified_base_stat("magical", 100.0, 100.0)


# --------------------------------------------------------------------- B.


# Public1 cell mapping for both calculators. Columns C and F hold the
# two side-by-side calculators; ``C6`` (damage type) and ``C7`` /
# ``C8`` (enemy defenses) are shared.
_PUBLIC1_INPUT_CELLS: dict[str, dict[str, str]] = {
    "C": {
        "damage_type": "C6",
        "enemy_pdef": "C7",
        "enemy_edef": "C8",
        "base_patk": "C12",
        "base_eatk": "C13",
        "equip_patk": "C18",
        "equip_eatk": "C19",
        "atk_skill_buff": "C22",
        "atk_passive_buff": "C23",
        "def_skill_debuff": "C24",
        "def_passive_debuff": "C25",
        "dmg_skill_buff": "C26",
        "dmg_passive_buff": "C27",
        "res_skill_debuff": "C28",
        "res_passive_debuff": "C29",
        "atk_ult_buff": "C30",
        "def_ult_debuff": "C31",
        "dmg_ult_buff": "C32",
        "res_ult_debuff": "C33",
        "atk_pet_buff": "C34",
        "def_pet_debuff": "C35",
        "dmg_pet_buff": "C36",
    },
    "F": {
        "damage_type": "C6",
        "enemy_pdef": "C7",
        "enemy_edef": "C8",
        "base_patk": "F12",
        "base_eatk": "F13",
        "equip_patk": "F18",
        "equip_eatk": "F19",
        "atk_skill_buff": "F22",
        "atk_passive_buff": "F23",
        "def_skill_debuff": "F24",
        "def_passive_debuff": "F25",
        "dmg_skill_buff": "F26",
        "dmg_passive_buff": "F27",
        "res_skill_debuff": "F28",
        "res_passive_debuff": "F29",
        "atk_ult_buff": "F30",
        "def_ult_debuff": "F31",
        "dmg_ult_buff": "F32",
        "res_ult_debuff": "F33",
        "atk_pet_buff": "F34",
        "def_pet_debuff": "F35",
        "dmg_pet_buff": "F36",
    },
}


_CELL_RE = re.compile(
    r'<c r="([^"]+)"(?:[^>]*?\bt="([^"]+)")?[^>]*>'
    r"(?:<f[^>]*>.*?</f>)?"
    r"(?:<v>([^<]*)</v>)?"
    r"</c>",
    re.DOTALL,
)
_SI_RE = re.compile(r"<si>(.*?)</si>", re.DOTALL)
_T_RE = re.compile(r"<t[^>]*>(.*?)</t>", re.DOTALL)


def _load_public1_cells() -> dict[str, object]:
    """Return Public1's cell values keyed by address.

    String cells (``t="s"``) are dereferenced through
    ``xl/sharedStrings.xml``. Numeric cells are returned as floats.
    Missing/empty cells are absent from the dict; callers default
    them to ``0.0``.
    """
    with zipfile.ZipFile(V11_XLSX) as z:
        sheet_xml = z.read("xl/worksheets/sheet2.xml").decode("utf-8")
        sst_xml = z.read("xl/sharedStrings.xml").decode("utf-8")

    sst_strings: list[str] = []
    for raw in _SI_RE.findall(sst_xml):
        sst_strings.append("".join(_T_RE.findall(raw)))

    cells: dict[str, object] = {}
    for match in _CELL_RE.finditer(sheet_xml):
        addr, ctype, value = match.group(1), match.group(2), match.group(3)
        if value is None:
            continue
        if ctype == "s":
            cells[addr] = sst_strings[int(value)]
        else:
            try:
                cells[addr] = float(value)
            except ValueError:
                cells[addr] = value
    return cells


def _build_inputs(calc: str, cells: dict[str, object]) -> V11Inputs:
    mapping = _PUBLIC1_INPUT_CELLS[calc]
    kwargs: dict[str, object] = {}
    for field_name, cell_addr in mapping.items():
        value = cells.get(cell_addr, 0.0)
        if value is None or value == "":
            value = 0.0 if field_name != "damage_type" else "physical"
        kwargs[field_name] = value
    return V11Inputs(**kwargs)  # type: ignore[arg-type]


@pytest.fixture(scope="module")
def public1_cells() -> dict[str, object]:
    if not V11_XLSX.exists():
        pytest.skip(f"V1.1 xlsx fixture missing at {V11_XLSX}")
    return _load_public1_cells()


class TestV11Parity:
    """Effective Value parity against the .xlsx fixture's cached cells."""

    def test_modified_base_stat_calc1(self, public1_cells: dict[str, object]) -> None:
        inputs = _build_inputs("C", public1_cells)
        m = modified_base_stat(
            inputs.damage_type, inputs.base_patk, inputs.base_eatk
        )
        assert m == pytest.approx(public1_cells["C15"], abs=1e-6)

    def test_modified_base_stat_calc2(self, public1_cells: dict[str, object]) -> None:
        inputs = _build_inputs("F", public1_cells)
        m = modified_base_stat(
            inputs.damage_type, inputs.base_patk, inputs.base_eatk
        )
        assert m == pytest.approx(public1_cells["F15"], abs=1e-6)

    def test_effective_value_calc1(self, public1_cells: dict[str, object]) -> None:
        inputs = _build_inputs("C", public1_cells)
        ev = effective_value(inputs)
        assert ev == pytest.approx(public1_cells["C38"], abs=1e-3)

    def test_effective_value_calc2(self, public1_cells: dict[str, object]) -> None:
        inputs = _build_inputs("F", public1_cells)
        ev = effective_value(inputs)
        assert ev == pytest.approx(public1_cells["F38"], abs=1e-3)

    def test_damage_difference_both_directions(
        self, public1_cells: dict[str, object]
    ) -> None:
        ev1 = effective_value(_build_inputs("C", public1_cells))
        ev2 = effective_value(_build_inputs("F", public1_cells))
        assert damage_difference(ev1, ev2) == pytest.approx(
            public1_cells["C39"], abs=1e-9
        )
        assert damage_difference(ev2, ev1) == pytest.approx(
            public1_cells["F39"], abs=1e-9
        )


# --------------------------------------------------------------------- C.


def _public1_to_full_groups(
    inputs: V11Inputs,
) -> tuple[float, float, float, float, float, float]:
    """Compute ``(base_term, g1, g2, g3, g4, g5)`` from V1.1 inputs.

    Each V1.1 buff field becomes one sub-bucket; capping is disabled
    (``default_cap=inf``) so the result equals the spreadsheet's
    additive sums regardless of whether inputs exceed 30%.
    """
    m = modified_base_stat(inputs.damage_type, inputs.base_patk, inputs.base_eatk)
    if inputs.damage_type.strip().lower() == PHYSICAL:
        atk, defense = inputs.equip_patk, inputs.enemy_pdef
    else:
        atk, defense = inputs.equip_eatk, inputs.enemy_edef
    base_term = m * (m + atk - defense)

    g1 = full_calc.additive_group(
        {
            "g1.active.atk_up": inputs.atk_skill_buff,
            "g1.passive.atk_up": inputs.atk_passive_buff,
            "g1.active.def_down": inputs.def_skill_debuff,
            "g1.passive.def_down": inputs.def_passive_debuff,
        },
        default_cap=float("inf"),
    )
    g2 = full_calc.additive_group(
        {
            "g2.active.dmg_up": inputs.dmg_skill_buff,
            "g2.passive.dmg_up": inputs.dmg_passive_buff,
        },
        default_cap=float("inf"),
    )
    g3 = full_calc.additive_group(
        {
            "g3.active.res_down": inputs.res_skill_debuff,
            "g3.passive.res_down": inputs.res_passive_debuff,
        },
        default_cap=float("inf"),
    )
    g4 = full_calc.multiplicative_group(
        stats_sums={
            "g4.stats.atk_up": inputs.atk_ult_buff,
            "g4.stats.def_down": inputs.def_ult_debuff,
        },
        dmg_up_sums={"g4.dmg.dmg_up": inputs.dmg_ult_buff},
        res_down_sums={"g4.res.res_down": inputs.res_ult_debuff},
        default_cap=float("inf"),
    )
    g5 = full_calc.multiplicative_group(
        stats_sums={
            "g5.stats.atk_up": inputs.atk_pet_buff,
            "g5.stats.def_down": inputs.def_pet_debuff,
        },
        dmg_up_sums={"g5.dmg.dmg_up": inputs.dmg_pet_buff},
        res_down_sums=None,
        default_cap=float("inf"),
    )
    return base_term, g1, g2, g3, g4, g5


class TestFullCalc:
    def test_reduces_to_spreadsheet_on_public1(
        self, public1_cells: dict[str, object]
    ) -> None:
        for calc in ("C", "F"):
            inputs = _build_inputs(calc, public1_cells)
            base_term, g1, g2, g3, g4, g5 = _public1_to_full_groups(inputs)
            full = full_calc.effective_damage(
                base_term, g1=g1, g2=g2, g3=g3, g4=g4, g5=g5
            )
            sheet = effective_value(inputs)
            assert full == pytest.approx(sheet, abs=1e-6)

    def test_subbucket_cap_30_default(self) -> None:
        # 40% in one sub-bucket caps to 30%
        g1 = full_calc.additive_group({"g1.active.atk_up": 0.40})
        assert g1 == pytest.approx(1.0 + DEFAULT_SUB_BUCKET_CAP)

    def test_subbucket_cap_50_jp_override(self) -> None:
        # JP cap-raise: the per-key override beats the default 30%
        caps = {"g1.active.atk_up": 0.50}
        # 40% < 50% override: passes through
        assert full_calc.additive_group(
            {"g1.active.atk_up": 0.40}, caps
        ) == pytest.approx(1.40)
        # 60% > 50% override: caps to 50%
        assert full_calc.additive_group(
            {"g1.active.atk_up": 0.60}, caps
        ) == pytest.approx(1.50)

    def test_two_capped_subbuckets_sum_independently(self) -> None:
        # Each sub-bucket caps independently, then the capped values sum
        sums = {"g1.active.atk_up": 0.40, "g1.passive.atk_up": 0.50}
        # 0.40 → 0.30 (default cap), 0.50 → 0.30
        assert full_calc.additive_group(sums) == pytest.approx(1.0 + 0.30 + 0.30)

    def test_umbrella_physical_spreads_over_eight_weapons(self) -> None:
        sums: dict[str, float] = {}
        full_calc.apply_umbrella(sums, "physical", 0.20, "g2.active.{type}_dmg_up")
        assert len(sums) == 8
        for w in WEAPONS:
            assert sums[f"g2.active.{w}_dmg_up"] == pytest.approx(0.20)

    def test_umbrella_elemental_spreads_over_six_elements(self) -> None:
        sums: dict[str, float] = {}
        full_calc.apply_umbrella(sums, "elemental", 0.15, "g2.active.{type}_dmg_up")
        assert len(sums) == 6
        for v in sums.values():
            assert v == pytest.approx(0.15)

    def test_umbrella_all_spreads_over_fourteen_types(self) -> None:
        sums: dict[str, float] = {}
        full_calc.apply_umbrella(sums, "all", 0.10, "g.{type}")
        assert len(sums) == 14

    def test_umbrella_unknown_kind_raises(self) -> None:
        with pytest.raises(ValueError):
            full_calc.apply_umbrella({}, "magical", 0.10, "x.{type}")

    def test_umbrella_caps_each_subbucket_independently(self) -> None:
        # 20% Physical DMG Up + 15% Sword DMG Up = 35% in Sword sub-bucket → caps to 30%
        # Other 7 weapon sub-buckets stay at 20% (uncapped at 30%)
        sums: dict[str, float] = {"g2.active.sword_dmg_up": 0.15}
        full_calc.apply_umbrella(sums, "physical", 0.20, "g2.active.{type}_dmg_up")
        assert sums["g2.active.sword_dmg_up"] == pytest.approx(0.35)
        assert sums["g2.active.bow_dmg_up"] == pytest.approx(0.20)
        # After capping, sword caps to 0.30, bow stays at 0.20
        g2 = full_calc.additive_group(sums)
        # 0.30 (sword) + 7 × 0.20 (other weapons) = 0.30 + 1.40 = 1.70
        assert g2 == pytest.approx(1.0 + 0.30 + 7 * 0.20)

    def test_examples_md_8_end_to_end_walk(self) -> None:
        """Reproduce ``buff_debuff/examples.md`` example 8 (≈ 15.6× base)."""
        g1 = full_calc.additive_group(
            {
                "g1.active.atk_up": 0.25,
                "g1.passive.atk_up": 0.15,
            }
        )
        g2 = full_calc.additive_group(
            {
                "g2.active.bow_dmg_up": 0.20,
                "g2.passive.bow_dmg_up": 0.10,
            }
        )
        g3 = full_calc.additive_group({"g3.active.bow_res_down": 0.25})
        g4 = full_calc.multiplicative_group(
            stats_sums={"g4.stats.atk_up": 0.30},
            dmg_up_sums={"g4.dmg.bow_dmg_up": 0.30},
            res_down_sums=None,
        )
        g5 = full_calc.multiplicative_group(
            stats_sums={"g5.stats.atk_up": 0.15},
            dmg_up_sums=None,
            res_down_sums=None,
        )
        g6 = full_calc.divine_beast_multiplier(True)
        crit = full_calc.crit_multiplier(True, 0.30)
        hhlw = full_calc.alignment_multiplier(0.50)
        soul = full_calc.soul_potency_multiplier(0.20)
        skill = full_calc.skill_potency_multiplier(0.15)

        damage = full_calc.effective_damage(
            base_term=1.0,
            g1=g1,
            g2=g2,
            g3=g3,
            g4=g4,
            g5=g5,
            g6=g6,
            crit=crit,
            hell_heaven_lw=hhlw,
            soul_potency=soul,
            skill_potency=skill,
        )

        assert g1 == pytest.approx(1.40)
        assert g2 == pytest.approx(1.30)
        assert g3 == pytest.approx(1.25)
        assert g4 == pytest.approx(1.30 * 1.30 * 1.0)
        assert g5 == pytest.approx(1.15)
        assert damage == pytest.approx(15.6, abs=0.1)

    def test_defender_g1_divides_attacker(self) -> None:
        attacker = full_calc.effective_damage(base_term=1000.0)
        defended = full_calc.effective_damage(base_term=1000.0, defender_g1=1.30)
        assert defended == pytest.approx(attacker / 1.30)

    def test_defender_full_product_divides(self) -> None:
        # Defender's groups all multiply, then divide attacker product
        damage = full_calc.effective_damage(
            base_term=1000.0,
            defender_g1=1.30,
            defender_g2=1.20,
            defender_g3=1.10,
            defender_g4=1.50,
            defender_g5=1.05,
        )
        expected = 1000.0 / (1.30 * 1.20 * 1.10 * 1.50 * 1.05)
        assert damage == pytest.approx(expected)

    def test_crit_inactive_returns_one(self) -> None:
        assert full_calc.crit_multiplier(False, 0.30) == 1.0

    def test_crit_active_uses_125_plus_damage_up(self) -> None:
        assert full_calc.crit_multiplier(True, 0.0) == pytest.approx(1.25)
        assert full_calc.crit_multiplier(True, 0.30) == pytest.approx(1.55)

    def test_divine_beast_off_is_one(self) -> None:
        assert full_calc.divine_beast_multiplier(False) == 1.0
        assert full_calc.divine_beast_multiplier(True) == pytest.approx(1.10)

    def test_additive_group_empty_returns_one(self) -> None:
        assert full_calc.additive_group(None) == 1.0
        assert full_calc.additive_group({}) == 1.0

    def test_multiplicative_group_all_empty_returns_one(self) -> None:
        assert full_calc.multiplicative_group() == 1.0
