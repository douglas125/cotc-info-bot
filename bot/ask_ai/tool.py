"""The single `query_sqlite` tool exposed to the agent.

The tool runs ONE SELECT statement against a fresh read-only SQLite
connection. Multiple safety layers stand between the model's SQL and the
on-disk database:

1. Statement parsing — must be a single SELECT or WITH … SELECT, no
   DDL/DML keywords, no second statement after a semicolon.
2. URI mode=ro — the connection itself rejects writes at the SQLite
   level, so even a parser bypass can't mutate data.
3. Wall-clock timeout via threading.Timer + conn.interrupt() — kills a
   pathological query (`WHERE x LIKE '%' OR sleep(...)` style).
4. Row + byte caps on the serialized result — prevents flooding the
   model context with megabytes of rows.
"""
from __future__ import annotations

import re
import sqlite3
import threading
from pathlib import Path
from typing import Any

from config import DB_PATH

from .constants import (
    ASK_AI_QUERY_TIMEOUT_SEC,
    ASK_AI_TOOL_BYTE_CAP,
    ASK_AI_TOOL_ROW_CAP,
)


QUERY_SQLITE_TOOL: dict[str, Any] = {
    "name": "query_sqlite",
    "description": (
        "Run a single read-only SELECT statement against the CotC SQLite "
        "mirror and get up to 200 rows back as plain text (TSV with a "
        "header row). Use this for every factual lookup: characters, "
        "forms, skills, equipment, unique-effect glossaries, stats, profiles, enemies, enemy "
        "stats/weaknesses, pets. For text searches over skill or "
        "equipment text, use the *_fts tables with `MATCH 'word'`, NOT "
        "LIKE. Always add a tight LIMIT clause; results past 200 rows "
        "or 8 KB are truncated. Anything other than a single SELECT (or "
        "WITH … SELECT) is rejected by a safety guard before SQLite "
        "ever sees it."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "sql": {
                "type": "string",
                "description": (
                    "A single SELECT (or WITH … SELECT) statement. "
                    "No DDL/DML, no PRAGMA, no ATTACH, no second "
                    "statement after a semicolon."
                ),
            },
        },
        "required": ["sql"],
    },
}


# Forbidden tokens, matched as whole words so they don't false-positive
# in user-supplied string literals (e.g. a skill description containing
# "create"). Only DDL/DML/connection-level keywords are listed; SELECT
# and WITH are explicitly allowed.
_FORBIDDEN = re.compile(
    r"(?<![A-Za-z_])("
    r"insert|update|delete|drop|alter|create|replace|attach|detach|"
    r"pragma|vacuum|reindex|begin|commit|rollback|savepoint|release"
    r")(?![A-Za-z_])",
    re.IGNORECASE,
)
_LEADING_KEYWORD = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)


class QueryRejected(Exception):
    """Raised when the SQL fails the safety guard."""


def _strip_string_literals_and_comments(sql: str) -> str:
    """Return SQL with string literals and comments blanked out.

    Forbidden-keyword scanning runs on the result so a literal like
    "DROP TABLE" inside `WHERE description LIKE '%DROP TABLE%'` doesn't
    trip the guard. Single-quoted strings (including '' escapes) and
    `-- line` / `/* block */` comments are stripped; the structural SQL
    around them is preserved for the keyword regex.
    """
    out: list[str] = []
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        if ch == "'":
            j = i + 1
            while j < n:
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        j += 2
                        continue
                    j += 1
                    break
                j += 1
            i = j
            continue
        if ch == "-" and i + 1 < n and sql[i + 1] == "-":
            nl = sql.find("\n", i)
            if nl == -1:
                break
            i = nl
            continue
        if ch == "/" and i + 1 < n and sql[i + 1] == "*":
            end = sql.find("*/", i + 2)
            if end == -1:
                break
            i = end + 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _validate_sql(sql: str) -> str:
    """Return the trimmed SQL or raise QueryRejected with a tool-friendly message."""
    s = (sql or "").strip().rstrip(";").strip()
    if not s:
        raise QueryRejected("empty SQL")
    if not _LEADING_KEYWORD.match(s):
        raise QueryRejected(
            "only single SELECT (or WITH … SELECT) statements are allowed"
        )
    if ";" in s:
        # A semicolon mid-statement means the model tried to chain a
        # second statement after the SELECT.
        raise QueryRejected("multiple statements are not allowed")
    sanitized = _strip_string_literals_and_comments(s)
    m = _FORBIDDEN.search(sanitized)
    if m:
        raise QueryRejected(
            f"forbidden keyword `{m.group(1).upper()}`; "
            f"this tool only runs SELECT queries"
        )
    return s


def _open_readonly(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def _serialize_rows(headers: list[str], rows: list[sqlite3.Row]) -> str:
    """Return a TSV-style block. Caller is responsible for byte-truncation."""
    lines = ["\t".join(headers)]
    for r in rows:
        cells = []
        for v in r:
            if v is None:
                cells.append("")
            else:
                # Replace tabs/newlines so TSV stays one-row-per-line and
                # the model doesn't get confused by embedded delimiters.
                s = str(v).replace("\t", " ").replace("\r", " ").replace("\n", " ")
                cells.append(s)
        lines.append("\t".join(cells))
    return "\n".join(lines)


def run_query(sql: str, *, db_path: Path | None = None) -> str:
    """Execute the model-supplied SQL safely and return the tool result string.

    On success: returns the TSV body, possibly with a `… (truncated, …)`
    suffix.
    On rejection or sqlite3 error: returns an `Error: …` string. The agent
    loop feeds this string back as the tool_result so the model can
    correct itself rather than crash.
    """
    try:
        validated = _validate_sql(sql)
    except QueryRejected as e:
        return f"Error: {e}"

    target = db_path or DB_PATH
    try:
        conn = _open_readonly(target)
    except sqlite3.Error as e:
        return f"Error: could not open database: {e}"

    timer = threading.Timer(ASK_AI_QUERY_TIMEOUT_SEC, conn.interrupt)
    timer.daemon = True
    timer.start()
    try:
        try:
            cur = conn.execute(validated)
        except sqlite3.Error as e:
            return f"Error: {e}"
        try:
            # Fetch one extra row so we can tell the model when results
            # were truncated (vs the natural end of a small result set).
            fetched = cur.fetchmany(ASK_AI_TOOL_ROW_CAP + 1)
        except sqlite3.Error as e:
            return f"Error: {e}"
        truncated_rows = len(fetched) > ASK_AI_TOOL_ROW_CAP
        rows = fetched[:ASK_AI_TOOL_ROW_CAP]
        headers = (
            [d[0] for d in cur.description] if cur.description else []
        )
    finally:
        timer.cancel()
        conn.close()

    if not headers:
        return "(no columns returned)"
    if not rows:
        return "(no rows)"

    body = _serialize_rows(headers, rows)
    notes: list[str] = []
    if truncated_rows:
        notes.append(
            f"truncated to {ASK_AI_TOOL_ROW_CAP} rows; add LIMIT or filter"
        )
    if len(body.encode("utf-8")) > ASK_AI_TOOL_BYTE_CAP:
        # Byte cap protects model context from a small number of huge text
        # rows (e.g. multi-paragraph skill descriptions). Bisect down to a
        # row count that fits.
        keep = len(rows)
        while keep > 0:
            keep //= 2
            trial = _serialize_rows(headers, rows[:keep])
            if len(trial.encode("utf-8")) <= ASK_AI_TOOL_BYTE_CAP:
                body = trial
                notes.append(
                    f"byte-truncated to first {keep} rows "
                    f"(of {len(rows)} returned)"
                )
                break
    if notes:
        body = f"{body}\n… ({'; '.join(notes)})"
    return body
