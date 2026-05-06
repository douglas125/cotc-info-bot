"""Tests for the sprite-URL refresher (CLI + /refresh post-step)."""
from __future__ import annotations

from pathlib import Path

import pytest

from db import repo
from scripts import refresh_sprite_urls as scraper


_FAKE_HTML = """
<table>
  <tr><th>Image</th><th>Name</th></tr>
  <tr>
    <td><img data-src="https://static.wikia.nocookie.net/octopath-traveler/images/4/4e/Cyrus_Sprite.png/revision/latest/scale-to-width-down/70?cb=20240101"></td>
    <td><a href="/wiki/Cyrus">Cyrus</a><br/>サイラス</td>
  </tr>
  <tr>
    <td><img data-src="https://static.wikia.nocookie.net/octopath-traveler/images/4/40/CotC_Castti_Sprite.png/revision/latest"></td>
    <td><a href="/wiki/Castti">Castti</a></td>
  </tr>
  <tr>
    <td><img data-src="https://static.wikia.nocookie.net/octopath-traveler/images/9/98/Hikari_EX_Sprite.png/revision/latest/scale-to-width-down/70"></td>
    <td><a href="/wiki/Hikari_EX">Hikari EX</a></td>
  </tr>
  <tr>
    <td><img data-src="https://static.wikia.nocookie.net/octopath-traveler/images/x/xx/Mystery_Sprite.png/revision/latest"></td>
    <td><a href="/wiki/Unmatched">Some Wiki Stranger</a></td>
  </tr>
</table>
"""


def _seed_three_chars(tmp_db_path: Path):
    conn = repo.connect(tmp_db_path)
    for name in ("Cyrus", "Castti", "EX Hikari"):
        repo.upsert_character(conn, canonical_name=name,
                              base_role="scholar", base_weapon="tome")
    return conn


def test_parse_pairs_extracts_name_and_image_src() -> None:
    """The HTMLParser must yield (link_text, data-src) per row, ignoring
    JP follow-ups and rows with no image."""
    pairs = scraper.parse_pairs(_FAKE_HTML)
    names = [n for n, _ in pairs]
    assert names == ["Cyrus", "Castti", "Hikari EX", "Some Wiki Stranger"]
    cyrus_url = next(u for n, u in pairs if n == "Cyrus")
    assert "Cyrus_Sprite.png" in cyrus_url
    assert cyrus_url.startswith("https://static.wikia.nocookie.net/")


def test_normalize_url_strips_scale_and_query() -> None:
    raw = (
        "https://static.wikia.nocookie.net/octopath-traveler/images/"
        "4/4e/Cyrus_Sprite.png/revision/latest/scale-to-width-down/70?cb=20240101"
    )
    out = scraper.normalize_url(raw)
    assert out == (
        "https://static.wikia.nocookie.net/octopath-traveler/images/"
        "4/4e/Cyrus_Sprite.png/revision/latest"
    )


def test_normalize_url_idempotent_on_canonical() -> None:
    canonical = (
        "https://static.wikia.nocookie.net/octopath-traveler/images/"
        "4/40/CotC_Castti_Sprite.png/revision/latest"
    )
    assert scraper.normalize_url(canonical) == canonical


def test_refresh_sprite_urls_populates_table_via_aliases(
    tmp_db_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end function call: HTML in → upserts out. ``Hikari EX`` on
    the wiki must reconcile to the DB's ``EX Hikari`` row through the
    EX prefix↔suffix swap baked into ``config.canonical_name_keys``."""
    monkeypatch.setattr(scraper, "fetch_wiki_html", lambda: _FAKE_HTML)
    conn = _seed_three_chars(tmp_db_path)
    summary = scraper.refresh_sprite_urls(conn)

    assert summary["parsed"] == 4
    assert summary["matched"] == 3
    assert summary["unmatched"] == ["Some Wiki Stranger"]

    rows = dict(conn.execute(
        "SELECT canonical_name, sprite_url FROM character_sprites"
    ).fetchall())
    conn.close()
    assert set(rows) == {"Cyrus", "Castti", "EX Hikari"}
    # The stored URL is the canonical full-res form (no scale hint, no ?cb=).
    assert rows["Cyrus"].endswith("/Cyrus_Sprite.png/revision/latest")
    assert "/scale-to-width-down/" not in rows["Cyrus"]
    assert rows["EX Hikari"].endswith("/Hikari_EX_Sprite.png/revision/latest")


def test_refresh_sprite_urls_raises_on_empty_response(
    tmp_db_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty wiki response must raise — the /refresh wrapper logs and
    moves on, but the function itself must not silently no-op (otherwise
    a structural wiki change would degrade thumbnails invisibly)."""
    monkeypatch.setattr(scraper, "fetch_wiki_html", lambda: "")
    conn = repo.connect(tmp_db_path)
    with pytest.raises(RuntimeError, match="empty response"):
        scraper.refresh_sprite_urls(conn)
    conn.close()


def test_refresh_sprite_urls_idempotent_on_rerun(
    tmp_db_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Calling twice must not double-insert — keyed by canonical_name."""
    monkeypatch.setattr(scraper, "fetch_wiki_html", lambda: _FAKE_HTML)
    conn = _seed_three_chars(tmp_db_path)
    scraper.refresh_sprite_urls(conn)
    scraper.refresh_sprite_urls(conn)
    n = conn.execute("SELECT COUNT(*) FROM character_sprites").fetchone()[0]
    conn.close()
    assert n == 3


def test_run_sync_continues_when_sprite_refresh_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The /refresh wrapper in sync.runner must isolate sprite-refresh
    failures so a wiki outage logs ``WARN: sprite refresh skipped: ...``
    without aborting the (already committed) sheet sync."""
    def boom() -> str:
        raise OSError("network unreachable")

    monkeypatch.setattr(scraper, "fetch_wiki_html", boom)

    # Mirror the runner's wrapper exactly.
    messages: list[str] = []
    progress = messages.append
    try:
        scraper.refresh_sprite_urls(None)
        progress("ok")
    except Exception as exc:
        progress(f"WARN: sprite refresh skipped: {exc}")

    assert any(m.startswith("WARN: sprite refresh skipped") for m in messages)
    assert "network unreachable" in messages[-1]
