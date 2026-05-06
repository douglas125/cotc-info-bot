"""Scrape the CotC fan wiki and populate ``character_sprites``.

Source page: https://octopathtraveler.fandom.com/wiki/Champions

The page lists ~263 characters in 8 job-tab tables. Each row carries a
wikia-CDN-hosted PNG sprite. We grab the canonical full-res URL,
reconcile the wiki name against ``characters.canonical_name`` (applying
``config.NAME_ALIASES`` and the EX prefix↔suffix swap), and upsert into
``character_sprites``.

Two entry points share the same ``refresh_sprite_urls(conn)`` function:

* This module's ``main()`` — standalone CLI for an isolated re-scrape::

      conda activate cotc-search
      python -m scripts.refresh_sprite_urls

* ``sync.runner.run_sync`` — calls it as a non-fatal post-step on every
  ``/refresh``, AFTER the main sheet-sync transaction commits, so a wiki
  outage doesn't abort an otherwise-good refresh. ``character_sprites``
  is preserved across ``/refresh`` (intentionally absent from every
  ``clear_*`` loop), so existing rows survive even if the post-step is
  skipped.

Override the SQLite path via ``COTC_DB_PATH`` so the CLI works locally
and on Railway. Idempotent — re-runs upsert in place.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from html.parser import HTMLParser
from typing import Iterable

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config import canonical_name_keys, force_utf8_console
from db import repo

force_utf8_console()

WIKI_API_URL = (
    "https://octopathtraveler.fandom.com/api.php"
    "?action=parse&page=Champions&format=json&prop=text&redirects=1"
)
USER_AGENT = "cotc-info-bot sprite-grabber (one-off, https://github.com/douglas125/cotc-info-bot)"

# Wikia URLs land in `data-src` because the page lazy-loads images.
# Format: .../images/<X>/<XY>/<Filename>.png/revision/latest[/scale-to-width-down/N][?cb=...]
_WIKIA_HOST = "static.wikia.nocookie.net"
_RE_SCALE = re.compile(r"/scale-to-width-down/\d+")


@retry(
    retry=retry_if_exception_type((urllib.error.URLError, OSError, TimeoutError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=15),
    reraise=True,
)
def fetch_wiki_html() -> str:
    """Return the parsed page HTML from the MediaWiki API.

    The public HTML route (``/wiki/Champions``) returns 403 from a script
    user-agent, but the API JSON route (``/api.php?action=parse``) is open.
    Mirrors the retry shape used by ``sync.fetch.fetch_spreadsheet`` so a
    transient wiki outage during ``/refresh`` doesn't immediately skip the
    sprite post-step.
    """
    req = urllib.request.Request(WIKI_API_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    text = payload.get("parse", {}).get("text", {})
    if isinstance(text, dict):
        return text.get("*", "") or ""
    return text or ""


# --- HTML parsing -----------------------------------------------------------


class _ChampionsTableParser(HTMLParser):
    """Extract ``(link_text, img_data_src)`` from each table row.

    State machine:
      - inside <table>: track row position (which <td> we're in)
      - inside the FIRST <td> of a row: capture the first <img>'s data-src
      - inside the SECOND <td>: capture the first <a>'s text content
      - emit one (name, url) pair per row

    Tolerant of nested tags inside the name `<a>` — concatenates all text
    until the closing `</a>`. Skips header rows that have no image.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[tuple[str, str]] = []
        self._in_table = 0
        self._td_index = -1
        self._row_img_url: str | None = None
        self._row_name_parts: list[str] = []
        self._capturing_link_text = False

    # row + cell tracking
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "table":
            self._in_table += 1
        if not self._in_table:
            return
        if tag == "tr":
            self._td_index = -1
            self._row_img_url = None
            self._row_name_parts = []
            self._capturing_link_text = False
        elif tag in ("td", "th"):
            self._td_index += 1
            self._capturing_link_text = False
        elif tag == "img" and self._td_index == 0:
            url = a.get("data-src") or a.get("src") or ""
            if _WIKIA_HOST in url and self._row_img_url is None:
                self._row_img_url = url
        elif tag == "a" and self._td_index == 1 and not self._row_name_parts:
            self._capturing_link_text = True

    def handle_endtag(self, tag: str) -> None:
        if not self._in_table:
            if tag == "table":
                # Stray </table> outside a counted open — ignore.
                pass
            return
        if tag == "a" and self._capturing_link_text:
            self._capturing_link_text = False
        elif tag == "tr":
            name = "".join(self._row_name_parts).strip()
            url = self._row_img_url
            if name and url:
                self.results.append((name, url))
            self._td_index = -1
            self._row_img_url = None
            self._row_name_parts = []
            self._capturing_link_text = False
        elif tag == "table":
            self._in_table = max(0, self._in_table - 1)

    def handle_data(self, data: str) -> None:
        if self._capturing_link_text and data:
            self._row_name_parts.append(data)


def parse_pairs(html: str) -> list[tuple[str, str]]:
    """Return ``[(wiki_name, raw_image_url), ...]`` from the wiki HTML.

    Deduplicated on (name, url) because the page sometimes lists the same
    character in multiple sub-tables (e.g. travelers in their job tab AND
    a "Featured" overview).
    """
    p = _ChampionsTableParser()
    p.feed(html)
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for name, url in p.results:
        key = (name, url)
        if key in seen:
            continue
        seen.add(key)
        out.append((name, url))
    return out


def normalize_url(raw: str) -> str:
    """Strip a wikia thumbnail URL down to its canonical full-res form.

    Removes ``/scale-to-width-down/N`` (size hint applied at render time
    instead) and any ``?cb=...`` cache-buster query string.
    """
    url = _RE_SCALE.sub("", raw)
    url = url.split("?", 1)[0]
    return url


# --- name reconciliation ----------------------------------------------------


def build_db_index(canonical_names: Iterable[str]) -> dict[str, str]:
    """Casefolded canonical_name → original canonical_name."""
    return {name.casefold(): name for name in canonical_names if name}


def reconcile(wiki_name: str, db_index: dict[str, str]) -> str | None:
    """Map a wiki link text to a ``characters.canonical_name`` row.

    Tries every alias-equivalent shape ``config.canonical_name_keys``
    yields (handles EX prefix↔suffix and the project's NAME_ALIASES map).
    Returns ``None`` if nothing matches — caller logs and skips.
    """
    for candidate in canonical_name_keys(wiki_name):
        hit = db_index.get(candidate.casefold())
        if hit:
            return hit
    return None


# --- callable + CLI ---------------------------------------------------------


def refresh_sprite_urls(conn) -> dict:
    """Fetch the wiki page, reconcile names, upsert into ``character_sprites``.

    Returns a summary dict::

        {"parsed": int, "matched": int, "unmatched": list[str]}

    Raises on network/HTML/parse errors — callers (the ``/refresh``
    runner, the CLI ``main``) decide whether to swallow or surface.

    HTTP + parsing happen first (no SQLite write lock held during
    network I/O). The matched upserts are then batched into a single
    ``BEGIN IMMEDIATE`` so the per-row autocommit overhead doesn't
    compound 263× on every refresh.
    """
    html = fetch_wiki_html()
    if not html:
        raise RuntimeError("empty response from wiki API")
    pairs = parse_pairs(html)
    db_canonicals = [
        r[0] for r in conn.execute("SELECT canonical_name FROM characters")
    ]
    db_index = build_db_index(db_canonicals)

    upserts: list[tuple[str, str]] = []
    unmatched: list[str] = []
    for wiki_name, raw_url in pairs:
        canonical = reconcile(wiki_name, db_index)
        if canonical is None:
            unmatched.append(wiki_name)
            continue
        upserts.append((canonical, normalize_url(raw_url)))

    if upserts:
        with repo.transaction(conn):
            repo.upsert_sprites_batch(
                conn, ((canonical, url, "wikia") for canonical, url in upserts),
            )

    return {"parsed": len(pairs), "matched": len(upserts), "unmatched": unmatched}


def main() -> int:
    print(f"Fetching {WIKI_API_URL}", flush=True)
    conn = repo.connect()
    repo.bootstrap(conn)
    try:
        if not conn.execute("SELECT 1 FROM characters LIMIT 1").fetchone():
            print(
                "ERROR: characters table is empty — run a sync first "
                "(`python -m sync.cli --api-key ...`).",
                file=sys.stderr,
            )
            return 2
        try:
            summary = refresh_sprite_urls(conn)
        except Exception as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

        print(
            f"Parsed {summary['parsed']} (name, url) pairs from the page.",
            flush=True,
        )
        print(f"\nMatched: {summary['matched']} / {summary['parsed']}", flush=True)
        if summary["unmatched"]:
            print(
                f"Unmatched ({len(summary['unmatched'])}) — "
                "extend config.NAME_ALIASES if needed:"
            )
            for name in summary["unmatched"]:
                print(f"  - {name!r}")

        total = conn.execute(
            "SELECT COUNT(*) FROM character_sprites"
        ).fetchone()[0]
        print(f"\ncharacter_sprites now has {total} rows.", flush=True)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
