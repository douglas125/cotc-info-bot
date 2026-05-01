"""Name → form_id resolution for the audit CLI.

A minimal subset of the logic in ``bot/commands.py``: enough to take a
user-typed character name and find the matching ``character_forms.id``,
respecting EX/EX2 word-order swaps and the ``config.NAME_ALIASES`` map.

The bot's slash command keeps its own (richer) resolver — this one is
for the offline audit CLI, where users type a comma-separated list of
names and we just need a deterministic best-effort match.
"""
from __future__ import annotations

from difflib import SequenceMatcher
import sqlite3

import config


def resolve_form_id(conn: sqlite3.Connection, name: str) -> int | None:
    """Return a form_id for ``name`` or None.

    Resolution order:
      1. Case-insensitive exact match on ``character_forms.display_name``.
      2. Same, but with EX/EX2 prefix↔suffix swap variants.
      3. Alias map lookup (``config.NAME_ALIASES``), retried via 1 and 2
         on the canonical Index name.
      4. Case-insensitive prefix match (most-rare-first wins).

    Returns the highest-rarity match in case of ties, mirroring the
    bot's behaviour.
    """
    raw = (name or "").strip()
    if not raw:
        return None

    # 1 + 2: exact match across EX swap variants.
    for variant in _ex_swap_variants(raw):
        row = conn.execute(
            "SELECT id FROM character_forms "
            "WHERE LOWER(display_name) = LOWER(?) "
            "ORDER BY rarity DESC LIMIT 1",
            (variant,),
        ).fetchone()
        if row:
            return row[0]

    # 3: alias to canonical Index name, then retry exact + swap.
    canonical = config.alias_to_canonical(raw)
    if canonical and canonical.lower() != raw.lower():
        for variant in config.canonical_name_keys(canonical):
            for swap in _ex_swap_variants(variant):
                row = conn.execute(
                    "SELECT id FROM character_forms "
                    "WHERE LOWER(display_name) = LOWER(?) "
                    "ORDER BY rarity DESC LIMIT 1",
                    (swap,),
                ).fetchone()
                if row:
                    return row[0]

    # 4: case-insensitive prefix match.
    row = conn.execute(
        "SELECT id FROM character_forms "
        "WHERE LOWER(display_name) LIKE LOWER(?) "
        "ORDER BY rarity DESC, display_name LIMIT 1",
        (raw + "%",),
    ).fetchone()
    if row:
        return row[0]

    return None


def _ex_swap_variants(name: str) -> list[str]:
    """Return ``name`` plus EX/EX2 prefix↔suffix swaps, deduped."""
    s = (name or "").strip()
    if not s:
        return []
    out = [s]
    low = s.lower()
    for prefix, suffix in (("ex2 ", " ex2"), ("ex ", " ex")):
        if low.startswith(prefix):
            rest = s[len(prefix):].strip()
            if rest:
                out.append(f"{rest}{suffix.upper()}")
        elif low.endswith(suffix):
            rest = s[: -len(suffix)].strip()
            if rest:
                out.append(f"{prefix.upper()}{rest}")
    seen: set[str] = set()
    deduped: list[str] = []
    for v in out:
        k = v.lower()
        if k not in seen:
            seen.add(k)
            deduped.append(v)
    return deduped


def resolve_form_ids(conn: sqlite3.Connection, names: list[str]) -> list[int | None]:
    """Resolve a list of names. ``None`` for any unresolved entry."""
    return [resolve_form_id(conn, n) for n in names]


def suggest_names(conn: sqlite3.Connection, name: str, *, limit: int = 3) -> list[str]:
    """Return close display-name suggestions for an unresolved query."""
    raw = (name or "").strip()
    if not raw:
        return []
    rows = conn.execute(
        "SELECT display_name FROM character_forms ORDER BY display_name"
    ).fetchall()
    scored: list[tuple[float, str]] = []
    raw_key = raw.casefold()
    for row in rows:
        display = str(row["display_name"])
        display_key = display.casefold()
        score = SequenceMatcher(None, raw_key, display_key).ratio()
        if raw_key in display_key or display_key in raw_key:
            score += 0.35
        if score >= 0.70:
            scored.append((score, display))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)

    out: list[str] = []
    seen: set[str] = set()
    for _score, display in scored:
        key = display.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(display)
        if len(out) >= limit:
            break
    return out
