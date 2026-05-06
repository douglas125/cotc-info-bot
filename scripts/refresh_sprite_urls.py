"""One-off scraper: populate `character_sprites` from the CotC fan wiki.

Source page: https://octopathtraveler.fandom.com/wiki/Champions

The page lists ~80–90 characters in 8 job-tab tables (NOT all 700+).
Each row carries a wikia-CDN-hosted PNG sprite. We grab the canonical
full-res URL, reconcile the wiki name against ``characters.canonical_name``
(applying ``config.NAME_ALIASES`` and the EX prefix↔suffix swap), and
upsert into ``character_sprites``.

Run:

    conda activate cotc-search
    python -m scripts.refresh_sprite_urls

Override the SQLite path via ``COTC_DB_PATH`` (same env var the bot
uses), so the same script works locally and on Railway::

    railway run python -m scripts.refresh_sprite_urls

Idempotent — re-runs upsert in place. Output is a stdout summary plus
per-row "no match" lines you can promote into ``config.NAME_ALIASES``.

This script is the only writer for ``character_sprites``. ``/refresh``
does NOT touch the table — it survives the wipe by design.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from html.parser import HTMLParser
from typing import Iterable

# Force UTF-8 on Windows consoles so character names with diacritics
# (e.g. "Kainé?") don't crash prints.
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass

from config import canonical_name_keys
from db import repo

WIKI_API_URL = (
    "https://octopathtraveler.fandom.com/api.php"
    "?action=parse&page=Champions&format=json&prop=text&redirects=1"
)
USER_AGENT = "cotc-info-bot sprite-grabber (one-off, https://github.com/douglas125/cotc-info-bot)"

# Wikia URLs land in `data-src` because the page lazy-loads images.
# Format: .../images/<X>/<XY>/<Filename>.png/revision/latest[/scale-to-width-down/N][?cb=...]
_WIKIA_HOST = "static.wikia.nocookie.net"
_RE_SCALE = re.compile(r"/scale-to-width-down/\d+")


def fetch_wiki_html() -> str:
    """Return the parsed page HTML from the MediaWiki API.

    The public HTML route (``/wiki/Champions``) returns 403 from a script
    user-agent, but the API JSON route (``/api.php?action=parse``) is open.
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


# --- main -------------------------------------------------------------------


def main() -> int:
    print(f"Fetching {WIKI_API_URL}", flush=True)
    html = fetch_wiki_html()
    if not html:
        print("ERROR: empty response from wiki API", file=sys.stderr)
        return 1
    pairs = parse_pairs(html)
    print(f"Parsed {len(pairs)} (name, url) pairs from the page.", flush=True)

    conn = repo.connect()
    repo.bootstrap(conn)
    try:
        db_canonicals = [
            r[0] for r in conn.execute("SELECT canonical_name FROM characters")
        ]
        if not db_canonicals:
            print(
                "ERROR: characters table is empty — run a sync first "
                "(`python -m sync.cli --api-key ...`).",
                file=sys.stderr,
            )
            return 2
        db_index = build_db_index(db_canonicals)

        matched = 0
        unmatched: list[str] = []
        for wiki_name, raw_url in pairs:
            canonical = reconcile(wiki_name, db_index)
            if canonical is None:
                unmatched.append(wiki_name)
                continue
            url = normalize_url(raw_url)
            repo.upsert_sprite(conn, canonical, url, source="wikia")
            matched += 1

        print(f"\nMatched: {matched} / {len(pairs)}", flush=True)
        if unmatched:
            print(f"Unmatched ({len(unmatched)}) — extend config.NAME_ALIASES if needed:")
            for name in unmatched:
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
