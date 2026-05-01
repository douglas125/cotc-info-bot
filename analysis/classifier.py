"""Skill/equipment description → ClassifiedEffect[].

Pure data extraction: pattern table in :mod:`analysis.patterns` is run
against the raw description, and each matching pattern's handler emits
one or more :class:`~analysis.types.ClassifiedEffect`. When no pattern
fires for a non-empty description, a single ``unparsed`` effect is
emitted so the audit CLI can surface it for review.

Phase 1 ships the pattern table empty. Every non-empty description
classifies as ``unparsed``; the empty/whitespace-only case yields zero
effects.
"""
from __future__ import annotations

from typing import Mapping

from . import patterns
from .types import ClassifiedEffect


def _is_blank(text: str | None) -> bool:
    return not text or not text.strip()


def _row_get(row: Mapping[str, object] | object, key: str, default=None):
    """Read a column from either a sqlite3.Row or a dict.

    sqlite3.Row supports indexing by name but not ``.get()``.
    """
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]
    except (IndexError, KeyError):
        return default


def _to_dict(row: Mapping[str, object] | object) -> dict:
    """Convert a sqlite3.Row to a plain dict; pass dicts through."""
    if isinstance(row, dict):
        return dict(row)
    try:
        return {k: row[k] for k in row.keys()}  # type: ignore[attr-defined]
    except AttributeError:
        return {}


def classify_skill(
    skill: Mapping[str, object],
    *,
    form_id: int,
) -> list[ClassifiedEffect]:
    """Classify one skill row into zero or more effects.

    ``skill`` is a sqlite3.Row-like mapping. Only the columns the
    classifier inspects are read: ``id``, ``kind``, ``description``,
    plus whatever future pattern handlers need.
    """
    description = str(_row_get(skill, "description") or "")
    if _is_blank(description):
        return []

    skill_id = int(_row_get(skill, "id") or -1)
    source_kind = str(_row_get(skill, "kind") or "active")

    return _run_patterns(
        description=description,
        form_id=form_id,
        skill_id=skill_id,
        source_kind=source_kind,
        skill_row=_to_dict(skill),
    )


def classify_equipment(
    equipment: Mapping[str, object],
    *,
    form_id: int,
) -> list[ClassifiedEffect]:
    """Classify one A4 accessory row.

    The classifier reuses the same pattern table over equipment text so
    cap-up / stat-up wording is recognised wherever it appears. The
    emitted effects carry ``source_kind='equipment'`` so the aggregator
    bins them as Passive (per ``buff_debuff/README.md`` rule 6).
    """
    description = str(_row_get(equipment, "description") or "")
    if _is_blank(description):
        return []

    return _run_patterns(
        description=description,
        form_id=form_id,
        skill_id=-1,
        source_kind="equipment",
        skill_row=_to_dict(equipment),
    )


def _run_patterns(
    *,
    description: str,
    form_id: int,
    skill_id: int,
    source_kind: str,
    skill_row: dict,
) -> list[ClassifiedEffect]:
    out: list[ClassifiedEffect] = []
    for regex, handler in patterns.PATTERNS:
        for match in regex.finditer(description):
            effects = handler(match, skill_row)
            for eff in effects:
                # Pattern handlers don't know the source ids; stamp them here
                # so handlers stay self-contained and testable.
                if eff.source_form_id == 0 or eff.source_skill_id == 0:
                    out.append(_with_ids(
                        eff,
                        form_id=form_id,
                        skill_id=skill_id,
                        source_kind=source_kind,
                    ))
                else:
                    out.append(eff)

    if out:
        return out

    if patterns.is_intentionally_ignored(description):
        return []

    # No pattern fired — surface the description as unparsed for the audit.
    return [
        ClassifiedEffect(
            source_form_id=form_id,
            source_skill_id=skill_id,
            source_kind=source_kind,
            category="unparsed",
            targets=(),
            direction="n/a",
            magnitude=0.0,
            duration_turns=None,
            condition=None,
            boost_required=None,
            target_scope=None,
            raw_description=description,
            confidence="unparsed",
        )
    ]


def _with_ids(
    eff: ClassifiedEffect,
    *,
    form_id: int,
    skill_id: int,
    source_kind: str,
) -> ClassifiedEffect:
    """Replace the source ids on a handler-emitted effect.

    Pattern handlers are expected to leave ``source_form_id`` /
    ``source_skill_id`` as ``0`` and ``source_kind`` empty so the
    classifier can stamp them. This keeps handlers self-contained.
    """
    return ClassifiedEffect(
        source_form_id=form_id,
        source_skill_id=skill_id,
        source_kind=source_kind or eff.source_kind,
        category=eff.category,
        targets=eff.targets,
        direction=eff.direction,
        magnitude=eff.magnitude,
        duration_turns=eff.duration_turns,
        condition=eff.condition,
        boost_required=eff.boost_required,
        target_scope=eff.target_scope,
        raw_description=eff.raw_description,
        confidence=eff.confidence,
    )
