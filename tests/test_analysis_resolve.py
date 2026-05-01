from __future__ import annotations

from pathlib import Path

from analysis import resolve
from db import repo


def _seed_form(conn, name: str) -> int:
    cid = repo.upsert_character(conn, name, base_role="warrior", base_weapon="sword")
    return repo.insert_form(conn, character_id=cid, display_name=name, rarity="5*")


def test_audit_resolver_uses_team_aliases(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    try:
        black_knight = _seed_form(conn, "Black Knight")
        black_maiden = _seed_form(conn, "Black Maiden")
        pardis = _seed_form(conn, "Pardis")
        lucette = _seed_form(conn, "Lucette")
        cygna = _seed_form(conn, "Cygna")

        assert resolve.resolve_form_id(conn, "Dark Knight") == black_knight
        assert resolve.resolve_form_id(conn, "Dark Priestess") == black_maiden
        assert resolve.resolve_form_id(conn, "Pardis III") == pardis
        assert resolve.resolve_form_id(conn, "Lucetta") == lucette
        assert resolve.resolve_form_id(conn, "Signa") == cygna
    finally:
        conn.close()


def test_suggest_names_returns_close_db_matches(tmp_db_path: Path) -> None:
    conn = repo.connect(tmp_db_path)
    try:
        _seed_form(conn, "Dark Princess")
        _seed_form(conn, "Solon")

        assert resolve.suggest_names(conn, "Dark Priestess", limit=1) == ["Dark Princess"]
    finally:
        conn.close()
