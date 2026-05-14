"""Single source of truth for /ask_ai limits, model, and message strings."""
from __future__ import annotations

# Input
ASK_AI_MAX_QUESTION_CHARS = 2000

# Rate limiting
ASK_AI_RATE_LIMIT_PER_HOUR = 3
ASK_AI_RATE_WINDOW_SEC = 60 * 60
ASK_AI_GLOBAL_DAILY_CAP = 100

# Tool-use loop
ASK_AI_MAX_ITERATIONS = 200

# HARD cap on output tokens; passed as `max_tokens=` on EVERY
# messages.create call. 2000 tokens leaves headroom for the rare long
# answer (e.g. team analyses) without forcing the model to stop
# mid-sentence; the prompt also tells the agent to aim for under
# ~250 words so most answers come back well below this. The Discord
# embed has a 6000-char total ceiling that's enforced separately by
# the embed builder (see bot/ask_ai/embeds.py).
ASK_AI_MAX_OUTPUT_TOKENS = 2000

# Tool-result limits — protect both the model context and the Discord
# response from runaway query results.
ASK_AI_TOOL_ROW_CAP = 200
ASK_AI_TOOL_BYTE_CAP = 8 * 1024
ASK_AI_QUERY_TIMEOUT_SEC = 2.0

# Anthropic model + caching
ASK_AI_MODEL = "claude-sonnet-4-6"
ASK_AI_CACHE_TTL = "1h"

# Sonnet 4.6 published pricing in USD per 1M tokens. Used to surface a
# rough per-question cost in the embed footer; if Anthropic changes
# pricing, update these and the footer auto-corrects.
# Source: https://platform.claude.com/docs/en/about-claude/pricing
SONNET_PRICE_INPUT_PER_M = 3.0       # uncached input
SONNET_PRICE_CACHE_WRITE_PER_M = 6.0 # 1 h TTL
SONNET_PRICE_CACHE_READ_PER_M = 0.30
SONNET_PRICE_OUTPUT_PER_M = 15.0


def estimate_cost_usd(
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read: int,
    cache_write: int,
) -> float:
    """Rough USD cost of one /ask_ai run from the four token counters."""
    return (
        input_tokens  * SONNET_PRICE_INPUT_PER_M       / 1_000_000
        + output_tokens * SONNET_PRICE_OUTPUT_PER_M      / 1_000_000
        + cache_read    * SONNET_PRICE_CACHE_READ_PER_M  / 1_000_000
        + cache_write   * SONNET_PRICE_CACHE_WRITE_PER_M / 1_000_000
    )

# User-facing strings (extracted so tests can assert on them)
AGENT_UNCONFIGURED_MESSAGE = (
    "The /ask_ai agent isn't configured on this server "
    "(ANTHROPIC_API_KEY missing). Ask the admin to set it."
)
RATE_LIMIT_MESSAGE_TMPL = (
    "Slow down — you've used your {limit} /ask_ai questions for the "
    "last hour. Try again in about {minutes} minute(s)."
)
GLOBAL_CAP_MESSAGE = (
    "The shared /ask_ai daily quota ({cap} questions) is exhausted. "
    "Try again tomorrow (UTC reset)."
)
INTERNAL_ERROR_MESSAGE = (
    "Something went wrong while answering. The error has been logged."
)
ITERATION_CAP_MESSAGE = (
    "I ran out of investigation steps before finding a definitive answer. "
    "Try a more specific question."
)
