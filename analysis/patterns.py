"""Classifier pattern table.

Phase 1 ships this **empty**. Phase 2 fills it as user-provided team
examples turn into fixtures under ``tests/fixtures/teams/``.

Each pattern is ``(regex, handler)`` where ``handler`` takes the regex
match object plus the source-skill metadata and returns a list of
:class:`analysis.types.ClassifiedEffect` (or an empty list to fall
through to the next pattern).

The classifier runs **all** patterns over a description and concatenates
the resulting effects, so one description can produce multiple effects
(e.g. "20% Atk Up + 15% Sword DMG Up" → one G1 + one G2). When no
pattern fires, the classifier emits a single ``unparsed`` effect.
"""
from __future__ import annotations

import re
from typing import Callable, Sequence

from .types import ClassifiedEffect


# ---------------------------------------------------------------------------
# Domain constants used by the classifier and the audit CLI.
# ---------------------------------------------------------------------------

# Free, always-available items that grant +100k damage cap up. Documented
# in ``buff_debuff/damage_cap_and_potency.md``.
FREE_DAMAGE_CAP_ORBS: tuple[str, ...] = (
    "Orb of King Dulin",
    "Blade of Eternal Flaw",
    "Sage Helva's Orb",
)

# Each free orb contributes this much cap up (raw units).
DAMAGE_CAP_PER_FREE_ORB: float = 100_000.0


# ---------------------------------------------------------------------------
# Pattern table — empty in Phase 1.
# ---------------------------------------------------------------------------

PatternHandler = Callable[
    [re.Match[str], dict],   # match + skill row dict
    Sequence[ClassifiedEffect],
]

# Ordered list. The classifier iterates in order and runs every pattern
# against the description; first-match-wins is NOT used because a single
# description often contains multiple effects.
PATTERNS: list[tuple[re.Pattern[str], PatternHandler]] = [
    # Phase 2 will add entries here, e.g.:
    #
    #   (
    #       re.compile(r"(\d+)%\s+Atk\s+Up", re.IGNORECASE),
    #       _handle_stat_up_atk,
    #   ),
    #
    # Each fixture in tests/fixtures/teams/ that fails because it expects
    # a non-empty BucketedTeam should drive the next pattern to add.
]
