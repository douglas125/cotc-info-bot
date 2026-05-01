"""Shared types and constants for the damage package."""

from __future__ import annotations

from dataclasses import dataclass


PHYSICAL = "physical"
ELEMENTAL = "elemental"

WEAPONS = ("sword", "dagger", "bow", "axe", "staff", "tome", "fan", "spear")
ELEMENTS = ("light", "dark", "wind", "ice", "fire", "lightning")
CORE_STATS = ("atk", "mag", "def", "mdef")

DEFAULT_SUB_BUCKET_CAP = 0.30
JP_OVERRIDE_CAP = 0.50


@dataclass(frozen=True)
class V11Inputs:
    """Inputs for the V1.1 spreadsheet calculator.

    Maps one-to-one to Public1's input cells. All buff fields are
    decimal fractions (``0.30`` for 30%). The spreadsheet does not
    auto-cap; this struct preserves that — feed already-capped values
    if you want spreadsheet parity.
    """

    damage_type: str
    base_patk: float
    base_eatk: float
    equip_patk: float
    equip_eatk: float
    enemy_pdef: float
    enemy_edef: float
    # G1 — Stats (additive within group)
    atk_skill_buff: float = 0.0
    atk_passive_buff: float = 0.0
    def_skill_debuff: float = 0.0
    def_passive_debuff: float = 0.0
    # G2 — DMG Up (additive within group; lumped per-type in V1.1)
    dmg_skill_buff: float = 0.0
    dmg_passive_buff: float = 0.0
    # G3 — Res Down (additive within group; lumped per-type in V1.1)
    res_skill_debuff: float = 0.0
    res_passive_debuff: float = 0.0
    # G4 — Ultimate (three sub-pools multiplying within group)
    atk_ult_buff: float = 0.0
    def_ult_debuff: float = 0.0
    dmg_ult_buff: float = 0.0
    res_ult_debuff: float = 0.0
    # G5 — Pets (two sub-pools multiplying within group; no Res Down)
    atk_pet_buff: float = 0.0
    def_pet_debuff: float = 0.0
    dmg_pet_buff: float = 0.0
