"""Classifier pattern table.

The parser deliberately starts with broad, mechanical patterns rather
than one-off character rules. It extracts the shared wording used across
the community sheet for stats, typed damage, typed resistance, cap-up,
potency, multi-cast, and survivability effects. Character-specific
phrases can still become fixtures as they appear in audits.
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
# Pattern table.
# ---------------------------------------------------------------------------

PatternHandler = Callable[
    [re.Match[str], dict],   # match + skill row dict
    Sequence[ClassifiedEffect],
]

# ---------------------------------------------------------------------------
# Broad parser implementation.
# ---------------------------------------------------------------------------

_STATS = {
    "atk": "atk",
    "patk": "atk",
    "p.atk": "atk",
    "mag": "mag",
    "eatk": "mag",
    "e.atk": "mag",
    "def": "def",
    "pdef": "def",
    "p.def": "def",
    "mdef": "mdef",
    "edef": "mdef",
    "e.def": "mdef",
    "crit": "crit",
}

_TYPES = {
    "sword": "sword",
    "dagger": "dagger",
    "bow": "bow",
    "axe": "axe",
    "staff": "staff",
    "tome": "tome",
    "fan": "fan",
    "spear": "spear",
    "light": "light",
    "dark": "dark",
    "wind": "wind",
    "ice": "ice",
    "fire": "fire",
    "lightning": "lightning",
}

_UMBRELLAS = {
    "physical": "umbrella:physical",
    "elemental": "umbrella:elemental",
    "magical": "umbrella:elemental",
    "all": "umbrella:all",
}

_PCT = r"(?P<pct>\d{1,3})%"
_LIST = r"(?P<targets>[A-Za-z./, +\-]+?)"

_RE_STAT_EFFECTS = re.compile(
    rf"{_PCT}\s+{_LIST}\s+(?P<direction>Up|Down)\b", re.IGNORECASE,
)
_RE_TYPED_DAMAGE_UP = re.compile(
    rf"{_PCT}\s+{_LIST}\s+(?:Damage|Dmg)\s+Up\b", re.IGNORECASE,
)
_RE_TYPED_RES_DOWN = re.compile(
    rf"{_PCT}\s+{_LIST}\s+(?:Res|Resistance)\s+Down\b", re.IGNORECASE,
)
_RE_DAMAGE_CAP_UP = re.compile(
    r"(?P<num>[+]?\d[\d,]*(?:\.\d+)?\s*k?)\s+Damage\s+Cap(?:\s+Up)?",
    re.IGNORECASE,
)
_RE_POTENCY_UP = re.compile(rf"{_PCT}\s+Potency\s+Up\b", re.IGNORECASE)
# Match "X% Crit Damage Up" / "X% Crit Dmg Up" — additive into the Crit
# final-multiplier pool (1.25 + Σ). Distinct from "X% Crit Up" which is
# Crit *chance* Up and lives in G1 as a stat. Order matters in
# ``_handle_description``: Crit Damage Up is parsed before generic stat
# effects so the same wording isn't double-counted.
_RE_CRIT_DMG_UP = re.compile(
    rf"{_PCT}\s+Crit\s+(?:Damage|Dmg)\s+Up\b", re.IGNORECASE,
)
# "Self Guaranteed Crit", "Guaranteed Critical Strike", etc. Qualitative —
# no magnitude. ``_scope`` parsing picks up the "Self" / "All Allies"
# prefix when present; default to ``self`` since that's the only known
# wording today (Pardis EX).
_RE_GUARANTEED_CRIT = re.compile(
    r"\bGuaranteed\s+Crit(?:ical(?:\s+Strike)?)?\b", re.IGNORECASE,
)
_RE_REGEN_SCOPE = re.compile(
    r"\b(frontrow|self|all allies|all other allies)\s+regen\b",
)
_RE_DURATION = re.compile(
    r"for\s+(\d+)(?:\s*[-~]\s*(\d+))?\s+turn", re.IGNORECASE,
)


def _handle_description(match: re.Match[str], row: dict) -> Sequence[ClassifiedEffect]:
    text = match.group(0)
    effects: list[ClassifiedEffect] = []
    effects.extend(_parse_crit_dmg_up(text))   # before stat/typed_dmg_up to
    effects.extend(_parse_guaranteed_crit(text))  # avoid double-counting
    effects.extend(_parse_stat_effects(text))
    effects.extend(_parse_typed_damage_up(text))
    effects.extend(_parse_typed_res_down(text))
    effects.extend(_parse_damage_cap_up(text))
    effects.extend(_parse_potency_up(text))
    effects.extend(_parse_multi_cast(text))
    effects.extend(_parse_survivability(text))
    effects.extend(_parse_cleanse(text))
    return effects


def is_intentionally_ignored(description: str) -> bool:
    """True when a non-empty row has no current team-analysis effect.

    Attack-only rows, weakness reveals/implants, ailments, shields, and
    descriptive unique-effect labels should not appear as parser debt.
    If the text contains a known buff/debuff keyword this returns False
    so missed mechanics still show up in audits.
    """
    text = " ".join((description or "").split())
    if not text:
        return True
    lower = text.lower()
    effect_words = (
        " up", " down", "regen", "heal", "undying", "remove",
        "cleanse", "damage cap", "triplecast", "second time",
        "doublecast",
    )
    if any(word in lower for word in effect_words):
        return False
    ignored_words = (
        "weakness", "burning", "bleeding", "blind", "paralysis",
        "poison", "shield count", "cannot break", "unique effects",
        "dizziness", "cursed scar",
    )
    return bool(re.search(r"\b\d+\s*x\b", lower)) or any(
        word in lower for word in ignored_words
    )


def _effect(
    *,
    category: str,
    raw_description: str,
    targets: tuple[str, ...] = (),
    direction: str = "n/a",
    magnitude: float = 0.0,
    target_scope: str | None = None,
    condition: str | None = None,
    boost_required: int | None = None,
) -> ClassifiedEffect:
    return ClassifiedEffect(
        source_form_id=0,
        source_skill_id=0,
        source_kind="",
        category=category,
        targets=targets,
        direction=direction,
        magnitude=magnitude,
        duration_turns=_duration(raw_description),
        condition=condition,
        boost_required=boost_required,
        target_scope=target_scope,
        raw_description=raw_description,
        confidence="high",
    )


def _parse_stat_effects(text: str) -> list[ClassifiedEffect]:
    out: list[ClassifiedEffect] = []
    for m in _RE_STAT_EFFECTS.finditer(text):
        targets = tuple(_stat_targets(m.group("targets")))
        if not targets:
            continue
        direction = m.group("direction").lower()
        category = "stat_up" if direction == "up" else "stat_down"
        out.append(_effect(
            category=category,
            targets=targets,
            direction=direction,
            magnitude=_pct(m.group("pct")),
            target_scope=_scope(text, category=category),
            raw_description=text,
        ))
    return out


def _parse_typed_damage_up(text: str) -> list[ClassifiedEffect]:
    out: list[ClassifiedEffect] = []
    for m in _RE_TYPED_DAMAGE_UP.finditer(text):
        targets = tuple(_typed_targets(m.group("targets")))
        if not targets:
            continue
        out.append(_effect(
            category="dmg_up",
            targets=targets,
            direction="up",
            magnitude=_pct(m.group("pct")),
            target_scope=_scope(text, category="dmg_up"),
            raw_description=text,
        ))
    return out


def _parse_typed_res_down(text: str) -> list[ClassifiedEffect]:
    out: list[ClassifiedEffect] = []
    for m in _RE_TYPED_RES_DOWN.finditer(text):
        targets = tuple(_typed_targets(m.group("targets")))
        if not targets:
            continue
        out.append(_effect(
            category="res_down",
            targets=targets,
            direction="down",
            magnitude=_pct(m.group("pct")),
            target_scope="enemies",
            raw_description=text,
        ))
    return out


def _parse_damage_cap_up(text: str) -> list[ClassifiedEffect]:
    out: list[ClassifiedEffect] = []
    for m in _RE_DAMAGE_CAP_UP.finditer(text):
        out.append(_effect(
            category="damage_cap_up",
            direction="up",
            magnitude=_raw_number(m.group("num")),
            target_scope=_scope(text, category="damage_cap_up"),
            raw_description=text,
        ))
    return out


def _parse_potency_up(text: str) -> list[ClassifiedEffect]:
    out: list[ClassifiedEffect] = []
    for m in _RE_POTENCY_UP.finditer(text):
        out.append(_effect(
            category="skill_potency_up",
            direction="up",
            magnitude=_pct(m.group("pct")),
            target_scope=_scope(text, category="skill_potency_up"),
            raw_description=text,
        ))
    return out


def _parse_crit_dmg_up(text: str) -> list[ClassifiedEffect]:
    """Crit Damage Up — additive into the Crit final-multiplier pool.

    Note that the magnitude is added to ``1.25 + Σ Crit Damage Up``
    (per ``buff_debuff/README.md``), and applies only when crit lands.
    Stored as a decimal fraction.
    """
    out: list[ClassifiedEffect] = []
    for m in _RE_CRIT_DMG_UP.finditer(text):
        out.append(_effect(
            category="crit_dmg_up",
            direction="up",
            magnitude=_pct(m.group("pct")),
            target_scope=_scope(text, category="crit_dmg_up"),
            raw_description=text,
        ))
    return out


def _parse_guaranteed_crit(text: str) -> list[ClassifiedEffect]:
    """Guaranteed Crit — qualitative; flips the crit final-multiplier on.

    Magnitude is meaningless for this category; presence is the signal.
    Default scope is ``self`` per the only-known wording (Pardis EX:
    "Self Guaranteed Crit"); ``_scope`` picks up explicit "All Allies" /
    "Frontrow" when those prefixes appear.
    """
    out: list[ClassifiedEffect] = []
    for _m in _RE_GUARANTEED_CRIT.finditer(text):
        out.append(_effect(
            category="crit_guaranteed",
            magnitude=0.0,
            target_scope=_scope(text, category="crit_guaranteed") or "self",
            raw_description=text,
        ))
    return out


def _parse_multi_cast(text: str) -> list[ClassifiedEffect]:
    lower = text.lower()
    out: list[ClassifiedEffect] = []
    if "triplecast" in lower:
        out.append(_effect(
            category="multi_cast",
            magnitude=3.0,
            target_scope="self",
            raw_description=text,
        ))
    if "doublecast" in lower or "used a second time in a row" in lower:
        out.append(_effect(
            category="multi_cast",
            magnitude=2.0,
            target_scope="self",
            raw_description=text,
        ))
    return out


def _parse_survivability(text: str) -> list[ClassifiedEffect]:
    lower = text.lower()
    out: list[ClassifiedEffect] = []
    if "undying" in lower:
        out.append(_effect(
            category="undying",
            target_scope=_scope(text, category="undying"),
            raw_description=text,
        ))
    if "heal" in lower:
        out.append(_effect(
            category="heal",
            magnitude=_strength(text, "Heal Strength"),
            target_scope=_scope(text, category="heal"),
            raw_description=text,
        ))
    # BP/SP regen does not preserve combat continuity; HP regen does.
    if (
        "hp regen" in lower
        or _RE_REGEN_SCOPE.search(lower)
    ) and "bp regen" not in lower and "sp regen" not in lower:
        out.append(_effect(
            category="regen",
            magnitude=_strength(text, "Regen Strength"),
            target_scope=_scope(text, category="regen"),
            raw_description=text,
        ))
    return out


def _parse_cleanse(text: str) -> list[ClassifiedEffect]:
    lower = text.lower()
    if "remove" not in lower and "cleanse" not in lower:
        return []
    if "status" not in lower and "ailment" not in lower and "swap block" not in lower:
        return []
    return [_effect(
        category="cleanse",
        target_scope=_scope(text, category="cleanse"),
        raw_description=text,
    )]


def _pct(raw: str) -> float:
    return float(raw) / 100.0


def _raw_number(raw: str) -> float:
    s = raw.strip().replace("+", "").replace(",", "").lower()
    if s.endswith("k"):
        return float(s[:-1].strip()) * 1000.0
    return float(s)


def _duration(text: str) -> int | None:
    m = _RE_DURATION.search(text)
    if not m:
        return None
    return int(m.group(2) or m.group(1))


def _strength(text: str, label: str) -> float:
    m = re.search(rf"(\d+)(?:\s*[-~]\s*(\d+))?\s+{re.escape(label)}", text, re.IGNORECASE)
    if not m:
        return 0.0
    return float(m.group(2) or m.group(1))


def _scope(text: str, *, category: str) -> str | None:
    lower = text.lower()
    if (
        "single-enemy" in lower
        or "single enemy" in lower
        or ("aoe" in lower and category in {"stat_down", "res_down"})
    ):
        return "enemies"
    if "all allies:" in lower and category in {
        "damage_cap_up", "skill_potency_up", "regen", "undying", "heal",
    }:
        return "all_allies"
    if (
        lower.startswith("self")
        or "grant self" in lower
        or "grants self" in lower
        or "gain self" in lower
        or "self gain" in lower
        or "this attack" in lower
    ):
        return "self"
    if "all other allies" in lower:
        return "other_allies"
    if "all allies" in lower or "all ally" in lower:
        return "all_allies"
    if "frontrow" in lower or "front row" in lower:
        return "frontrow"
    if "single-ally" in lower or "single ally" in lower:
        return "single_ally"
    if "self" in lower:
        return "self"
    if category in {"stat_down", "res_down"}:
        return "enemies"
    if category in {"damage_cap_up", "skill_potency_up", "multi_cast"}:
        return "self"
    return None


def _split_targets(raw: str) -> list[str]:
    cleaned = raw.replace("+", " ")
    cleaned = re.sub(r"\band\b", "/", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bor\b", "/", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.replace(",", "/")
    return [p.strip().lower().strip(".") for p in cleaned.split("/") if p.strip()]


def _stat_targets(raw: str) -> list[str]:
    out: list[str] = []
    for part in _split_targets(raw):
        token = part.split()[-1]
        mapped = _STATS.get(token)
        if mapped and mapped not in out:
            out.append(mapped)
    return out


def _typed_targets(raw: str) -> list[str]:
    out: list[str] = []
    for part in _split_targets(raw):
        words = part.split()
        for word in words:
            mapped = _TYPES.get(word) or _UMBRELLAS.get(word)
            if mapped and mapped not in out:
                out.append(mapped)
                break
    return out


# Ordered list. The classifier iterates in order and runs every pattern
# against the description; first-match-wins is NOT used because a single
# description often contains multiple effects. The catch-all handler parses
# the whole text once and emits every effect it recognises.
PATTERNS: list[tuple[re.Pattern[str], PatternHandler]] = [
    (re.compile(r".+", re.IGNORECASE | re.DOTALL), _handle_description),
]
