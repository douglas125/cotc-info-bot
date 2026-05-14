"""Sonnet-4.6 SQL-tool agent for the /ask_ai Discord command.

The agent answers Octopath Traveler: Champions of the Continent questions
by issuing read-only SELECT queries against the local SQLite mirror via a
single `query_sqlite` tool. The system prompt embeds the canonical
`buff_debuff/*.md` game-mechanics docs so damage / buff-stacking / team
questions can be answered against the spec, not training-data recall.
"""
from __future__ import annotations

from .agent import AskResult, answer_question, AGENT_UNCONFIGURED_MESSAGE
from .constants import (
    ASK_AI_GLOBAL_DAILY_CAP,
    ASK_AI_MAX_QUESTION_CHARS,
    ASK_AI_RATE_LIMIT_PER_HOUR,
    ASK_AI_RATE_WINDOW_SEC,
)

__all__ = [
    "AskResult",
    "answer_question",
    "AGENT_UNCONFIGURED_MESSAGE",
    "ASK_AI_GLOBAL_DAILY_CAP",
    "ASK_AI_MAX_QUESTION_CHARS",
    "ASK_AI_RATE_LIMIT_PER_HOUR",
    "ASK_AI_RATE_WINDOW_SEC",
]
