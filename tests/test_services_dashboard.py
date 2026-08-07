"""Tests for services/dashboard.py — General-topic stats dashboard."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from services import dashboard


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_system_message(key: str, chat_id: int, message_id: int, payload: dict | None = None):
    """Build a minimal fake system_message row."""
    row = MagicMock()
    row.key = key
    row.chat_id = chat_id
    row.message_id = message_id
    row.payload = payload or {}
    return row


def _make_lock(resource_type: str, resource_id: str, moderator_id: int = 1):
    lock = MagicMock()
    lock.resource_type = resource_type
    lock.resource_id = resource_id
    lock.moderator_id = moderator_id
    return lock


class _SessionCtx:
    def __init__(self, session) -> None:
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class _FakeSessionFactory:
    def __init__(self, session) -> None:
        self._session = session

    def __call__(self):
        return _SessionCtx(self._session)


@pytest.fixture(autouse=True)
def _reset_dashboard_state(monkeypatch):
    """Each test starts with a clean dirty flag / last render time."""
    monkeypatch.setattr(dashboard, "_dirty", False)
    monkeypatch.setattr(dashboard, "_last_render_at", 0.0)


def _patch_counters(
    monkeypatch,
    *,
    status_counts: dict | None = None,
    dead: list | None = None,
    published_7d: int = 0,
    rejected_7d: int = 0,
    user=None,
) -> None:
    monkeypatch.setattr(
        dashboard, "count_submissions_by_status", AsyncMock(return_value=status_counts or {})
    )
    monkeypatch.setattr(dashboard, "list_dead_publications", AsyncMock(return_value=dead or []))
    monkeypatch.setattr(
        dashboard, "count_recent_publications", AsyncMock(return_value=published_7d)
    )
    monkeypatch.setattr(dashboard, "count_recent_rejections", AsyncMock(return_value=rejected_7d))
    monkeypatch.setattr(dashboard, "get_user_by_id", AsyncMock(return_value=user))


# ---------------------------------------------------------------------------
# Content tests
# ---------------------------------------------------------------------------


async def test_dashboard_text_has_correct_counters_and_empty_locks(monkeypatch) -> None:
    session = AsyncMock()
    locks_result = MagicMock()
    locks_result.scalars.return_value.all.return_value = []
    session.execute = AsyncMock(return_value=locks_result)

    _patch_counters(
        monkeypatch,
        status_counts={"pending": 3, "scheduled": 6},
        dead=[MagicMock(), MagicMock()],
        published_7d=7,
        rejected_7d=9,
    )

    text = await dashboard._build_dashboard_text(session)

    assert "<b>3</b>" in text  # pending
    assert "<b>4</b>" in text  # scheduled = 6 - 2 dead
    assert "<b>2</b>" in text  # dead
    assert "7" in text  # published_7d
    assert "9" in text  # rejected_7d
    assert "Сейчас никто ничего не редактирует" in text


async def test_dashboard_text_includes_locks_block(monkeypatch) -> None:
    session = AsyncMock()
    lock = _make_lock("submission", "42", moderator_id=1)
    locks_result = MagicMock()
    locks_result.scalars.return_value.all.return_value = [lock]
    session.execute = AsyncMock(return_value=locks_result)

    user = MagicMock()
    user.username = "moderator1"
    _patch_counters(monkeypatch, user=user)

    text = await dashboard._build_dashboard_text(session)

    assert "Сейчас в работе" in text
    assert "@moderator1" in text
    assert "#42" in text


async def test_locks_block_management_uses_section_label(monkeypatch) -> None:
    session = AsyncMock()
    lock = _make_lock("management", "presets", moderator_id=1)
    locks_result = MagicMock()
    locks_result.scalars.return_value.all.return_value = [lock]
    session.execute = AsyncMock(return_value=locks_result)

    user = MagicMock()
    user.username = "moderator1"
    monkeypatch.setattr(dashboard, "get_user_by_id", AsyncMock(return_value=user))

    block = await dashboard._build_locks_block(session)

    assert "Пресеты тегов" in block


# ---------------------------------------------------------------------------
# Coalescing / job scheduling tests
# ---------------------------------------------------------------------------


async def test_two_requests_between_ticks_yield_one_render(monkeypatch) -> None:
    """Two request_dashboard() calls followed by one job tick → exactly one edit_message_text."""
    bot = AsyncMock()
    session = AsyncMock()
    session_factory = _FakeSessionFactory(session)

    existing = _make_system_message("general:stats", -100999, 7, {"checksum": "old"})
    monkeypatch.setattr(dashboard, "get_system_message", AsyncMock(return_value=existing))
    monkeypatch.setattr(dashboard, "upsert_system_message", AsyncMock())
    monkeypatch.setattr(
        dashboard, "_build_dashboard_text", AsyncMock(return_value="new dashboard text")
    )

    dashboard.request_dashboard()
    dashboard.request_dashboard()

    await dashboard.dashboard_render_job(bot, session_factory)

    bot.edit_message_text.assert_awaited_once()


async def test_tick_without_dirty_within_interval_makes_no_calls(monkeypatch) -> None:
    bot = AsyncMock()
    session = AsyncMock()
    session_factory = _FakeSessionFactory(session)

    monkeypatch.setattr(dashboard, "get_system_message", AsyncMock())
    monkeypatch.setattr(dashboard, "upsert_system_message", AsyncMock())

    loop_time = 100.0

    class _Loop:
        def time(self) -> float:
            return loop_time

    monkeypatch.setattr(dashboard.asyncio, "get_running_loop", lambda: _Loop())
    monkeypatch.setattr(dashboard, "_last_render_at", loop_time)
    monkeypatch.setattr(dashboard, "_dirty", False)

    await dashboard.dashboard_render_job(bot, session_factory)

    bot.edit_message_text.assert_not_awaited()
    bot.send_message.assert_not_awaited()


async def test_unchanged_checksum_makes_no_telegram_call(monkeypatch) -> None:
    bot = AsyncMock()
    session = AsyncMock()

    text = "same text"
    cs = dashboard._checksum(text)
    existing = _make_system_message("general:stats", -100999, 7, {"checksum": cs})

    monkeypatch.setattr(dashboard, "get_system_message", AsyncMock(return_value=existing))
    monkeypatch.setattr(dashboard, "_build_dashboard_text", AsyncMock(return_value=text))
    monkeypatch.setattr(dashboard, "upsert_system_message", AsyncMock())

    await dashboard._render_dashboard_inner(bot, session)

    bot.edit_message_text.assert_not_awaited()
    bot.send_message.assert_not_awaited()


async def test_message_not_modified_records_checksum(monkeypatch) -> None:
    from aiogram.exceptions import TelegramAPIError

    bot = AsyncMock()
    session = AsyncMock()

    existing = _make_system_message("general:stats", -100999, 7, {"checksum": "old"})
    monkeypatch.setattr(dashboard, "get_system_message", AsyncMock(return_value=existing))
    monkeypatch.setattr(dashboard, "_build_dashboard_text", AsyncMock(return_value="new text"))
    upsert = AsyncMock()
    monkeypatch.setattr(dashboard, "upsert_system_message", upsert)

    api_error = TelegramAPIError(method=MagicMock(), message="message is not modified")
    bot.edit_message_text.side_effect = api_error

    await dashboard._render_dashboard_inner(bot, session)

    upsert.assert_awaited_once()
    assert upsert.call_args.kwargs["payload"]["checksum"] == dashboard._checksum("new text")


async def test_force_self_heal_redraws_locks_block_without_request(monkeypatch) -> None:
    """Lock changes without request_dashboard(); FORCE_RENDER_INTERVAL elapsed → job redraws anyway."""
    bot = AsyncMock()
    session = AsyncMock()
    session_factory = _FakeSessionFactory(session)

    existing = _make_system_message("general:stats", -100999, 7, {"checksum": "old"})
    monkeypatch.setattr(dashboard, "get_system_message", AsyncMock(return_value=existing))
    monkeypatch.setattr(dashboard, "upsert_system_message", AsyncMock())
    monkeypatch.setattr(
        dashboard, "_build_dashboard_text", AsyncMock(return_value="text with new lock")
    )

    loop_time = 1000.0

    class _Loop:
        def time(self) -> float:
            return loop_time

    monkeypatch.setattr(dashboard.asyncio, "get_running_loop", lambda: _Loop())
    monkeypatch.setattr(dashboard, "_last_render_at", loop_time - dashboard._FORCE_RENDER_INTERVAL)
    monkeypatch.setattr(dashboard, "_dirty", False)

    await dashboard.dashboard_render_job(bot, session_factory)

    bot.edit_message_text.assert_awaited_once()


# ---------------------------------------------------------------------------
# Merged legend block (the General topic keeps a single pin)
# ---------------------------------------------------------------------------


async def test_dashboard_text_appends_legend(monkeypatch) -> None:
    session = AsyncMock()
    locks_result = MagicMock()
    locks_result.scalars.return_value.all.return_value = []
    session.execute = AsyncMock(return_value=locks_result)
    _patch_counters(monkeypatch)

    text = await dashboard._build_dashboard_text(session, "ЛЕГЕНДА-БЛОК")

    assert text.index("Сводка предложки") < text.index("ЛЕГЕНДА-БЛОК")


async def test_render_includes_legend_from_status_config(monkeypatch) -> None:
    """The rendered message carries stats and legend together, in one edit."""
    bot = AsyncMock()
    bot.me = AsyncMock(return_value=MagicMock(username="thebot"))
    session = AsyncMock()

    existing = _make_system_message("general:stats", -100999, 7, {"checksum": "old"})
    monkeypatch.setattr(dashboard, "get_system_message", AsyncMock(return_value=existing))
    monkeypatch.setattr(dashboard, "upsert_system_message", AsyncMock())
    monkeypatch.setattr(dashboard, "build_topic_nav_legend", lambda username: f"ЛЕГЕНДА {username}")
    monkeypatch.setattr(dashboard, "_build_dashboard_text", AsyncMock(return_value="stats+legend"))

    await dashboard.render_dashboard(bot, session, force=True)

    assert dashboard._build_dashboard_text.await_args.args[1] == "ЛЕГЕНДА thebot"
    bot.edit_message_text.assert_awaited_once()
