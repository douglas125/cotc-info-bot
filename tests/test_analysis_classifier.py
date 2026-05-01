"""Contract tests for the broad team-analysis classifier."""
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


def test_buff_description_yields_classified_effects():
    skill = _skill(
        sid=99, kind="active",
        description="Frontrow 20% Atk Up + 15% Sword DMG Up for 3 turns",
    )
    out = classifier.classify_skill(skill, form_id=42)
    assert {(e.category, e.targets, e.magnitude) for e in out} == {
        ("stat_up", ("atk",), 0.20),
        ("dmg_up", ("sword",), 0.15),
    }
    assert {e.target_scope for e in out} == {"frontrow"}
    assert all(e.source_form_id == 42 for e in out)
    assert all(e.source_skill_id == 99 for e in out)
    assert all(e.source_kind == "active" for e in out)


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
    assert out[0].category == "damage_cap_up"
    assert out[0].magnitude == 100_000.0


def test_attack_only_description_is_ignored_not_unparsed():
    skill = _skill(
        sid=100,
        kind="active",
        description="3x AoE Axe, also hits Lightning weakness (3x 80 Power)",
    )
    assert classifier.classify_skill(skill, form_id=42) == []


def test_unparsed_effect_carries_raw_description():
    desc = "Some unrecognised wording here"
    out = classifier.classify_skill(
        _skill(sid=1, kind="active", description=desc), form_id=1,
    )
    assert out[0].raw_description == desc
