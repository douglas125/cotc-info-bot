"""Survivability tier rules — synthetic effects yield the right verdict."""
from __future__ import annotations

from pathlib import Path

import pytest

from analysis import survivability
from analysis.types import (
    AssumptionProfile,
    BucketedTeam,
    ClassifiedEffect,
)
from db import repo


def _eff(**overrides) -> ClassifiedEffect:
    base = dict(
        source_form_id=1, source_skill_id=1, source_kind="active",
        category="regen", targets=(), direction="n/a",
        magnitude=150.0, duration_turns=2, condition=None,
        boost_required=None, target_scope="all_allies",
        raw_description="All Allies Regen (150 Regen Strength)",
        confidence="high",
    )
    base.update(overrides)
    return ClassifiedEffect(**base)


def _bucketed(effects: list[ClassifiedEffect]) -> BucketedTeam:
    return BucketedTeam(
        frontrow_form_ids=tuple({e.source_form_id for e in effects} or {1}),
        backrow_form_ids=(),
        pet_id=None, divine_beast=False, cap_orbs=0,
        raw_sub_bucket_sums={}, team_damage_cap_up=0.0,
        team_skill_potency_up=0.0, team_soul_potency_up=0.0,
        classified=tuple(effects), unparsed=(),
        profile=AssumptionProfile(),
    )


@pytest.fixture()
def conn(tmp_db_path: Path):
    c = repo.connect(tmp_db_path)
    cid = repo.upsert_character(c, "Tester", base_role="warrior", base_weapon="sword")
    fid = repo.insert_form(
        c, character_id=cid, display_name="Tester",
        rarity="5*", variant_kind="base", server="global",
    )
    # Patch source_form_id on later effects to this real fid for citation lookup.
    yield c, fid
    c.close()


def test_no_effects_yields_none(conn):
    c, _ = conn
    bt = _bucketed([])
    v = survivability.assess(bt, c)
    assert v.tier == "None"
    assert v.citations == ()


def test_undying_beats_regen(conn):
    c, fid = conn
    bt = _bucketed([
        _eff(source_form_id=fid, category="undying", target_scope="self",
             raw_description="Undying"),
        _eff(source_form_id=fid, category="regen", target_scope="all_allies"),
    ])
    v = survivability.assess(bt, c)
    assert v.tier == "Undying"
    assert v.primary_source_display == "Tester"
    assert any("Undying" in cite.snippet for cite in v.citations)


def test_full_party_regen_when_all_allies_scope(conn):
    c, fid = conn
    bt = _bucketed([_eff(source_form_id=fid, target_scope="all_allies")])
    v = survivability.assess(bt, c)
    assert v.tier == "Full-party regen"


def test_full_party_regen_when_other_allies_scope(conn):
    c, fid = conn
    bt = _bucketed([_eff(source_form_id=fid, target_scope="other_allies")])
    assert survivability.assess(bt, c).tier == "Full-party regen"


def test_frontrow_regen_when_no_full_party_regen(conn):
    c, fid = conn
    bt = _bucketed([_eff(source_form_id=fid, target_scope="frontrow")])
    assert survivability.assess(bt, c).tier == "Frontrow regen"


def test_heal_only_when_only_heal_effects(conn):
    c, fid = conn
    bt = _bucketed([
        _eff(source_form_id=fid, category="heal", target_scope="all_allies",
             raw_description="Heal All Allies"),
    ])
    assert survivability.assess(bt, c).tier == "Heal-only"


def test_unrelated_effects_dont_qualify_as_survivability(conn):
    c, fid = conn
    bt = _bucketed([
        _eff(source_form_id=fid, category="stat_up", targets=("atk",),
             direction="up", target_scope="all_allies", magnitude=0.20,
             raw_description="20% Atk Up"),
    ])
    assert survivability.assess(bt, c).tier == "None"
