"""Public entry point for /ask_ai: the tool-use loop around Sonnet 4.6.

`answer_question(question)` runs the full conversation until either the
model returns text without a tool_use block (`stop_reason='end_turn'`),
the iteration cap is hit, or an error breaks the loop. It returns an
`AskResult` capturing the final text plus telemetry (queries the agent
ran, token usage, error string when applicable) so the caller can both
render an embed and log to the `ai_queries` table.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

import config

from .constants import (
    AGENT_UNCONFIGURED_MESSAGE,
    ASK_AI_CACHE_TTL,
    ASK_AI_MAX_ITERATIONS,
    ASK_AI_MAX_OUTPUT_TOKENS,
    ASK_AI_MODEL,
    INTERNAL_ERROR_MESSAGE,
    ITERATION_CAP_MESSAGE,
)
from .prompt import SYSTEM_PROMPT
from .tool import QUERY_SQLITE_TOOL, run_query

logger = logging.getLogger(__name__)


@dataclass
class AskResult:
    """Outcome of a single /ask_ai run, suitable for embed rendering and logging."""
    text: str
    queries: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0
    error: str | None = None
    truncated: bool = False  # iteration cap hit before end_turn


def _get_api_key() -> str | None:
    return config.get_setting("ANTHROPIC_API_KEY", "anthropic_api_key")


def is_configured() -> bool:
    return bool(_get_api_key())


def _extract_text(response: Any) -> str:
    """Concatenate all text blocks in the response.content list."""
    parts: list[str] = []
    for block in response.content:
        # SDK objects expose .type / .text; dict shapes (test mocks) use ["..."]
        btype = getattr(block, "type", None) or block.get("type")  # type: ignore[union-attr]
        if btype == "text":
            text = getattr(block, "text", None)
            if text is None:
                text = block.get("text", "")  # type: ignore[union-attr]
            if text:
                parts.append(text)
    return "".join(parts).strip()


def _extract_tool_uses(response: Any) -> list[Any]:
    return [
        b for b in response.content
        if (getattr(b, "type", None) or b.get("type")) == "tool_use"  # type: ignore[union-attr]
    ]


def _accumulate_usage(result: AskResult, usage: Any) -> None:
    """Pull the four token counters off response.usage; tolerate dicts and SDK objects."""
    def _g(name: str) -> int:
        v = getattr(usage, name, None)
        if v is None and isinstance(usage, dict):
            v = usage.get(name)
        return int(v or 0)

    result.input_tokens += _g("input_tokens")
    result.output_tokens += _g("output_tokens")
    result.cache_read += _g("cache_read_input_tokens")
    result.cache_write += _g("cache_creation_input_tokens")


def _build_system_blocks() -> list[dict[str, Any]]:
    """System prompt as a single cached text block (1 h TTL, ephemeral)."""
    return [
        {
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral", "ttl": ASK_AI_CACHE_TTL},
        },
    ]


def _build_tools() -> list[dict[str, Any]]:
    return [QUERY_SQLITE_TOOL]


def _run_loop_sync(client: Any, question: str) -> AskResult:
    """The synchronous tool-use loop. Wrapped by `answer_question` in an
    asyncio.to_thread so it doesn't block the Discord event loop."""
    result = AskResult(text="")
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": question},
    ]

    for iteration in range(ASK_AI_MAX_ITERATIONS):
        try:
            response = client.messages.create(
                model=ASK_AI_MODEL,
                max_tokens=ASK_AI_MAX_OUTPUT_TOKENS,
                system=_build_system_blocks(),
                tools=_build_tools(),
                messages=messages,
                extra_headers={"anthropic-beta": "extended-cache-ttl-2025-04-11"},
            )
        except Exception as e:
            logger.exception("ask_ai: anthropic call failed (iter=%d)", iteration)
            result.error = f"anthropic-error: {e!r}"
            result.text = INTERNAL_ERROR_MESSAGE
            return result

        _accumulate_usage(result, getattr(response, "usage", None))

        stop_reason = getattr(response, "stop_reason", None)
        if stop_reason == "tool_use":
            tool_uses = _extract_tool_uses(response)
            if not tool_uses:
                # Defensive: stop_reason said tool_use but no block found.
                result.text = _extract_text(response) or INTERNAL_ERROR_MESSAGE
                return result

            messages.append({
                "role": "assistant",
                "content": response.model_dump()["content"],
            })

            tool_results: list[dict[str, Any]] = []
            for tu in tool_uses:
                tu_id = getattr(tu, "id", None) or tu.get("id")  # type: ignore[union-attr]
                tu_input = getattr(tu, "input", None) or tu.get("input", {})  # type: ignore[union-attr]
                sql = (tu_input or {}).get("sql", "")
                result.queries.append(sql)
                tool_output = run_query(sql)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu_id,
                    "content": tool_output,
                })
            messages.append({"role": "user", "content": tool_results})
            continue

        if stop_reason in ("end_turn", "stop_sequence"):
            result.text = _extract_text(response) or INTERNAL_ERROR_MESSAGE
            return result

        # max_tokens / refusal / model_context_window_exceeded / pause_turn / …
        text = _extract_text(response)
        result.text = text or INTERNAL_ERROR_MESSAGE
        result.error = f"unexpected-stop-reason: {stop_reason!r}"
        return result

    # Iteration cap hit before end_turn. Return whatever text the last
    # response had, falling back to the cap message.
    result.truncated = True
    if not result.text:
        result.text = ITERATION_CAP_MESSAGE
    result.error = "iteration-cap"
    return result


def _make_client() -> Any:
    """Lazy-import so the bot can boot without `anthropic` installed."""
    from anthropic import Anthropic  # local import

    api_key = _get_api_key()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not configured")
    return Anthropic(api_key=api_key)


async def answer_question(question: str) -> AskResult:
    """Run the agent in a background thread so the Discord event loop stays free."""
    if not is_configured():
        return AskResult(text=AGENT_UNCONFIGURED_MESSAGE, error="unconfigured")
    try:
        client = _make_client()
    except Exception as e:
        logger.exception("ask_ai: failed to construct Anthropic client")
        return AskResult(text=INTERNAL_ERROR_MESSAGE, error=f"client-init: {e!r}")
    return await asyncio.to_thread(_run_loop_sync, client, question)


def queries_to_json(queries: list[str]) -> str:
    """Serialize the agent's SQL queries for the ai_queries log."""
    return json.dumps(queries, ensure_ascii=False)
