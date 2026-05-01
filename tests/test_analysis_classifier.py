"""Phase 1 contract tests for the classifier.

The pattern table is empty; every non-blank description must classify
as a single ``unparsed`` effect, blanks must classify as zero.
"""
from __future__ import annotations

from analysis import classifier


def _skill(*, sid: int, kind: str, description: str) -> dict:
    return {
        "id": sid, "kind": kind, "description": description,
        "name": None, "sp_cost": None, "learn_board": None,
        "tier_level": None, "initial_use": None, "cooldown": None,
        "power_min": None, "power_max": None, "hits": None,
        "max_uses": None, "unlock_condition": None,
    }


def test_blank_description_yields_no_effects():
    assert classifier.classify_skill(_skill(sid=1, kind="active", description=""), form_id=1) == []
    assert classifier.classify_skill(_skill(sid=1, kind="active", description="   "), form_id=1) == []


def test_non_blank_description_yields_single_unparsed_effect():
    skill = _skill(
        sid=99, kind="active",
        description="Frontrow 20% Atk Up + 15% Sword DMG Up for 3 turns",
    )
    out = classifier.classify_skill(skill, form_id=42)
    assert len(out) == 1
    eff = out[0]
    assert eff.confidence == "unparsed"
    assert eff.category == "unparsed"
    assert eff.source_form_id == 42
    assert eff.source_skill_id == 99
    assert eff.source_kind == "active"
    assert eff.magnitude == 0.0
    assert eff.targets == ()
    assert eff.direction == "n/a"


def test_passive_skill_keeps_passive_kind():
    skill = _skill(sid=10, kind="passive", description="While at Full HP, +15% Atk Up.")
    out = classifier.classify_skill(skill, form_id=5)
    assert out[0].source_kind == "passive"


def test_classify_equipment_uses_equipment_source_kind():
    eq = {
        "id": 7,
        "kind": None,
        "description": "+100k Damage Cap Up while equipped",
        "name": "Bargello A4",
    }
    out = classifier.classify_equipment(eq, form_id=11)
    assert len(out) == 1
    assert out[0].source_kind == "equipment"
    assert out[0].source_form_id == 11
    # skill_id is sentinel -1 for equipment-derived effects.
    assert out[0].source_skill_id == -1


def test_unparsed_effect_carries_raw_description():
    desc = "Some unrecognised wording here"
    out = classifier.classify_skill(
        _skill(sid=1, kind="active", description=desc), form_id=1,
    )
    assert out[0].raw_description == desc
