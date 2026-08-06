"""Tests for db/queries/submission_media.py.

Unit tests use a mock AsyncSession and run without a DB.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from db.queries import delete_media


async def test_delete_media_returns_true_when_row_deleted() -> None:
    session = AsyncMock()
    session.execute.return_value = SimpleNamespace(rowcount=1)

    result = await delete_media(session, media_id=5, submission_id=10)

    assert result is True
    session.execute.assert_awaited_once()
    session.flush.assert_awaited_once()


async def test_delete_media_returns_false_when_no_row_matched() -> None:
    session = AsyncMock()
    session.execute.return_value = SimpleNamespace(rowcount=0)

    result = await delete_media(session, media_id=99, submission_id=10)

    assert result is False
    session.execute.assert_awaited_once()
    session.flush.assert_awaited_once()
