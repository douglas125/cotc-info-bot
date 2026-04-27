"""End-to-end smoke test for the Streamlit app + lint guard for deprecations."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


# --- deprecation guard ------------------------------------------------------

def test_no_use_container_width_in_source() -> None:
    """`use_container_width` was deprecated by Streamlit on 2025-12-31.

    Replaced with `width='stretch'` / `width='content'`. Catch any
    accidental re-introduction across the project (excluding tests, the
    plan file, and CLAUDE.md notes).
    """
    offenders: list[tuple[Path, int, str]] = []
    for py in ROOT.rglob("*.py"):
        # Skip the test file itself (it documents the deprecated name)
        if py.name == "test_app_smoke.py":
            continue
        if "__pycache__" in py.parts:
            continue
        for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            if "use_container_width" in line:
                offenders.append((py.relative_to(ROOT), i, line.strip()))
    assert not offenders, (
        "Found deprecated `use_container_width=`; use `width='stretch'` "
        f"instead. Offenders: {offenders}"
    )


# --- AppTest smoke ----------------------------------------------------------

def test_streamlit_app_runs_without_exception(tmp_path: Path, monkeypatch) -> None:
    """The full app.py script must execute cleanly when run by Streamlit's
    test harness — this would have caught the SQLite-cross-thread bug at
    import time once the page contained any DB call.
    """
    pytest.importorskip("streamlit.testing.v1")
    from streamlit.testing.v1 import AppTest

    # Redirect DB to a tmp path AND the user-config path to tmp so the test
    # is hermetic.
    import config
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.sqlite")
    monkeypatch.setattr(config, "USER_CONFIG_DIR", tmp_path / ".cotc-search")
    monkeypatch.setattr(config, "USER_CONFIG_PATH",
                        tmp_path / ".cotc-search" / "config.toml")

    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30)
    at.run()
    # The harness collects exceptions instead of bubbling them.
    assert not at.exception, f"app.py raised during execution: {at.exception}"
