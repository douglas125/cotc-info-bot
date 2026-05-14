"""Render an AskResult as a Discord embed.

Reuses bot.embeds._split_bullets_into_field_values + _add_chunked_fields
so the answer body chunks into ≤1024-char fields named "Answer (n/N)".
A separate Discord-side trim guards the embed against the 6000-char
total ceiling so the API never rejects a too-large embed.
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
from .constants import ASK_AI_MAX_ITERATIONS, estimate_cost_usd


_QUESTION_TITLE_LIMIT = TITLE_LIMIT
_FOOTER_TMPL = "Sonnet 4.6 · {tokens} tokens · ${cost:.4f} · {q} quer{plural}"

# Discord rejects an embed whose total payload (title + description +
# every field name + every field value + footer) exceeds 6000 chars.
# Leave headroom for fixed parts so chunked answer fields can sum to
# ~5500 chars without the API throwing HTTPException.
_EMBED_TOTAL_LIMIT = 6000
_EMBED_FIXED_OVERHEAD = 500


def _trim_chunks_to_embed_budget(
    chunks: list[str], fixed_overhead_chars: int,
) -> tuple[list[str], bool]:
    """Drop trailing chunks until the cumulative size fits Discord's budget.

    Returns the kept chunks and a flag indicating whether anything was
    dropped. Each kept chunk also accounts for ~16 chars of "Answer (n/N)"
    field-name overhead."""
    budget = _EMBED_TOTAL_LIMIT - fixed_overhead_chars
    kept: list[str] = []
    used = 0
    for c in chunks:
        cost = len(c) + 16  # field name "Answer (n/N)"
        if used + cost > budget:
            return kept, True
        kept.append(c)
        used += cost
    return kept, False


def build_progress_embed(question: str, step: int) -> discord.Embed:
    """Placeholder embed shown while the agent is iterating.

    `step` is the 1-indexed iteration number; rendered as `(step / max)`."""
    embed = discord.Embed(
        title=_truncate(f"❓ {question}", _QUESTION_TITLE_LIMIT),
        color=discord.Color.greyple(),
        description=f"🔄 Working… (step {step} / {ASK_AI_MAX_ITERATIONS})",
    )
    embed.set_footer(text="Sonnet 4.6 · streaming")
    return embed


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
    discord_trimmed = False

    if not chunks:
        embed.description = _truncate(
            result.text or "(no answer)", EMBED_DESCRIPTION_LIMIT,
        )
    elif len(chunks) == 1 and len(chunks[0]) <= EMBED_DESCRIPTION_LIMIT:
        # One small block is cleaner as a description than a single field.
        embed.description = chunks[0]
    else:
        budget_chunks = chunks[: MAX_FIELDS]
        kept, trimmed_by_budget = _trim_chunks_to_embed_budget(
            budget_chunks, _EMBED_FIXED_OVERHEAD,
        )
        discord_trimmed = trimmed_by_budget or len(chunks) > MAX_FIELDS
        _add_chunked_fields(embed, "Answer", kept)
        if discord_trimmed:
            embed.add_field(
                name="…",
                value=_truncate(
                    "Answer truncated to fit Discord's 6000-char embed limit.",
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
    cost = estimate_cost_usd(
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cache_read=result.cache_read,
        cache_write=result.cache_write,
    )
    footer = _FOOTER_TMPL.format(
        tokens=total_tokens, cost=cost, q=len(result.queries), plural=plural,
    )
    if result.truncated or discord_trimmed:
        if result.error == "max-tokens":
            reason = "max output tokens"
        elif result.truncated:
            reason = "iteration cap"
        else:
            reason = "Discord 6KB embed"
        footer = f"{footer} · truncated ({reason})"
    embed.set_footer(text=footer)
    return embed
