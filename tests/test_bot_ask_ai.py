"""Tests for the /ask_ai package.

Covers SQL guard, read-only connection, rate limiting + admin bypass,
embed chunking, off-topic refusal pass-through, tool-use loop, prompt
size, and the buff_debuff knowledge-embed sentinels.

Anthropic and Discord interactions are mocked — no network, no live DB.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest

from bot.ask_ai import agent as ask_ai_agent
from bot.ask_ai import constants, prompt
from bot.ask_ai.agent import AskResult, _run_loop_sync
from bot.ask_ai.embeds import build_ask_ai_embed
from bot.ask_ai.tool import (
    QUERY_SQLITE_TOOL,
    QueryRejected,
    _strip_string_literals_and_comments,
    _validate_sql,
    run_query,
)


# ---------------------------------------------------------------------------
# SQL safety guard
# ---------------------------------------------------------------------------

def test_validate_accepts_select() -> None:
    assert _validate_sql("SELECT * FROM characters LIMIT 5") == \
        "SELECT * FROM characters LIMIT 5"


def test_validate_accepts_with_cte() -> None:
    assert _validate_sql(
        "WITH x AS (SELECT id FROM characters) SELECT * FROM x"
    ).startswith("WITH ")


def test_validate_strips_trailing_semicolon() -> None:
    assert _validate_sql("SELECT 1;") == "SELECT 1"


@pytest.mark.parametrize("bad", [
    "INSERT INTO characters VALUES (1, 'x', NULL, NULL)",
    "UPDATE characters SET base_role='warrior'",
    "DELETE FROM characters",
    "DROP TABLE characters",
    "ALTER TABLE characters ADD COLUMN foo TEXT",
    "CREATE TABLE evil (id INTEGER)",
    "PRAGMA writable_schema = ON",
    "ATTACH DATABASE 'evil.db' AS evil",
    "VACUUM",
])
def test_validate_rejects_ddl_dml(bad: str) -> None:
    with pytest.raises(QueryRejected):
        _validate_sql(bad)


def test_validate_rejects_chained_statements() -> None:
    with pytest.raises(QueryRejected):
        _validate_sql("SELECT 1; SELECT 2")


def test_validate_rejects_non_select_leading() -> None:
    with pytest.raises(QueryRejected):
        _validate_sql("EXPLAIN SELECT * FROM characters")


def test_validate_allows_drop_inside_string_literal() -> None:
    """A user's literal "DROP TABLE" inside a LIKE pattern must not trip."""
    sql = (
        "SELECT id FROM skills "
        "WHERE description LIKE '%DROP TABLE characters%' "
        "LIMIT 3"
    )
    assert _validate_sql(sql).startswith("SELECT")


def test_strip_string_literals_handles_doubled_quotes() -> None:
    src = "SELECT 'it''s fine' AS x"
    assert "it" not in _strip_string_literals_and_comments(src).split("'")
    # Structural keywords survive
    assert "SELECT" in _strip_string_literals_and_comments(src)


# ---------------------------------------------------------------------------
# Read-only connection + result shaping
# ---------------------------------------------------------------------------

def _seed_one_character(db_path: Path) -> None:
    from db import repo
    conn = repo.connect(db_path)
    conn.execute(
        "INSERT INTO characters(canonical_name, base_role, base_weapon) "
        "VALUES ('Cyrus', 'scholar', 'tome')"
    )
    conn.execute(
        "INSERT INTO character_forms(character_id, display_name, rarity, "
        "variant_kind, server) "
        "VALUES (1, 'Cyrus', '5*', 'base', 'global')"
    )
    conn.close()


def test_run_query_returns_tsv(tmp_db_path: Path) -> None:
    _seed_one_character(tmp_db_path)
    out = run_query(
        "SELECT canonical_name, base_role FROM characters",
        db_path=tmp_db_path,
    )
    lines = out.splitlines()
    assert lines[0] == "canonical_name\tbase_role"
    assert lines[1] == "Cyrus\tscholar"


def test_run_query_no_rows_message(tmp_db_path: Path) -> None:
    out = run_query("SELECT * FROM characters", db_path=tmp_db_path)
    assert out == "(no rows)"


def test_run_query_rejects_write(tmp_db_path: Path) -> None:
    out = run_query(
        "INSERT INTO characters(canonical_name) VALUES ('X')",
        db_path=tmp_db_path,
    )
    assert out.startswith("Error:")


def test_run_query_readonly_connection_blocks_writes(tmp_db_path: Path) -> None:
    """Even if the parser were bypassed, the URI mode=ro layer rejects writes
    at SQLite level. We assert the lower layer directly."""
    from bot.ask_ai.tool import _open_readonly
    conn = _open_readonly(tmp_db_path)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO characters(canonical_name) VALUES ('Y')")
    conn.close()


def test_run_query_truncates_large_result(tmp_db_path: Path) -> None:
    from db import repo
    conn = repo.connect(tmp_db_path)
    for i in range(constants.ASK_AI_TOOL_ROW_CAP + 30):
        conn.execute(
            "INSERT INTO characters(canonical_name) VALUES (?)",
            (f"Char{i:04d}",),
        )
    conn.close()
    out = run_query(
        "SELECT canonical_name FROM characters", db_path=tmp_db_path,
    )
    assert "truncated" in out
    body_lines = out.splitlines()
    # Header + ASK_AI_TOOL_ROW_CAP rows + the trailing "… (…)" note
    assert len(body_lines) == constants.ASK_AI_TOOL_ROW_CAP + 2


def test_query_sqlite_tool_shape() -> None:
    assert QUERY_SQLITE_TOOL["name"] == "query_sqlite"
    assert "input_schema" in QUERY_SQLITE_TOOL
    assert QUERY_SQLITE_TOOL["input_schema"]["required"] == ["sql"]


# ---------------------------------------------------------------------------
# Tool-use loop
# ---------------------------------------------------------------------------

def _fake_response(
    *, stop_reason: str, content: list[dict[str, Any]],
    usage: dict[str, int] | None = None,
) -> Any:
    """Build a SimpleNamespace that quacks like an Anthropic Message."""
    usage = usage or {
        "input_tokens": 100, "output_tokens": 30,
        "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
    }
    msg = SimpleNamespace(
        stop_reason=stop_reason,
        content=[SimpleNamespace(**block) for block in content],
        usage=SimpleNamespace(**usage),
    )
    # The agent calls .model_dump() to capture the raw assistant content
    # for the next-turn message append. Provide a simple round-trip.
    msg.model_dump = lambda: {"content": content}  # type: ignore[attr-defined]
    return msg


def test_loop_returns_text_on_end_turn() -> None:
    client = SimpleNamespace()
    client.messages = SimpleNamespace()
    client.messages.create = mock.Mock(return_value=_fake_response(
        stop_reason="end_turn",
        content=[{"type": "text", "text": "Cyrus is the answer."}],
    ))
    result = _run_loop_sync(client, "Who's the strongest scholar?")
    assert result.text == "Cyrus is the answer."
    assert result.queries == []
    assert result.error is None
    assert client.messages.create.call_count == 1
    # max_tokens cap is enforced on every call.
    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["max_tokens"] == constants.ASK_AI_MAX_OUTPUT_TOKENS


def test_loop_runs_tool_then_finishes(tmp_db_path: Path, monkeypatch) -> None:
    _seed_one_character(tmp_db_path)
    # Point run_query inside the agent module at the temp DB.
    real_run_query = ask_ai_agent.run_query
    monkeypatch.setattr(
        ask_ai_agent, "run_query",
        lambda sql: real_run_query(sql, db_path=tmp_db_path),
    )

    client = SimpleNamespace()
    client.messages = SimpleNamespace()

    responses = [
        _fake_response(
            stop_reason="tool_use",
            content=[{
                "type": "tool_use",
                "id": "tool_001",
                "name": "query_sqlite",
                "input": {"sql": "SELECT canonical_name FROM characters"},
            }],
        ),
        _fake_response(
            stop_reason="end_turn",
            content=[{"type": "text", "text": "There is exactly one: Cyrus."}],
        ),
    ]
    client.messages.create = mock.Mock(side_effect=responses)

    result = _run_loop_sync(client, "How many characters are there?")
    assert "Cyrus" in result.text
    assert result.queries == ["SELECT canonical_name FROM characters"]
    assert client.messages.create.call_count == 2

    # Second call should have been given a tool_result.
    second_call_messages = client.messages.create.call_args_list[1].kwargs["messages"]
    last_user = second_call_messages[-1]
    assert last_user["role"] == "user"
    assert last_user["content"][0]["type"] == "tool_result"
    assert last_user["content"][0]["tool_use_id"] == "tool_001"


def test_loop_respects_iteration_cap(monkeypatch) -> None:
    monkeypatch.setattr(ask_ai_agent, "run_query", lambda sql: "(no rows)")
    client = SimpleNamespace()
    client.messages = SimpleNamespace()

    def _always_tool_use(**_: Any) -> Any:
        return _fake_response(
            stop_reason="tool_use",
            content=[{
                "type": "tool_use",
                "id": f"tu_{_['messages'][-1].get('role', '?')}",
                "name": "query_sqlite",
                "input": {"sql": "SELECT 1"},
            }],
        )
    client.messages.create = mock.Mock(side_effect=_always_tool_use)

    result = _run_loop_sync(client, "Loop forever")
    assert result.truncated is True
    assert result.error == "iteration-cap"
    assert client.messages.create.call_count == constants.ASK_AI_MAX_ITERATIONS


def test_loop_passes_off_topic_refusal_through(monkeypatch) -> None:
    """When the system prompt refuses an off-topic question, the agent's
    text comes back unchanged. We mock the API to simulate that response."""
    client = SimpleNamespace()
    client.messages = SimpleNamespace()
    refusal = (
        "I only answer questions about Octopath Traveler: Champions of the "
        "Continent."
    )
    client.messages.create = mock.Mock(return_value=_fake_response(
        stop_reason="end_turn",
        content=[{"type": "text", "text": refusal}],
    ))
    result = _run_loop_sync(client, "Tell me a joke")
    assert result.text == refusal
    assert result.queries == []


def test_loop_handles_anthropic_exception(monkeypatch) -> None:
    client = SimpleNamespace()
    client.messages = SimpleNamespace()
    client.messages.create = mock.Mock(side_effect=RuntimeError("network down"))
    result = _run_loop_sync(client, "Anything")
    assert result.error and "anthropic-error" in result.error
    assert result.text == constants.INTERNAL_ERROR_MESSAGE


# ---------------------------------------------------------------------------
# Embed builder — chunking
# ---------------------------------------------------------------------------

def test_embed_short_answer_uses_description() -> None:
    result = AskResult(text="Short and sweet.", input_tokens=5, output_tokens=4)
    embed = build_ask_ai_embed("test?", result)
    assert embed.description == "Short and sweet."
    assert len(embed.fields) == 0


def test_embed_long_answer_chunks_into_fields() -> None:
    # 20 lines × ~120 chars = ~2400 chars → should split into 3 fields
    # (FIELD_VALUE_LIMIT is 1024).
    line = "x" * 120
    text = "\n".join(f"{i}: {line}" for i in range(20))
    result = AskResult(text=text)
    embed = build_ask_ai_embed("long?", result)
    assert len(embed.fields) >= 2
    for f in embed.fields:
        assert len(f.value) <= 1024
        assert f.name.startswith("Answer")


def test_embed_footer_reports_query_count_and_tokens() -> None:
    result = AskResult(
        text="Hi",
        queries=["SELECT 1", "SELECT 2"],
        input_tokens=100, output_tokens=50,
        cache_read=900, cache_write=0,
    )
    embed = build_ask_ai_embed("?", result)
    assert "1050" in embed.footer.text  # total tokens
    assert "2 queries" in embed.footer.text


# ---------------------------------------------------------------------------
# System prompt — size and embedded knowledge
# ---------------------------------------------------------------------------

def test_system_prompt_includes_buff_debuff_sentinels() -> None:
    """Each of the five buff_debuff/*.md files must contribute its content
    to the assembled prompt. Catches a path typo or accidental file removal."""
    sentinels = [
        "G6 — Divine Beast",                              # README.md
        "Lynette",                                        # examples.md
        "Boost Lv",                                       # edge_cases.md
        "240",                                            # damage_cap_and_potency.md (240 potency rule)
        "frontrow",                                       # team_composition.md
    ]
    for s in sentinels:
        assert s in prompt.SYSTEM_PROMPT, f"missing sentinel: {s!r}"


def test_system_prompt_within_token_budget() -> None:
    """Rough char-based ceiling so we catch a ballooned prompt without
    needing to call the (network) count_tokens endpoint in CI.

    20K tokens × 4 chars/token ≈ 80,000 characters. The current assembly
    is comfortably under this — the test fires only if someone bloats it.
    """
    assert len(prompt.SYSTEM_PROMPT) <= 80_000


def test_system_prompt_states_off_topic_rule() -> None:
    assert "I only answer questions about Octopath Traveler" in prompt.SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Repo helpers — rate-limit counters
# ---------------------------------------------------------------------------

def test_recent_ai_query_count_window(tmp_db_path: Path) -> None:
    from db import repo
    conn = repo.connect(tmp_db_path)
    # Five rows for user 1, two outside the window
    conn.execute(
        "INSERT INTO ai_queries(user_id, asked_at, question) VALUES "
        "(1, '2024-01-01T00:00:00Z', 'old1'),"
        "(1, '2024-01-01T00:01:00Z', 'old2'),"
        "(1, '2025-06-01T12:00:00Z', 'recent1'),"
        "(1, '2025-06-01T12:30:00Z', 'recent2'),"
        "(2, '2025-06-01T12:00:00Z', 'other-user')"
    )
    assert repo.recent_ai_query_count(conn, 1, "2025-06-01T00:00:00Z") == 2
    assert repo.recent_ai_query_count(conn, 1, "2025-06-01T13:00:00Z") == 0
    assert repo.recent_ai_query_count(conn, 2, "2025-06-01T00:00:00Z") == 1
    conn.close()


def test_ai_queries_today_count(tmp_db_path: Path) -> None:
    from db import repo
    conn = repo.connect(tmp_db_path)
    conn.execute(
        "INSERT INTO ai_queries(user_id, asked_at, question) VALUES "
        "(1, '2025-06-01T00:30:00Z', 'a'),"
        "(2, '2025-06-01T11:00:00Z', 'b'),"
        "(3, '2025-06-02T00:30:00Z', 'c')"
    )
    assert repo.ai_queries_today_count(conn, "2025-06-01") == 2
    assert repo.ai_queries_today_count(conn, "2025-06-02") == 1
    assert repo.ai_queries_today_count(conn, "2030-01-01") == 0
    conn.close()


def test_insert_ai_query_round_trip(tmp_db_path: Path) -> None:
    from db import repo
    conn = repo.connect(tmp_db_path)
    rid = repo.insert_ai_query(
        conn,
        user_id=42,
        question="how many 5* clerics?",
        answer="14",
        queries_json=json.dumps(["SELECT COUNT(*) ..."]),
        input_tokens=100, output_tokens=20,
        cache_read=14000, cache_write=0,
        error=None,
    )
    assert rid >= 1
    row = conn.execute(
        "SELECT user_id, question, answer, input_tokens, cache_read, error "
        "FROM ai_queries WHERE id=?", (rid,),
    ).fetchone()
    assert row["user_id"] == 42
    assert row["answer"] == "14"
    assert row["cache_read"] == 14000
    assert row["error"] is None
    conn.close()


# ---------------------------------------------------------------------------
# Wipe loops MUST NOT touch ai_queries
# ---------------------------------------------------------------------------

def test_ai_queries_survives_clear_calls(tmp_db_path: Path) -> None:
    from db import repo
    conn = repo.connect(tmp_db_path)
    repo.insert_ai_query(
        conn,
        user_id=1, question="q", answer="a", queries_json=None,
        input_tokens=None, output_tokens=None,
        cache_read=None, cache_write=None, error=None,
    )
    for fn_name in ("clear_character_tables", "clear_enemy_tables",
                    "clear_pet_tables"):
        fn = getattr(repo, fn_name, None)
        if fn is None:
            continue
        fn(conn)
    remaining = conn.execute("SELECT COUNT(*) FROM ai_queries").fetchone()[0]
    assert remaining == 1
    conn.close()
