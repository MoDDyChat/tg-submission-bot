"""Приём поста должен переживать любой сбой Telegram: пост коммитится первым."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

import core.messages as msg
from services import submission_intake
from tests.helpers import (
    FakeSessionFactory,
    make_message,
    make_photo_sizes,
    make_submission,
    make_user,
)


async def _no_sleep(_: float) -> None:
    return None


def _make_session(trace: list[str]) -> AsyncMock:
    session = AsyncMock()
    session.commit.side_effect = lambda *args, **kwargs: trace.append("commit")
    session.rollback.side_effect = lambda *args, **kwargs: trace.append("rollback")
    return session


def _patch_intake(monkeypatch, trace: list[str], sub, *, sub_full=None):
    """Замокать всё, что делает приём поста, с записью порядка вызовов в trace."""
    sub_full = sub_full or sub

    monkeypatch.setattr(submission_intake, "create_submission", AsyncMock(return_value=sub))
    monkeypatch.setattr(submission_intake, "add_media", AsyncMock())
    monkeypatch.setattr(
        submission_intake, "get_submission_with_user", AsyncMock(return_value=sub_full)
    )

    def _tracer(name: str, result=None):
        def _fn(*args, **kwargs):
            trace.append(name)
            return result
        return _fn

    mocks = {
        "ensure_user_topic": AsyncMock(return_value=42),
        # Возвращает (media_ids, card_id) — вызывающий держит их для компенсации.
        "post_submission_card": AsyncMock(),
        "request_topic_title_sync": AsyncMock(),
    }
    results = {"post_submission_card": ([], 1)}
    for name, mock in mocks.items():
        mock.side_effect = _tracer(name, results.get(name))
        monkeypatch.setattr(submission_intake.topics, name, mock)

    render_queue = AsyncMock()
    monkeypatch.setattr(submission_intake, "_render_queue", render_queue)
    monkeypatch.setattr(submission_intake, "request_dashboard", lambda: None)
    monkeypatch.setattr(submission_intake, "request_author_card", lambda user_id: None)
    mocks["_render_queue"] = render_queue
    return mocks


def _tracing_answer(trace: list[str], **kwargs) -> AsyncMock:
    return AsyncMock(side_effect=lambda *a, **kw: trace.append("answer"), **kwargs)


# ── порядок: пост закоммичен до Telegram ──────────────────────────

async def test_submit_single_media_commits_before_any_telegram_call(monkeypatch) -> None:
    trace: list[str] = []
    session = _make_session(trace)
    message = make_message(photo=make_photo_sizes(("f1", "u1")))
    message.answer = _tracing_answer(trace)
    db_user = make_user()
    _patch_intake(monkeypatch, trace, make_submission(sub_id=9, user=db_user))

    await submission_intake.submit_single_media(message, session, db_user)

    assert trace[0] == "commit"
    assert trace.index("commit") < trace.index("answer")
    assert trace.index("commit") < trace.index("post_submission_card")


async def test_submit_text_commits_before_any_telegram_call(monkeypatch) -> None:
    trace: list[str] = []
    session = _make_session(trace)
    message = make_message(text="Текст поста")
    message.answer = _tracing_answer(trace)
    db_user = make_user()
    _patch_intake(monkeypatch, trace, make_submission(sub_id=10, user=db_user))

    await submission_intake.submit_text(message, session, db_user)

    assert trace[0] == "commit"
    assert trace.index("commit") < trace.index("answer")
    assert trace.index("commit") < trace.index("post_submission_card")


# ── сбои Telegram не откатывают пост ──────────────────────────────

@pytest.mark.parametrize("failing", ["post_submission_card", "ensure_user_topic"])
async def test_topic_failure_keeps_submission_and_warns_author(monkeypatch, failing) -> None:
    trace: list[str] = []
    session = _make_session(trace)
    message = make_message(photo=make_photo_sizes(("f1", "u1")))
    db_user = make_user()
    mocks = _patch_intake(monkeypatch, trace, make_submission(sub_id=11, user=db_user))
    mocks[failing].side_effect = RuntimeError("telegram down")

    await submission_intake.submit_single_media(message, session, db_user)

    assert trace[0] == "commit"  # пост уже в БД
    assert "rollback" in trace
    assert message.answer.await_args_list[-1].args[0] == msg.SUBMISSION_SEND_ERROR


async def test_title_sync_failure_does_not_warn_author(monkeypatch) -> None:
    trace: list[str] = []
    session = _make_session(trace)
    message = make_message(photo=make_photo_sizes(("f1", "u1")))
    db_user = make_user()
    mocks = _patch_intake(monkeypatch, trace, make_submission(sub_id=12, user=db_user))
    mocks["request_topic_title_sync"].side_effect = RuntimeError("lock timeout")

    await submission_intake.submit_single_media(message, session, db_user)

    # Карточка успела закоммититься, автору про сбой заголовка не сообщаем.
    assert trace.index("post_submission_card") < trace.index("rollback")
    assert trace[trace.index("post_submission_card") + 1] == "commit"
    sent = [call.args[0] for call in message.answer.await_args_list]
    assert msg.SUBMISSION_SEND_ERROR not in sent


async def test_answer_failure_does_not_block_topic_card(monkeypatch) -> None:
    trace: list[str] = []
    session = _make_session(trace)
    message = make_message(photo=make_photo_sizes(("f1", "u1")))
    message.answer = AsyncMock(side_effect=ConnectionError("network is unreachable"))
    db_user = make_user()
    mocks = _patch_intake(monkeypatch, trace, make_submission(sub_id=13, user=db_user))

    await submission_intake.submit_single_media(message, session, db_user)

    mocks["post_submission_card"].assert_awaited_once()


async def test_card_commit_failure_deletes_delivered_block(monkeypatch) -> None:
    """Telegram доставил блок, коммит ID упал → блок удаляем, идём по ветке ошибки."""
    trace: list[str] = []
    session = _make_session(trace)
    message = make_message(photo=make_photo_sizes(("f1", "u1")))
    message.answer = _tracing_answer(trace)
    db_user = make_user()
    mocks = _patch_intake(monkeypatch, trace, make_submission(sub_id=15, user=db_user))
    mocks["post_submission_card"].side_effect = lambda *a, **kw: (
        trace.append("post_submission_card") or ([21, 22], 23)
    )
    cleanup = AsyncMock()
    monkeypatch.setattr(submission_intake.topics, "delete_delivered_card_messages", cleanup)

    commits = 0

    def _commit(*args, **kwargs):
        nonlocal commits
        commits += 1
        trace.append("commit")
        # Падает именно коммит записи ID карточки (третий: пост, тема, карточка).
        if commits == 3:
            raise RuntimeError("lock timeout")

    session.commit.side_effect = _commit

    await submission_intake.submit_single_media(message, session, db_user)

    cleanup.assert_awaited_once_with(message.bot, [21, 22, 23], 15)
    assert "rollback" in trace
    assert message.answer.await_args_list[-1].args[0] == msg.SUBMISSION_SEND_ERROR


# ── медиа-группа идёт по тому же пути ─────────────────────────────

async def test_finalize_media_group_uses_the_same_durable_path(monkeypatch) -> None:
    group_id = "album-durable"
    trace: list[str] = []
    session = _make_session(trace)
    db_user = make_user()
    first = make_message(
        message_id=10,
        caption="Подпись",
        photo=make_photo_sizes(("f1", "u1")),
        media_group_id=group_id,
    )
    first.answer = _tracing_answer(trace)
    submission_intake._media_group_buffers[group_id] = [first]
    submission_intake._media_group_locks[group_id] = asyncio.Lock()
    submission_intake._media_group_timestamps[group_id] = submission_intake.time.monotonic()

    monkeypatch.setattr(submission_intake.asyncio, "sleep", _no_sleep)
    mocks = _patch_intake(monkeypatch, trace, make_submission(sub_id=14, user=db_user))
    mocks["post_submission_card"].side_effect = RuntimeError("telegram down")

    await submission_intake._finalize_media_group(group_id, FakeSessionFactory(session), db_user)

    assert trace[0] == "commit"
    assert "rollback" in trace
    assert first.answer.await_args_list[-1].args[0] == msg.SUBMISSION_SEND_ERROR
