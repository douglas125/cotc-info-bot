"""Callback tests for the `/analyze_team` dropdown view.

Stubs out :class:`discord.Interaction` so the select callback can run
without a live Discord connection. Asserts on the captured
``edit_message`` kwargs (embed, attachments, view). Coroutines are
driven via ``asyncio.run`` rather than pytest-asyncio so this test
file doesn't add a new dependency.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from analysis.types import TeamReport
from bot import team_commands, team_embeds, team_views
from db import repo


@pytest.fixture()
def seeded_team(tmp_db_path: Path):
    """A single-character team with a sword skill — enough for a non-empty matrix."""
    conn = repo.connect(tmp_db_path)
    try:
        cid = repo.upsert_character(conn, "Hero", base_role="warrior", base_weapon="sword")
        fid = repo.insert_form(
            conn, character_id=cid, display_name="Hero",
            rarity="5*", variant_kind="base", server="global",
        )
        repo.insert_affinities(conn, fid, [("weapon", "Sword", None)])
        repo.insert_skills(conn, fid, [
            {
                "slot_order": 1, "name": "Slash", "kind": "active",
                "power_min": 80, "power_max": 80, "hits": 5,
                "description": "5x AoE Sword (5x 80 Power)",
            },
        ])
        report = team_commands.build_team_report(
            conn, frontrow_form_ids=[fid],
        )
    finally:
        conn.close()
    return report


def _stub_interaction() -> MagicMock:
    """Build a MagicMock that quacks like discord.Interaction.

    Only what the view callback touches: ``response.edit_message`` and
    ``response.defer``. Both are AsyncMocks so we can `await` them.
    """
    interaction = MagicMock()
    interaction.response = MagicMock()
    interaction.response.edit_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    return interaction


def test_default_section_is_matrix() -> None:
    """The view defaults to the matrix section."""
    assert team_views.DEFAULT_SECTION == "matrix"


def test_view_timeout_is_180s(seeded_team: TeamReport) -> None:
    """Match the 180s timeout used by `/character`'s CharacterView."""
    view = team_views.AnalyzeTeamView(
        report=seeded_team, matrix_bytes=b"\x89PNG\r\n",
    )
    assert view.timeout == 180


def test_select_options_are_matrix_then_analysis(seeded_team: TeamReport) -> None:
    """Dropdown lists matrix first, analysis second."""
    view = team_views.AnalyzeTeamView(
        report=seeded_team, matrix_bytes=b"\x89PNG\r\n",
    )
    select = view.children[0]
    values = [opt.value for opt in select.options]
    assert values == ["matrix", "analysis"]
    # Default flag is on the first option (matrix).
    defaults = [opt.default for opt in select.options]
    assert defaults == [True, False]


def test_select_analysis_clears_attachments(seeded_team: TeamReport, tmp_db_path: Path) -> None:
    """Toggling to "analysis" calls edit_message with attachments=[]."""
    matrix_bytes = b"\x89PNG\r\nfake-bytes-for-test"
    view = team_views.AnalyzeTeamView(
        report=seeded_team, matrix_bytes=matrix_bytes,
    )
    select = view.children[0]
    select._values = ["analysis"]

    interaction = _stub_interaction()
    with patch("bot.team_views.bot_db.conn", return_value=repo.connect(tmp_db_path)):
        asyncio.run(select.callback(interaction))

    interaction.response.edit_message.assert_awaited_once()
    kwargs = interaction.response.edit_message.await_args.kwargs
    assert kwargs["attachments"] == []
    assert kwargs["embed"] is not None
    # Analysis embed has no attachment:// image.
    assert kwargs["embed"].image.url is None


def test_select_matrix_attaches_file(seeded_team: TeamReport, tmp_db_path: Path) -> None:
    """Toggling to "matrix" calls edit_message with a non-empty attachments list."""
    matrix_bytes = b"\x89PNG\r\nfake-bytes-for-test"
    view = team_views.AnalyzeTeamView(
        report=seeded_team, matrix_bytes=matrix_bytes,
    )
    select = view.children[0]
    select._values = ["matrix"]

    interaction = _stub_interaction()
    with patch("bot.team_views.bot_db.conn", return_value=repo.connect(tmp_db_path)):
        asyncio.run(select.callback(interaction))

    interaction.response.edit_message.assert_awaited_once()
    kwargs = interaction.response.edit_message.await_args.kwargs
    assert len(kwargs["attachments"]) == 1
    file = kwargs["attachments"][0]
    assert file.filename == "team_matrix.png"
    assert kwargs["embed"].image.url == "attachment://team_matrix.png"


def test_toggle_does_not_re_render_matrix(seeded_team: TeamReport, tmp_db_path: Path) -> None:
    """Switching matrix → analysis → matrix uses the cached bytes; the
    matrix renderer is never re-invoked from the callback path."""
    matrix_bytes = b"\x89PNG\r\nfake-bytes-for-test"
    view = team_views.AnalyzeTeamView(
        report=seeded_team, matrix_bytes=matrix_bytes,
    )

    async def _both_toggles() -> None:
        interaction = _stub_interaction()
        # Toggle to analysis.
        select = view.children[0]
        select._values = ["analysis"]
        await select.callback(interaction)
        # Toggle back to matrix. The previous Select was replaced by
        # `view.clear_items` + `view.add_item` inside the callback;
        # grab the new one.
        select = view.children[0]
        select._values = ["matrix"]
        await select.callback(interaction)

    with patch("bot.team_views.bot_db.conn", return_value=repo.connect(tmp_db_path)), \
            patch("analysis.matrix_image.render") as mock_render:
        asyncio.run(_both_toggles())
        mock_render.assert_not_called()


def test_build_matrix_message_uses_attachment_protocol(seeded_team: TeamReport, tmp_db_path: Path) -> None:
    """The matrix embed's image.url uses the attachment:// protocol."""
    from analysis import matrix_image
    rendered = matrix_image.render(seeded_team.bucketed)
    conn = repo.connect(tmp_db_path)
    try:
        message = team_embeds.build_matrix_message(
            conn, seeded_team, rendered_image=rendered,
        )
    finally:
        conn.close()
    assert message.embed.image.url == "attachment://team_matrix.png"
    assert message.file is not None
    assert message.file.filename == "team_matrix.png"


def test_build_analysis_message_has_no_file(seeded_team: TeamReport, tmp_db_path: Path) -> None:
    """The analysis breakdown embed never carries a file attachment."""
    conn = repo.connect(tmp_db_path)
    try:
        message = team_embeds.build_analysis_message(conn, seeded_team)
    finally:
        conn.close()
    assert message.file is None
    assert message.embed.image.url is None
