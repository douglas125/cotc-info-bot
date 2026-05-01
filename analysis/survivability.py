"""Survivability tier assessment from a BucketedTeam.

Tier rules (highest priority wins):

  1. **Undying** — any active member has a ``category='undying'`` effect
     (Shana's mechanic tag). Output cites the responsible character.
  2. **Full-party regen** — any active member has a ``category='regen'``
     effect with ``target_scope`` in ``{'all_allies', 'other_allies'}``.
     "Other Allies" qualifies because the caster is the only ally
     excluded.
  3. **Frontrow regen** — any active member has ``category='regen'``
     with ``target_scope='frontrow'`` (no all-allies regen on the team).
  4. **Heal-only** — only ``category='heal'`` effects (one-shot heal,
     no regen ticks).
  5. **None** — no survivability effects classified.

Phase 1 ships with an empty pattern table so every team classifies as
``None``. Phase 2 patterns surface the upper tiers.
"""
from __future__ import annotations

import sqlite3
from typing import Iterable

from db import repo

from .types import (
    BucketedTeam,
    ClassifiedEffect,
    SurvivabilityCitation,
    SurvivabilityVerdict,
)


def assess(
    bucketed: BucketedTeam, conn: sqlite3.Connection,
) -> SurvivabilityVerdict:
    """Pick the highest tier matched on the active 4 and cite it."""
    effects = bucketed.classified

    by_tier = (
        ("Undying",          _filter_undying(effects)),
        ("Full-party regen", _filter_full_party_regen(effects)),
        ("Frontrow regen",   _filter_frontrow_regen(effects)),
        ("Heal-only",        _filter_heal_only(effects)),
    )
    for tier, hits in by_tier:
        if hits:
            return _verdict(tier, hits, conn)

    return SurvivabilityVerdict(
        tier="None", primary_source_display="—", citations=(),
    )


# ---------------------------------------------------------------------------
# Tier filters.
# ---------------------------------------------------------------------------

def _filter_undying(effects: Iterable[ClassifiedEffect]) -> list[ClassifiedEffect]:
    return [e for e in effects if e.category == "undying"]


def _filter_full_party_regen(effects: Iterable[ClassifiedEffect]) -> list[ClassifiedEffect]:
    return [
        e for e in effects
        if e.category == "regen"
        and e.target_scope in {"all_allies", "other_allies"}
    ]


def _filter_frontrow_regen(effects: Iterable[ClassifiedEffect]) -> list[ClassifiedEffect]:
    return [
        e for e in effects
        if e.category == "regen" and e.target_scope == "frontrow"
    ]


def _filter_heal_only(effects: Iterable[ClassifiedEffect]) -> list[ClassifiedEffect]:
    return [e for e in effects if e.category == "heal"]


# ---------------------------------------------------------------------------
# Citation rendering.
# ---------------------------------------------------------------------------

def _verdict(
    tier: str,
    hits: list[ClassifiedEffect],
    conn: sqlite3.Connection,
) -> SurvivabilityVerdict:
    by_form: dict[int, str] = {}
    citations: list[SurvivabilityCitation] = []
    for h in hits:
        if h.source_form_id not in by_form:
            row = repo.get_form(conn, h.source_form_id)
            display = row["display_name"] if row else f"form#{h.source_form_id}"
            by_form[h.source_form_id] = display
        citations.append(
            SurvivabilityCitation(
                form_id=h.source_form_id,
                skill_id=h.source_skill_id,
                snippet=_short(h.raw_description),
            )
        )
    primary = next(iter(by_form.values())) if by_form else "—"
    return SurvivabilityVerdict(
        tier=tier,
        primary_source_display=primary,
        citations=tuple(citations),
    )


def _short(text: str, limit: int = 120) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"
