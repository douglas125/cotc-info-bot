"""1:1 port of the V1.1 spreadsheet's calculator (Public1).

Cells referenced inline in docstrings come from
``buff_debuff/COTC Effective Damage Calculator V1.1.xlsx`` →
``xl/worksheets/sheet2.xml`` (Public1).
"""

from __future__ import annotations

from .types import ELEMENTAL, PHYSICAL, V11Inputs


def modified_base_stat(damage_type: str, base_patk: float, base_eatk: float) -> float:
    """Public1 cell ``C15``.

    Spreadsheet formula::

        if(C6="physical",
           if(C12 > C13, C12, 0.75*C13 + 0.25*C12),
           if(C13 > C12, C13, 0.75*C12 + 0.25*C13))

    Comparison on ``damage_type`` is normalized to lowercase so callers
    can pass ``"Physical"`` / ``"Elemental"`` without surprises.
    """
    dt = damage_type.strip().lower()
    if dt == PHYSICAL:
        if base_patk > base_eatk:
            return base_patk
        return 0.75 * base_eatk + 0.25 * base_patk
    if dt == ELEMENTAL:
        if base_eatk > base_patk:
            return base_eatk
        return 0.75 * base_patk + 0.25 * base_eatk
    raise ValueError(
        f"unknown damage_type: {damage_type!r} (expected 'physical' or 'elemental')"
    )


def effective_value(inputs: V11Inputs) -> float:
    """Public1 cells ``C38`` / ``F38``.

    The factor ``(M + ATK − DEF)`` can be negative when the enemy's
    defense exceeds attacker total ATK; the spreadsheet does not clamp
    at zero, and neither do we — this preserves comparison semantics
    in the negative regime.
    """
    m = modified_base_stat(inputs.damage_type, inputs.base_patk, inputs.base_eatk)
    if inputs.damage_type.strip().lower() == PHYSICAL:
        atk = inputs.equip_patk
        defense = inputs.enemy_pdef
    else:
        atk = inputs.equip_eatk
        defense = inputs.enemy_edef

    g1 = 1.0 + (
        inputs.atk_skill_buff
        + inputs.atk_passive_buff
        + inputs.def_skill_debuff
        + inputs.def_passive_debuff
    )
    g2 = 1.0 + (inputs.dmg_skill_buff + inputs.dmg_passive_buff)
    g3 = 1.0 + (inputs.res_skill_debuff + inputs.res_passive_debuff)
    g4 = (
        (1.0 + inputs.atk_ult_buff + inputs.def_ult_debuff)
        * (1.0 + inputs.dmg_ult_buff)
        * (1.0 + inputs.res_ult_debuff)
    )
    g5 = (1.0 + inputs.atk_pet_buff + inputs.def_pet_debuff) * (
        1.0 + inputs.dmg_pet_buff
    )

    return m * (m + atk - defense) * g1 * g2 * g3 * g4 * g5


def damage_difference(ev1: float, ev2: float) -> float:
    """Public1 cell ``C39``: ``(ev1 - ev2) / ev2``.

    Sign convention mirrors the sheet exactly. With both values
    negative (enemy DEF > attacker ATK), a positive return means
    ``ev1`` is *less negative* than ``ev2``.
    """
    return (ev1 - ev2) / ev2
