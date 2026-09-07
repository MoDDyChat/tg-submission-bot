import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from services import topics_queue


@pytest.fixture(autouse=True)
def reset_queue_render_state(monkeypatch) -> None:
    monkeypatch.setattr(topics_queue, "_dirty", False)
    monkeypatch.setattr(topics_queue, "_last_render_at", 0.0)


def test_request_queue_render_only_marks_dirty() -> None:
    topics_queue.request_queue_render()

    assert topics_queue._dirty is True


async def test_repeated_requests_between_ticks_render_once(monkeypatch) -> None:
    render_inner = AsyncMock()
    monkeypatch.setattr(topics_queue, "_render_queue_inner", render_inner)
    # Время последнего рендера задаётся явно: _last_render_at по умолчанию 0.0,
    # а loop.time() — монотонные часы хоста, так что на свежезагруженной машине
    # (CI-раннер) тик оказался бы force-рендером и тест проверял бы не тот путь.
    monkeypatch.setattr(topics_queue, "_last_render_at", asyncio.get_running_loop().time())
    bot = Mock()
    session = AsyncMock()

    for _ in range(5):
        topics_queue.request_queue_render()
    await topics_queue.render_queue_tick(bot, session)
    await topics_queue.render_queue_tick(bot, session)

    render_inner.assert_awaited_once_with(bot, session, force_reconcile=False)
    bot.assert_not_called()


async def test_dirty_flag_is_cleared_before_render_and_mid_render_event_is_kept(
    monkeypatch,
) -> None:
    render_inner = AsyncMock()

    async def render_and_request(*args, **kwargs) -> None:
        assert topics_queue._dirty is False
        topics_queue.request_queue_render()

    render_inner.side_effect = render_and_request
    monkeypatch.setattr(topics_queue, "_render_queue_inner", render_inner)
    bot = Mock()
    session = AsyncMock()

    topics_queue.request_queue_render()
    await topics_queue.render_queue_tick(bot, session)
    await topics_queue.render_queue_tick(bot, session)

    assert render_inner.await_count == 2


async def test_clean_tick_skips_until_force_render_interval(monkeypatch) -> None:
    render_inner = AsyncMock()
    monkeypatch.setattr(topics_queue, "_render_queue_inner", render_inner)
    loop = asyncio.get_running_loop()
    bot = Mock()
    session = AsyncMock()

    monkeypatch.setattr(topics_queue, "_last_render_at", loop.time())
    await topics_queue.render_queue_tick(bot, session)
    assert render_inner.await_count == 0

    monkeypatch.setattr(
        topics_queue,
        "_last_render_at",
        loop.time() - topics_queue._FORCE_RENDER_INTERVAL - 1,
    )
    await topics_queue.render_queue_tick(bot, session)

    assert render_inner.await_count == 1
