"""Scrape the CotC fan wiki and populate ``character_sprites``.

Source page: https://octopathtraveler.fandom.com/wiki/Champions

The page lists ~263 characters in 8 job-tab tables. Each row carries a
wikia-CDN-hosted PNG sprite. We grab the canonical full-res URL,
reconcile the wiki name against ``characters.canonical_name`` (applying
``config.NAME_ALIASES`` and the EX prefix↔suffix swap), then layer exact
``SPRITE_FILE_OVERRIDES`` for reviewed omissions and ambiguous EX/EX2 rows
before upserting into ``character_sprites``.

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
import urllib.parse
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
WIKI_FILE_API_URL = "https://octopathtraveler.fandom.com/api.php"
USER_AGENT = "cotc-info-bot sprite-grabber (one-off, https://github.com/douglas125/cotc-info-bot)"

# Exact Fandom file mappings for characters the Champions page cannot identify
# unambiguously (EX/EX2 rows share the same visible name), omits entirely, or
# spells differently from the Index. These are intentionally reviewed instead
# of inferred: a wrong thumbnail is harder to notice than a missing one.
SPRITE_FILE_OVERRIDES: dict[str, str] = {
    "EX Araune": "Alaune_EX_Dancer_Sprite.png",
    "EX2 Araune": "Alaune_EX_Warrior_Sprite.png",
    "EX Erika": "Elrica_EX_Thief_Sprite.png",
    "EX2 Erika": "Elrica_EX_Dancer_Sprite.png",
    "Levina EX": "Levina_EX_Dancer_Sprite.png",
    "EX2 Levina": "Levina_EX_Thief_Sprite.png",
    "EX Viola": "Viola_EX_Scholar_Sprite.png",
    "EX2 Viola": "Viola_EX_Warrior_Sprite.png",
    "Levina EX ⚔️": "Levina_EX_Dancer_Sprite.png",
    "Lynette EX ⚔️": "Lynette_EX_Sprite.png",
    "Phenn ⚔️": "Phenn_Sprite.png",
    "Xerc ⚔️": "Xerc_Sprite.png",
    "Mooloo": "Molu_Sprite.png",
    "Auron": "Auron_Sprite.png",
    "EX Mydia": "Mydia_EX_Sprite.png",
    "EX Temenos": "Temenos_EX_Sprite.png",
    "EX Tiziano": "Tiziano_EX_Sprite.png",
    "Nada": "Nada_Sprite.png",
    "Reime": "Reime_Sprite.png",
    "Tidus": "Tidus_Sprite.png",
    "Yuna": "Yuna_Sprite.png",
}

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


def _file_title_key(title: str) -> str:
    """Normalize a MediaWiki file title for underscore/space-insensitive matching."""
    if title.casefold().startswith("file:"):
        title = title[5:]
    return " ".join(title.replace("_", " ").split()).casefold()


def parse_wiki_file_urls(
    payload: dict, requested_titles: Iterable[str],
) -> dict[str, str]:
    """Validate an imageinfo response and return requested title -> CDN URL.

    Curated mappings fail closed. Every requested title must resolve to a PNG
    on Fandom's image CDN, otherwise the caller raises before touching SQLite.
    """
    requested = {_file_title_key(title): title for title in requested_titles}
    resolved: dict[str, str] = {}
    for page in payload.get("query", {}).get("pages", {}).values():
        key = _file_title_key(str(page.get("title", "")))
        original = requested.get(key)
        if original is None:
            continue
        info = (page.get("imageinfo") or [{}])[0]
        url = str(info.get("url", ""))
        mime = info.get("mime")
        if (
            mime != "image/png"
            or urllib.parse.urlparse(url).hostname != _WIKIA_HOST
        ):
            continue
        resolved[original] = normalize_url(url)

    missing = [title for title in requested.values() if title not in resolved]
    if missing:
        raise RuntimeError(
            "wiki file lookup did not return valid PNGs for: "
            + ", ".join(sorted(missing))
        )
    return resolved


@retry(
    retry=retry_if_exception_type((urllib.error.URLError, OSError, TimeoutError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=15),
    reraise=True,
)
def fetch_wiki_file_urls(file_titles: Iterable[str]) -> dict[str, str]:
    """Resolve reviewed Fandom file titles in one MediaWiki API request."""
    titles = list(dict.fromkeys(file_titles))
    if not titles:
        return {}
    params = urllib.parse.urlencode({
        "action": "query",
        "format": "json",
        "prop": "imageinfo",
        "iiprop": "url|mime",
        "titles": "|".join(f"File:{title}" for title in titles),
    })
    req = urllib.request.Request(
        f"{WIKI_FILE_API_URL}?{params}", headers={"User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return parse_wiki_file_urls(payload, titles)


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

        {"parsed": int, "matched": int, "page_mapped": int,
         "overrides": int, "total_mapped": int, "character_total": int,
         "missing": list[str], "unmatched": list[str]}

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

    applicable_overrides = {
        canonical: file_title
        for canonical, file_title in SPRITE_FILE_OVERRIDES.items()
        if canonical.casefold() in db_index
    }
    override_urls = fetch_wiki_file_urls(applicable_overrides.values())

    page_urls: dict[str, str] = {}
    unmatched: list[str] = []
    for wiki_name, raw_url in pairs:
        canonical = reconcile(wiki_name, db_index)
        if canonical is None:
            unmatched.append(wiki_name)
            continue
        url = normalize_url(raw_url)
        previous = page_urls.get(canonical)
        if (
            previous is not None
            and previous != url
            and canonical not in applicable_overrides
        ):
            raise RuntimeError(
                f"unreviewed wiki sprite collision for {canonical!r}"
            )
        page_urls[canonical] = url

    rows: dict[str, tuple[str, str]] = {
        canonical: (url, "wikia") for canonical, url in page_urls.items()
    }
    for canonical, file_title in applicable_overrides.items():
        rows[canonical] = (override_urls[file_title], "wikia-file-override")

    if rows:
        with repo.transaction(conn):
            repo.upsert_sprites_batch(
                conn,
                (
                    (canonical, url, source)
                    for canonical, (url, source) in rows.items()
                ),
            )

    character_total = len(db_canonicals)
    mapped_names = {
        r[0] for r in conn.execute("SELECT canonical_name FROM character_sprites")
    }
    missing = sorted(set(db_canonicals) - mapped_names, key=str.casefold)
    return {
        "parsed": len(pairs),
        "matched": len(rows),
        "page_mapped": len(page_urls),
        "overrides": len(applicable_overrides),
        "total_mapped": character_total - len(missing),
        "character_total": character_total,
        "missing": missing,
        "unmatched": unmatched,
    }


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
        print(
            f"\nMapped: {summary['total_mapped']} / "
            f"{summary['character_total']} characters "
            f"({summary['page_mapped']} page, "
            f"{summary['overrides']} curated)",
            flush=True,
        )
        if summary["unmatched"]:
            print(
                f"Unmatched ({len(summary['unmatched'])}) — "
                "extend config.NAME_ALIASES if needed:"
            )
            for name in summary["unmatched"]:
                print(f"  - {name!r}")
        if summary["missing"]:
            print(f"Missing sprites ({len(summary['missing'])}):")
            for name in summary["missing"]:
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
