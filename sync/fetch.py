"""Fetch the full Google Sheet via the v4 API in one call.

The single `spreadsheets.get?includeGridData=true` request returns formatted text,
hyperlinks, foreground colors, and image-formula values for every cell of every
tab — enough to drive the rest of the pipeline without further round-trips.
"""
from __future__ import annotations

from typing import Any

import httplib2
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from config import SPREADSHEET_ID

# Field mask: pull only the cell properties the parsers actually consume.
# `userEnteredValue` (already covered) carries `=IMAGE("url")` formulas via
# its `formulaValue` slot — the only image-capture path the Sheets v4 API
# actually exposes through `spreadsheets.get`. Floating drawings and
# "image in cell" inserts are not API-accessible.
_FIELDS = (
    "spreadsheetId,"
    "properties.title,"
    "sheets.properties(sheetId,title,index,gridProperties),"
    "sheets.data.rowData.values("
    "formattedValue,"
    "hyperlink,"
    "note,"
    "userEnteredValue,"
    "effectiveValue,"
    "dataValidation.condition(type,values.userEnteredValue),"
    "effectiveFormat.textFormat.foregroundColorStyle.rgbColor,"
    "effectiveFormat.textFormat.foregroundColor,"
    "effectiveFormat.backgroundColorStyle.rgbColor,"
    "effectiveFormat.backgroundColor,"
    "textFormatRuns(startIndex,format(foregroundColor,foregroundColorStyle.rgbColor))"
    ")"
)


class _IdentityEncodingHttp(httplib2.Http):
    """httplib2 transport that opts out of compressed response bodies.

    The character spreadsheet response is highly repetitive JSON: in
    production it has compressed to ~192 KiB and inflated to ~19 MiB, just
    over a downstream 100x decompression-amplification guard. Requesting
    identity encoding avoids that guard entirely; we still gzip our stored
    raw snapshot after the response is parsed.
    """

    def request(
        self,
        uri,
        method="GET",
        body=None,
        headers=None,
        redirections=httplib2.DEFAULT_MAX_REDIRECTS,
        connection_type=None,
    ):
        clean_headers = {
            k: v
            for k, v in (headers or {}).items()
            if k.lower() != "accept-encoding"
        }
        clean_headers["accept-encoding"] = "identity"
        return super().request(
            uri,
            method=method,
            body=body,
            headers=clean_headers,
            redirections=redirections,
            connection_type=connection_type,
        )


def _build_service(api_key: str):
    return build(
        "sheets",
        "v4",
        developerKey=api_key,
        cache_discovery=False,
        http=_IdentityEncodingHttp(),
    )



@retry(
    retry=retry_if_exception_type((HttpError, OSError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=15),
    reraise=True,
)
def fetch_spreadsheet(api_key: str, spreadsheet_id: str = SPREADSHEET_ID) -> dict[str, Any]:
    """Single API call returning grid data for all tabs.

    Raises HttpError on non-retryable Google errors (e.g. 403 invalid key).
    """
    svc = _build_service(api_key)
    return (
        svc.spreadsheets()
        .get(spreadsheetId=spreadsheet_id, includeGridData=True, fields=_FIELDS)
        .execute()
    )


def sheet_by_gid(payload: dict[str, Any], gid: int) -> dict[str, Any] | None:
    for sheet in payload.get("sheets", []):
        if sheet.get("properties", {}).get("sheetId") == gid:
            return sheet
    return None


def iter_rows(sheet: dict[str, Any]) -> list[list[dict[str, Any]]]:
    """Return a list of rows, where each row is a list of cell dicts (possibly empty)."""
    out: list[list[dict[str, Any]]] = []
    for grid in sheet.get("data", []):
        for row in grid.get("rowData", []):
            out.append(row.get("values", []) or [])
    return out
