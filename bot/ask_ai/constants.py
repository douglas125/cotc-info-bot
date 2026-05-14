"""Single source of truth for /ask_ai limits, model, and message strings."""
from __future__ import annotations

# Input
ASK_AI_MAX_QUESTION_CHARS = 2000

# Rate limiting
ASK_AI_RATE_LIMIT_PER_HOUR = 3
ASK_AI_RATE_WINDOW_SEC = 60 * 60
ASK_AI_GLOBAL_DAILY_CAP = 100

# Tool-use loop
ASK_AI_MAX_ITERATIONS = 5

# HARD cap on output tokens; passed as `max_tokens=` on EVERY messages.create
# call. Bounds cost per question and guarantees the answer fits Discord's
# ~6000-char total embed budget after chunking (1500 tokens ≈ 6000 chars).
ASK_AI_MAX_OUTPUT_TOKENS = 1500

# Tool-result limits — protect both the model context and the Discord
# response from runaway query results.
ASK_AI_TOOL_ROW_CAP = 200
ASK_AI_TOOL_BYTE_CAP = 8 * 1024
ASK_AI_QUERY_TIMEOUT_SEC = 2.0

# Anthropic model + caching
ASK_AI_MODEL = "claude-sonnet-4-6"
ASK_AI_CACHE_TTL = "1h"

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
