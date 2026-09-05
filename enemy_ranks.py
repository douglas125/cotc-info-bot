"""Shared enemy rank normalization and ordering (no fixed EX ceiling)."""
from __future__ import annotations

import re

Rank = str
RANK_PATTERN = re.compile(r"^\s*(rank\s*[123]|ex\s*[1-9]\d*)\s*$", re.IGNORECASE)


def normalize_rank(value: str) -> Rank | None:
    if value.strip().lower() == "default":
        return "Default"
    if not RANK_PATTERN.fullmatch(value):
        return None
    compact = re.sub(r"\s+", "", value).lower()
    return ("EX" + compact[2:]) if compact.startswith("ex") else ("Rank" + compact[4:])


def rank_order(rank: Rank) -> int:
    """Ascending difficulty for persistence; 99 is the legacy unknown sentinel."""
    normalized = normalize_rank(rank)
    if normalized == "Default":
        return 0
    if normalized and normalized.startswith("Rank"):
        return int(normalized[4:])
    if normalized and normalized.startswith("EX"):
        return 3 + int(normalized[2:])
    return 99


def rank_label(rank: Rank) -> str:
    normalized = normalize_rank(rank)
    if normalized and normalized.startswith("EX"):
        return "EX " + normalized[2:]
    if normalized and normalized.startswith("Rank"):
        return "Rank " + normalized[4:]
    return rank


def rank_description(rank: Rank) -> str | None:
    if rank == "Default":
        return "Single-stat NPC"
    if rank == "Rank1":
        return "Lowest difficulty"
    if rank.startswith("EX"):
        return "Endgame difficulty"
    return None
