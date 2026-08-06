from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from aiogram import Bot

from db.models import Publication, Submission, SubmissionMedia, TagPresetEntry, TagPresetSection, User


class _SessionContextManager:
    def __init__(self, session) -> None:
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class FakeSessionFactory:
    def __init__(self, *sessions) -> None:
        self._sessions = list(sessions)
        self.calls = 0

    def __call__(self):
        if not self._sessions:
            raise AssertionError("FakeSessionFactory was called without configured sessions")
        index = min(self.calls, len(self._sessions) - 1)
        self.calls += 1
        return _SessionContextManager(self._sessions[index])


class FakeState:
    def __init__(self, data: dict | None = None) -> None:
        self.data = dict(data or {})
        self.state = None
        self.cleared = False

    async def get_data(self) -> dict:
        return dict(self.data)

    async def update_data(self, **kwargs) -> None:
        self.data.update(kwargs)

    async def set_state(self, state) -> None:
        self.state = state

    async def clear(self) -> None:
        self.data.clear()
        self.state = None
        self.cleared = True

    async def get_state(self) -> str | None:
        return self.state


def make_user(
    *,
    user_id: int = 1,
    telegram_id: int = 101,
    username: str | None = "artist",
    full_name: str = "Artist Name",
    is_banned: bool = False,
    ban_reason: str | None = None,
) -> User:
    return User(
        id=user_id,
        telegram_id=telegram_id,
        username=username,
        full_name=full_name,
        is_banned=is_banned,
        ban_reason=ban_reason,
    )


def make_media(
    *,
    media_id: int = 1,
    submission_id: int = 1,
    file_id: str = "file-1",
    file_unique_id: str = "unique-1",
    media_type: str = "photo",
    sort_order: int = 0,
) -> SubmissionMedia:
    return SubmissionMedia(
        id=media_id,
        submission_id=submission_id,
        file_id=file_id,
        file_unique_id=file_unique_id,
        media_type=media_type,
        sort_order=sort_order,
    )


def make_submission(
    *,
    sub_id: int = 1,
    user: User | None = None,
    caption: str | None = "Caption",
    tags: list[str] | None = None,
    status: str = "pending",
    media: list[SubmissionMedia] | None = None,
    topic_card_message_id: int | None = None,
    topic_media_message_ids: list[int] | None = None,
) -> Submission:
    user = user or make_user()
    submission = Submission(
        id=sub_id,
        user_id=user.id,
        caption=caption,
        tags=tags or [],
        status=status,
        topic_card_message_id=topic_card_message_id,
        topic_media_message_ids=topic_media_message_ids,
    )
    submission.user = user
    submission.media = list(media or [])
    return submission


def make_publication(
    *,
    pub_id: int = 1,
    submission_id: int = 1,
    publish_at: datetime | None = None,
    published_at: datetime | None = None,
    edited_caption: str | None = None,
    channel_message_id: int | None = None,
    channel_message_ids: list[int] | None = None,
) -> Publication:
    return Publication(
        id=pub_id,
        submission_id=submission_id,
        publish_at=publish_at or datetime(2026, 4, 15, 12, 0, tzinfo=timezone.utc),
        published_at=published_at,
        edited_caption=edited_caption,
        channel_message_id=channel_message_id,
        channel_message_ids=channel_message_ids,
    )


def make_section(
    *,
    key: str = "category",
    label: str = "Категория",
    columns: int = 3,
    sort_order: int = 0,
) -> TagPresetSection:
    return TagPresetSection(key=key, label=label, columns=columns, sort_order=sort_order)


def make_preset(
    *,
    preset_id: int = 1,
    preset_type: str = "category",
    label: str = "Арт",
    tag: str = "MineShieldArt",
    sort_order: int = 0,
) -> TagPresetEntry:
    return TagPresetEntry(
        id=preset_id,
        preset_type=preset_type,
        label=label,
        tag=tag,
        sort_order=sort_order,
    )


def make_sent_message(message_id: int = 900, *, chat_id: int = 1, text: str | None = None):
    return SimpleNamespace(message_id=message_id, chat=SimpleNamespace(id=chat_id), text=text)


def make_forum_topic(message_thread_id: int = 42):
    return SimpleNamespace(message_thread_id=message_thread_id)


def make_bot() -> MagicMock:
    """Return a spec'd mock of aiogram Bot; typos in method names will raise AttributeError."""
    me_user = SimpleNamespace(username="testbot")
    bot: MagicMock = AsyncMock(spec=Bot)
    bot.send_message.return_value = make_sent_message(1001)
    bot.send_photo.return_value = make_sent_message(1002)
    bot.send_video.return_value = make_sent_message(1003)
    bot.send_animation.return_value = make_sent_message(1004)
    bot.send_document.return_value = make_sent_message(1005)
    bot.send_media_group.return_value = [make_sent_message(1101), make_sent_message(1102)]
    bot.edit_message_text.return_value = None
    bot.edit_message_reply_markup.return_value = None
    bot.delete_message.return_value = None
    bot.get_chat.return_value = SimpleNamespace(pinned_message=None)
    bot.pin_chat_message.return_value = None
    bot.create_forum_topic.return_value = make_forum_topic(42)
    bot.edit_forum_topic.return_value = None
    bot.me.return_value = me_user
    return bot


def make_photo_sizes(*pairs: tuple[str, str]):
    return [
        SimpleNamespace(file_id=file_id, file_unique_id=file_unique_id)
        for file_id, file_unique_id in pairs
    ]


def make_message(
    *,
    message_id: int = 1,
    chat_id: int = 10,
    from_user_id: int = 20,
    bot=None,
    text: str | None = None,
    caption: str | None = None,
    caption_entities=None,
    entities=None,
    photo=None,
    video=None,
    animation=None,
    document=None,
    media_group_id: str | None = None,
):
    bot = bot or make_bot()
    message = SimpleNamespace(
        message_id=message_id,
        chat=SimpleNamespace(id=chat_id),
        from_user=SimpleNamespace(id=from_user_id),
        bot=bot,
        text=text,
        caption=caption,
        caption_entities=caption_entities,
        entities=entities,
        photo=photo,
        video=video,
        animation=animation,
        document=document,
        media_group_id=media_group_id,
        answer=AsyncMock(return_value=make_sent_message(message_id + 100, chat_id=chat_id)),
        answer_photo=AsyncMock(return_value=make_sent_message(message_id + 101, chat_id=chat_id)),
        answer_video=AsyncMock(return_value=make_sent_message(message_id + 102, chat_id=chat_id)),
        answer_animation=AsyncMock(return_value=make_sent_message(message_id + 103, chat_id=chat_id)),
        answer_document=AsyncMock(return_value=make_sent_message(message_id + 104, chat_id=chat_id)),
        answer_media_group=AsyncMock(
            return_value=[
                make_sent_message(message_id + 105, chat_id=chat_id),
                make_sent_message(message_id + 106, chat_id=chat_id),
            ]
        ),
        delete=AsyncMock(),
        edit_text=AsyncMock(),
        edit_reply_markup=AsyncMock(),
    )
    return message


def make_callback(*, message=None, bot=None, from_user_id: int = 20):
    message = message or make_message(bot=bot)
    bot = bot or message.bot
    return SimpleNamespace(
        bot=bot,
        message=message,
        from_user=SimpleNamespace(id=from_user_id),
        answer=AsyncMock(),
    )
