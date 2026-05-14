"""Render an AskResult as a Discord embed.

Reuses bot.embeds._split_bullets_into_field_values + _add_chunked_fields
so the answer body chunks into ≤1024-char fields named "Answer (n/N)".
The agent's max_tokens cap (1500) is sized to fit Discord's ~6000-char
total embed budget after chunking.
"""
from __future__ import annotations

import discord

from bot.embeds import (
    EMBED_DESCRIPTION_LIMIT,
    FIELD_VALUE_LIMIT,
    MAX_FIELDS,
    TITLE_LIMIT,
    _add_chunked_fields,
    _split_bullets_into_field_values,
    _truncate,
)

from .agent import AskResult


_QUESTION_TITLE_LIMIT = TITLE_LIMIT
_FOOTER_TMPL = "Sonnet 4.6 · {tokens} tokens · {q} quer{plural}"


def _split_answer(text: str) -> list[str]:
    """Split the answer into bullets at line boundaries, preserving blank lines.

    The reused chunker takes a list of "bullets" (one logical line each)
    and packs them into ≤1024-char fields. Splitting on `\n` here gives
    the chunker natural break points; multi-paragraph answers stay
    readable across field boundaries.
    """
    if not text:
        return []
    return text.split("\n")


def build_ask_ai_embed(question: str, result: AskResult) -> discord.Embed:
    """Build the public response embed for a /ask_ai answer."""
    color = discord.Color.dark_red() if result.error else discord.Color.blurple()
    embed = discord.Embed(
        title=_truncate(f"❓ {question}", _QUESTION_TITLE_LIMIT),
        color=color,
    )

    bullets = _split_answer(result.text or "")
    chunks = _split_bullets_into_field_values(bullets)

    if not chunks:
        embed.description = _truncate(
            result.text or "(no answer)", EMBED_DESCRIPTION_LIMIT,
        )
    elif len(chunks) == 1 and len(chunks[0]) <= EMBED_DESCRIPTION_LIMIT:
        # One small block is cleaner as a description than a single field.
        embed.description = chunks[0]
    else:
        _add_chunked_fields(embed, "Answer", chunks[: MAX_FIELDS])
        if len(chunks) > MAX_FIELDS:
            embed.add_field(
                name="…",
                value=_truncate(
                    "Answer truncated to fit Discord's embed limit.",
                    FIELD_VALUE_LIMIT,
                ),
                inline=False,
            )

    total_tokens = (
        result.input_tokens
        + result.output_tokens
        + result.cache_read
        + result.cache_write
    )
    plural = "y" if len(result.queries) == 1 else "ies"
    footer = _FOOTER_TMPL.format(
        tokens=total_tokens, q=len(result.queries), plural=plural,
    )
    if result.truncated:
        footer = f"{footer} · iteration cap hit"
    embed.set_footer(text=footer)
    return embed
