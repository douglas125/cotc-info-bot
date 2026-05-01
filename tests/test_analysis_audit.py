from __future__ import annotations

from pathlib import Path

from analysis import audit
from db import repo


def test_audit_resolves_aliases_and_surfaces_alias_trail(
    tmp_db_path: Path, capsys,
) -> None:
    """Aliased typed names resolve to real forms AND appear in the output.

    The CLI now uses Unicode glyphs (≈, ×, →) to match the Discord embed
    line format. stdout is reconfigured to UTF-8 at module load.
    """
    conn = repo.connect(tmp_db_path)
    try:
        cid = repo.upsert_character(conn, "Black Knight", base_role="warrior", base_weapon="sword")
        fid = repo.insert_form(conn, character_id=cid, display_name="Black Knight", rarity="5*")
        repo.insert_affinities(conn, fid, [("weapon", "Sword", None)])
        repo.insert_skills(conn, fid, [
            {
                "slot_order": 1,
                "name": "Fivefold",
                "kind": "active",
                "power_min": 100,
                "power_max": 100,
                "hits": 5,
                "description": "5x AoE Sword (5x 100 Power)",
            },
        ])
        cid = repo.upsert_character(conn, "Black Maiden", base_role="dancer", base_weapon="fan")
        fid = repo.insert_form(conn, character_id=cid, display_name="Black Maiden", rarity="5*")
        repo.insert_affinities(conn, fid, [("weapon", "Fan", None)])
        cid = repo.upsert_character(conn, "Cygna", base_role="dancer", base_weapon="fan")
        fid = repo.insert_form(conn, character_id=cid, display_name="Cygna", rarity="5*")
        repo.insert_affinities(conn, fid, [("weapon", "Fan", None)])
    finally:
        conn.close()

    rc = audit.main([
        "Dark Knight",
        "--backrow", "Dark Priestess, Signa",
        "--cap-orbs", "3",
        "--db", str(tmp_db_path),
    ])
    out = capsys.readouterr().out

    assert rc == 0
    assert "Backrow:  Black Maiden, Cygna" in out
    assert "Unresolved names:" not in out
    assert "3 counted" in out
    # Alias trail surfaces the typed -> resolved mapping.
    assert "Aliased inputs:" in out
    assert "Dark Knight -> Black Knight" in out
    assert "Dark Priestess -> Black Maiden" in out
    assert "Signa -> Cygna" in out
    # New output sections present.
    assert "Damage potential by type:" in out
    assert "Parser coverage:" in out
