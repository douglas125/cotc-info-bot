"""Fetch the full Google Sheet via the v4 API in one call.

The single `spreadsheets.get?includeGridData=true` request returns formatted text,
hyperlinks, foreground colors, and image-formula values for every cell of every
tab — enough to drive the rest of the pipeline without further round-trips.
"""
from __future__ import annotations

from typing import Any

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from config import SPREADSHEET_ID

# Field mask: pull only the cell properties the parsers actually consume.
_FIELDS = (
    "spreadsheetId,"
    "properties.title,"
    "sheets.properties(sheetId,title,index,gridProperties),"
    "sheets.data.rowData.values("
    "formattedValue,"
    "hyperlink,"
    "userEnteredValue,"
    "effectiveValue,"
    "effectiveFormat.textFormat.foregroundColorStyle.rgbColor,"
    "effectiveFormat.textFormat.foregroundColor,"
    "effectiveFormat.backgroundColorStyle.rgbColor,"
    "effectiveFormat.backgroundColor,"
    "textFormatRuns(startIndex,format(foregroundColor,foregroundColorStyle.rgbColor))"
    ")"
)


def _build_service(api_key: str):
    return build("sheets", "v4", developerKey=api_key, cache_discovery=False)


@retry(
    retry=retry_if_exception_type((HttpError, OSError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=15),
    reraise=True,
)
def fetch_spreadsheet(api_key: str) -> dict[str, Any]:
    """Single API call returning grid data for all tabs.

    Raises HttpError on non-retryable Google errors (e.g. 403 invalid key).
    """
    svc = _build_service(api_key)
    return (
        svc.spreadsheets()
        .get(spreadsheetId=SPREADSHEET_ID, includeGridData=True, fields=_FIELDS)
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
