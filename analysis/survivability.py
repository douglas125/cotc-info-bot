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
from typing import Callable

from db import repo

from .types import (
    BucketedTeam,
    ClassifiedEffect,
    SurvivabilityCitation,
    SurvivabilityVerdict,
)


_TierPredicate = Callable[[ClassifiedEffect], bool]

_TIERS: tuple[tuple[str, _TierPredicate], ...] = (
    ("Undying",          lambda e: e.category == "undying"),
    ("Full-party regen", lambda e: e.category == "regen"
                                   and e.target_scope in {"all_allies", "other_allies"}),
    ("Frontrow regen",   lambda e: e.category == "regen" and e.target_scope == "frontrow"),
    ("Heal-only",        lambda e: e.category == "heal"),
)


def assess(
    bucketed: BucketedTeam, conn: sqlite3.Connection,
) -> SurvivabilityVerdict:
    """Pick the highest tier matched on the active 4 and cite it."""
    for tier, predicate in _TIERS:
        hits = [e for e in bucketed.classified if predicate(e)]
        if hits:
            return _verdict(tier, hits, conn)

    return SurvivabilityVerdict(
        tier="None", primary_source_display="—", citations=(),
    )


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
    return text[: max(0, limit - 1)].rstrip() + "…"
