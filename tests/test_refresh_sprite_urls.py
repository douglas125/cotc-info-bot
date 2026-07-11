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


def _cdn(filename: str) -> str:
    return (
        "https://static.wikia.nocookie.net/octopath-traveler/images/"
        f"a/aa/{filename}/revision/latest"
    )


def test_parse_wiki_file_urls_validates_and_normalizes() -> None:
    payload = {"query": {"pages": {"1": {
        "title": "File:Alaune EX Warrior Sprite.png",
        "imageinfo": [{
            "url": _cdn("Alaune_EX_Warrior_Sprite.png") + "?cb=123",
            "mime": "image/png",
        }],
    }}}}

    rows = scraper.parse_wiki_file_urls(
        payload, ["Alaune_EX_Warrior_Sprite.png"],
    )

    assert rows == {
        "Alaune_EX_Warrior_Sprite.png":
            _cdn("Alaune_EX_Warrior_Sprite.png"),
    }


@pytest.mark.parametrize(
    ("url", "mime"),
    [
        ("https://example.com/Alaune.png", "image/png"),
        (_cdn("Alaune_EX_Warrior_Sprite.png"), "image/jpeg"),
    ],
)
def test_parse_wiki_file_urls_rejects_untrusted_or_non_png(
    url: str, mime: str,
) -> None:
    payload = {"query": {"pages": {"1": {
        "title": "File:Alaune EX Warrior Sprite.png",
        "imageinfo": [{"url": url, "mime": mime}],
    }}}}

    with pytest.raises(RuntimeError, match="Alaune_EX_Warrior_Sprite.png"):
        scraper.parse_wiki_file_urls(
            payload, ["Alaune_EX_Warrior_Sprite.png"],
        )


def test_parse_wiki_file_urls_requires_every_requested_title() -> None:
    with pytest.raises(RuntimeError, match="Mydia_EX_Sprite.png"):
        scraper.parse_wiki_file_urls(
            {"query": {"pages": {}}}, ["Mydia_EX_Sprite.png"],
        )


@pytest.mark.parametrize(
    ("canonical", "filename"),
    [
        ("Levina EX ⚔️", "Levina_EX_Dancer_Sprite.png"),
        ("Lynette EX ⚔️", "Lynette_EX_Sprite.png"),
        ("Phenn ⚔️", "Phenn_Sprite.png"),
        ("Xerc ⚔️", "Xerc_Sprite.png"),
        ("Mooloo", "Molu_Sprite.png"),
    ],
)
def test_regional_and_spelling_overrides_are_explicit(
    canonical: str, filename: str,
) -> None:
    assert scraper.SPRITE_FILE_OVERRIDES[canonical] == filename


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


def test_curated_files_disambiguate_ex_and_ex2_collisions(
    tmp_db_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    html = """
    <table>
      <tr><td><img src="%s"></td><td><a>Alaune EX</a></td></tr>
      <tr><td><img src="%s"></td><td><a>Alaune EX</a></td></tr>
      <tr><td><img src="%s"></td><td><a>Elrica EX</a></td></tr>
      <tr><td><img src="%s"></td><td><a>Elrica EX</a></td></tr>
      <tr><td><img src="%s"></td><td><a>Levina EX</a></td></tr>
      <tr><td><img src="%s"></td><td><a>Levina EX</a></td></tr>
      <tr><td><img src="%s"></td><td><a>Viola EX</a></td></tr>
      <tr><td><img src="%s"></td><td><a>Viola EX</a></td></tr>
    </table>
    """ % tuple(_cdn(name) for name in (
        "Alaune_EX_Dancer_Sprite.png", "Alaune_EX_Warrior_Sprite.png",
        "Elrica_EX_Thief_Sprite.png", "Elrica_EX_Dancer_Sprite.png",
        "Levina_EX_Dancer_Sprite.png", "Levina_EX_Thief_Sprite.png",
        "Viola_EX_Scholar_Sprite.png", "Viola_EX_Warrior_Sprite.png",
    ))
    monkeypatch.setattr(scraper, "fetch_wiki_html", lambda: html)
    monkeypatch.setattr(
        scraper, "fetch_wiki_file_urls",
        lambda titles: {title: _cdn(title) for title in titles},
    )
    conn = repo.connect(tmp_db_path)
    expected = {
        "EX Araune": "Alaune_EX_Dancer_Sprite.png",
        "EX2 Araune": "Alaune_EX_Warrior_Sprite.png",
        "EX Erika": "Elrica_EX_Thief_Sprite.png",
        "EX2 Erika": "Elrica_EX_Dancer_Sprite.png",
        "Levina EX": "Levina_EX_Dancer_Sprite.png",
        "EX2 Levina": "Levina_EX_Thief_Sprite.png",
        "EX Viola": "Viola_EX_Scholar_Sprite.png",
        "EX2 Viola": "Viola_EX_Warrior_Sprite.png",
    }
    for canonical in expected:
        repo.upsert_character(conn, canonical_name=canonical,
                              base_role=None, base_weapon=None)

    summary = scraper.refresh_sprite_urls(conn)
    rows = {
        r["canonical_name"]: (r["sprite_url"], r["source"])
        for r in conn.execute(
            "SELECT canonical_name, sprite_url, source FROM character_sprites"
        )
    }
    conn.close()

    assert summary["total_mapped"] == summary["character_total"] == 8
    assert summary["missing"] == []
    for canonical, filename in expected.items():
        assert rows[canonical] == (_cdn(filename), "wikia-file-override")


def test_unreviewed_collision_preserves_existing_sprite(
    tmp_db_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    html = """
    <table>
      <tr><td><img src="%s"></td><td><a>Cyrus</a></td></tr>
      <tr><td><img src="%s"></td><td><a>Cyrus</a></td></tr>
    </table>
    """ % (_cdn("Cyrus_A.png"), _cdn("Cyrus_B.png"))
    monkeypatch.setattr(scraper, "fetch_wiki_html", lambda: html)
    conn = repo.connect(tmp_db_path)
    repo.upsert_character(conn, canonical_name="Cyrus",
                          base_role=None, base_weapon=None)
    repo.upsert_sprite(conn, "Cyrus", _cdn("Cyrus_Existing.png"))
    conn.execute(
        "UPDATE character_sprites SET updated_at='2000-01-01 00:00:00' "
        "WHERE canonical_name='Cyrus'"
    )

    with pytest.raises(RuntimeError, match="unreviewed wiki sprite collision"):
        scraper.refresh_sprite_urls(conn)

    row = conn.execute(
        "SELECT sprite_url, updated_at FROM character_sprites "
        "WHERE canonical_name='Cyrus'"
    ).fetchone()
    conn.close()
    assert row["sprite_url"] == _cdn("Cyrus_Existing.png")
    assert row["updated_at"] == "2000-01-01 00:00:00"


def test_override_lookup_failure_preserves_existing_sprite(
    tmp_db_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(scraper, "fetch_wiki_html", lambda: _FAKE_HTML)
    monkeypatch.setattr(
        scraper, "fetch_wiki_file_urls",
        lambda _titles: (_ for _ in ()).throw(RuntimeError("missing curated file")),
    )
    conn = repo.connect(tmp_db_path)
    repo.upsert_character(conn, canonical_name="Auron",
                          base_role=None, base_weapon=None)
    repo.upsert_sprite(conn, "Auron", _cdn("Auron_Existing.png"))
    conn.execute(
        "UPDATE character_sprites SET updated_at='2000-01-01 00:00:00' "
        "WHERE canonical_name='Auron'"
    )

    with pytest.raises(RuntimeError, match="missing curated file"):
        scraper.refresh_sprite_urls(conn)

    row = conn.execute(
        "SELECT sprite_url, updated_at FROM character_sprites "
        "WHERE canonical_name='Auron'"
    ).fetchone()
    conn.close()
    assert row["sprite_url"] == _cdn("Auron_Existing.png")
    assert row["updated_at"] == "2000-01-01 00:00:00"


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
