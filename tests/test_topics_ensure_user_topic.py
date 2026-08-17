"""ensure_user_topic не должен держать открытую транзакцию под Telegram-вызовами."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from services import topics
from tests.helpers import make_bot, make_forum_topic, make_user
from db.models import UserTopic


def _session_with_trace(trace: list[str], inserted_id) -> AsyncMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = inserted_id
    session = AsyncMock()
    session.execute.return_value = result
    session.commit.side_effect = lambda *a, **kw: trace.append("commit")
    return session


def _tracing_bot(trace: list[str]):
    bot = make_bot()
    topic = make_forum_topic(77)

    def _create(*a, **kw):
        trace.append("create_forum_topic")
        return topic

    bot.create_forum_topic.side_effect = _create
    bot.delete_forum_topic.side_effect = lambda *a, **kw: trace.append("delete_forum_topic")
    bot.send_message.side_effect = lambda *a, **kw: trace.append("send_message")
    return bot


async def test_ensure_user_topic_commits_row_before_author_card(monkeypatch) -> None:
    trace: list[str] = []
    bot = _tracing_bot(trace)
    session = _session_with_trace(trace, 77)
    monkeypatch.setattr(
        topics,
        "create_author_card_message",
        AsyncMock(side_effect=lambda *a, **kw: trace.append("create_author_card_message")),
    )

    with patch.object(topics, "get_user_topic", AsyncMock(return_value=None)):
        result = await topics.ensure_user_topic(bot, session, make_user(user_id=5))

    assert result == 77
    # INSERT закоммичен до карточки автора — иначе Telegram-вызов идёт под
    # открытой пишущей транзакцией.
    assert trace.index("commit") < trace.index("create_author_card_message")


async def test_ensure_user_topic_commits_before_deleting_duplicate_topic(monkeypatch) -> None:
    """Ветка проигранной гонки: транзакция закрыта до deleteForumTopic."""
    trace: list[str] = []
    bot = _tracing_bot(trace)
    session = _session_with_trace(trace, None)  # INSERT DO NOTHING — гонка проиграна

    existing = UserTopic()
    existing.user_id = 5
    existing.topic_id = 99
    existing.current_status_key = "pending"

    with patch.object(topics, "get_user_topic", AsyncMock(side_effect=[None, existing])):
        result = await topics.ensure_user_topic(bot, session, make_user(user_id=5))

    assert result == 99
    assert trace.index("commit") < trace.index("delete_forum_topic")
