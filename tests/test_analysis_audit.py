from __future__ import annotations

from pathlib import Path

from analysis import audit
from db import repo


def test_audit_summary_is_ascii_and_reports_unresolved_backrow(
    tmp_db_path: Path, capsys,
) -> None:
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
    finally:
        conn.close()

    rc = audit.main([
        "Dark Knight",
        "--backrow", "Dark Priestess",
        "--cap-orbs", "3",
        "--db", str(tmp_db_path),
    ])
    out = capsys.readouterr().out

    assert rc == 0
    assert "Unresolved names:" in out
    assert "Dark Priestess" in out
    assert "1 counted" in out
    out.encode("ascii")
