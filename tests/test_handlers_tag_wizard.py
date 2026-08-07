from __future__ import annotations

from unittest.mock import AsyncMock


import core.messages as msg
from handlers.moderator import _helpers as moderator_helpers
from handlers import tag_wizard
from keyboards.callbacks import SubmissionCB
from states.moderator import ModeratorReview
from tests.helpers import (
    FakeState,
    make_callback,
    make_media,
    make_message,
    make_preset,
    make_section,
    make_sent_message,
    make_submission,
    make_user,
)


class FakeBadRequest(Exception):
    pass


def _wizard_data():
    category = make_section(key="category", label="Категория & тип", columns=2, sort_order=1)
    character = make_section(key="character", label="Персонаж", columns=3, sort_order=2)
    grouped = {
        "category": [make_preset(preset_id=1, preset_type="category", label="Арт", tag="MineShieldArt")],
        "character": [make_preset(preset_id=2, preset_type="character", label="Неркин", tag="Nerkin")],
    }
    return [category, character], grouped


def test_normalize_selected_by_section_filters_invalid_values() -> None:
    normalized = tag_wizard._normalize_selected_by_section(
        {
            "category": ["MineShieldArt", 1],
            "character": "Nerkin",
            1: ["bad"],
        }
    )

    assert normalized == {"category": ["MineShieldArt"]}


def test_sync_wizard_data_moves_removed_sections_and_tags_to_custom() -> None:
    sections, grouped = _wizard_data()
    selected, custom, page_index = tag_wizard._sync_wizard_data(
        {
            "wizard_sections": {
                "removed": ["Legacy"],
                "category": ["MineShieldArt", "GhostTag"],
            },
            "wizard_custom": ["Custom"],
            "wizard_page_index": 100,
        },
        sections[:1],
        {"category": grouped["category"]},
    )

    assert selected == {"category": ["MineShieldArt"]}
    assert custom == ["Custom", "Legacy", "GhostTag"]
    assert page_index == 1


def test_detect_existing_presets_splits_known_and_custom_tags() -> None:
    sections, grouped = _wizard_data()

    selected, custom = tag_wizard._detect_existing_presets(
        ["MineShieldArt", "Custom"],
        sections,
        grouped,
    )

    assert selected == {"category": ["MineShieldArt"], "character": []}
    assert custom == ["Custom"]


def test_build_wizard_view_returns_preset_page() -> None:
    sections, grouped = _wizard_data()
    state, text, keyboard, selected, custom, page_index = tag_wizard._build_wizard_view(
        {
            "wizard_caption": "Описание",
            "wizard_sections": {"category": ["MineShieldArt"], "character": []},
            "wizard_custom": ["Custom"],
            "wizard_page_index": 0,
        },
        sections,
        grouped,
    )

    assert state == ModeratorReview.editing_tags_presets
    assert "Категория &amp; тип" in text
    assert "#MineShieldArt | #Custom" in text
    assert keyboard.inline_keyboard[-1][-1].text == "Далее →"
    assert selected["category"] == ["MineShieldArt"]
    assert custom == ["Custom"]
    assert page_index == 0


def test_build_wizard_view_returns_custom_page_after_last_section() -> None:
    sections, grouped = _wizard_data()
    state, text, keyboard, *_ = tag_wizard._build_wizard_view(
        {
            "wizard_caption": "Описание",
            "wizard_sections": {"category": [], "character": []},
            "wizard_custom": ["Custom"],
            "wizard_page_index": len(sections),
        },
        sections,
        grouped,
    )

    assert state == ModeratorReview.editing_tags_custom
    assert msg.TAGS_WIZARD_CUSTOM in text
    assert keyboard.inline_keyboard[-1][0].text == "Завершить редактирование"


def test_build_wizard_view_custom_page_shows_suggested_hint() -> None:
    sections, grouped = _wizard_data()
    state, text, keyboard, *_ = tag_wizard._build_wizard_view(
        {
            "wizard_caption": "Описание",
            "wizard_sections": {"category": [], "character": []},
            "wizard_custom": [],
            "wizard_suggested_unknown": ["тег & <script>", "тег2"],
            "wizard_page_index": len(sections),
        },
        sections,
        grouped,
    )

    assert state == ModeratorReview.editing_tags_custom
    assert msg.TAGS_WIZARD_CUSTOM in text
    assert "💡 Автор предлагал теги:" in text
    assert "#тег &amp; &lt;script&gt;, #тег2" in text
    assert "<script>" not in text
    assert keyboard.inline_keyboard[-1][0].text == "Завершить редактирование"


async def test_edit_tags_start_prefills_sections_and_unknown_from_suggested(monkeypatch) -> None:
    sections, grouped = _wizard_data()
    sub = make_submission(sub_id=5, status="pending", caption="Описание", media=[])
    sub.suggested_tags = [
        {"raw": "art", "tag": "MineShieldArt", "exact": True},
        {"raw": "неизвестный", "tag": None, "exact": False},
    ]
    callback = make_callback(message=make_message())
    state = FakeState({})
    db_user = make_user()

    monkeypatch.setattr(tag_wizard, "_load_presets", AsyncMock(return_value=(sections, grouped)))
    monkeypatch.setattr(tag_wizard, "get_submission_with_user", AsyncMock(return_value=sub))
    monkeypatch.setattr(tag_wizard, "_render_wizard_message", AsyncMock())
    monkeypatch.setattr(tag_wizard.edit_lock, "extend_lock", AsyncMock(return_value=True))

    await tag_wizard.handle_edit_tags_start(
        callback, SubmissionCB(action="edit_tags", sub_id=5), AsyncMock(), state, db_user
    )

    assert state.data["wizard_sections"] == {"category": ["MineShieldArt"], "character": []}
    assert state.data["wizard_custom"] == []
    assert state.data["wizard_suggested_unknown"] == ["неизвестный"]
    assert state.data["wizard_page_index"] == 0


async def test_edit_tags_start_no_prefill_when_tags_present(monkeypatch) -> None:
    sections, grouped = _wizard_data()
    sub = make_submission(sub_id=5, status="pending", caption="Описание", media=[], tags=["Custom"])
    sub.suggested_tags = [
        {"raw": "art", "tag": "MineShieldArt", "exact": True},
        {"raw": "неизвестный", "tag": None, "exact": False},
    ]
    callback = make_callback(message=make_message())
    state = FakeState({})
    db_user = make_user()

    monkeypatch.setattr(tag_wizard, "_load_presets", AsyncMock(return_value=(sections, grouped)))
    monkeypatch.setattr(tag_wizard, "get_submission_with_user", AsyncMock(return_value=sub))
    monkeypatch.setattr(tag_wizard, "_render_wizard_message", AsyncMock())
    monkeypatch.setattr(tag_wizard.edit_lock, "extend_lock", AsyncMock(return_value=True))

    await tag_wizard.handle_edit_tags_start(
        callback, SubmissionCB(action="edit_tags", sub_id=5), AsyncMock(), state, db_user
    )

    assert state.data["wizard_sections"] == {"category": [], "character": []}
    assert state.data["wizard_custom"] == ["Custom"]
    assert state.data["wizard_suggested_unknown"] == []


async def test_edit_tags_start_clears_stale_suggested_unknown(monkeypatch) -> None:
    sections, grouped = _wizard_data()
    sub = make_submission(sub_id=5, status="pending", caption="Описание", media=[])
    callback = make_callback(message=make_message())
    state = FakeState({"wizard_suggested_unknown": ["stale"]})
    db_user = make_user()

    monkeypatch.setattr(tag_wizard, "_load_presets", AsyncMock(return_value=(sections, grouped)))
    monkeypatch.setattr(tag_wizard, "get_submission_with_user", AsyncMock(return_value=sub))
    monkeypatch.setattr(tag_wizard, "_render_wizard_message", AsyncMock())
    monkeypatch.setattr(tag_wizard.edit_lock, "extend_lock", AsyncMock(return_value=True))

    await tag_wizard.handle_edit_tags_start(
        callback, SubmissionCB(action="edit_tags", sub_id=5), AsyncMock(), state, db_user
    )

    assert state.data["wizard_suggested_unknown"] == []


async def test_render_wizard_message_edits_existing_message(monkeypatch) -> None:
    sections, grouped = _wizard_data()
    state = FakeState({
        "wizard_caption": "Описание",
        "wizard_sections": {"category": [], "character": []},
        "wizard_custom": [],
        "wizard_page_index": 0,
        "wizard_message_id": 55,
    })
    bot = make_message().bot

    monkeypatch.setattr(tag_wizard, "_load_presets", AsyncMock(return_value=(sections, grouped)))

    message_id = await tag_wizard._render_wizard_message(bot, 99, state, AsyncMock(), message_id=55)

    assert message_id == 55
    assert state.state == ModeratorReview.editing_tags_presets
    assert state.data["wizard_message_id"] == 55
    bot.edit_message_text.assert_awaited_once()


async def test_render_wizard_message_keeps_existing_message_on_not_modified(monkeypatch) -> None:
    sections, grouped = _wizard_data()
    state = FakeState({
        "wizard_caption": "Описание",
        "wizard_sections": {"category": [], "character": []},
        "wizard_custom": [],
        "wizard_page_index": 0,
        "wizard_message_id": 55,
    })
    bot = make_message().bot

    monkeypatch.setattr(tag_wizard, "_load_presets", AsyncMock(return_value=(sections, grouped)))
    monkeypatch.setattr(tag_wizard, "TelegramBadRequest", FakeBadRequest)
    bot.edit_message_text.side_effect = FakeBadRequest("message is not modified")

    message_id = await tag_wizard._render_wizard_message(bot, 99, state, AsyncMock(), message_id=55)

    assert message_id == 55
    bot.send_message.assert_not_awaited()


async def test_render_wizard_message_sends_new_message_when_original_missing(monkeypatch) -> None:
    sections, grouped = _wizard_data()
    state = FakeState({
        "wizard_caption": "Описание",
        "wizard_sections": {"category": [], "character": []},
        "wizard_custom": [],
        "wizard_page_index": 0,
        "wizard_message_id": 55,
    })
    bot = make_message().bot
    bot.send_message.return_value = make_sent_message(99, chat_id=77)

    monkeypatch.setattr(tag_wizard, "_load_presets", AsyncMock(return_value=(sections, grouped)))
    monkeypatch.setattr(tag_wizard, "TelegramBadRequest", FakeBadRequest)
    bot.edit_message_text.side_effect = FakeBadRequest("message to edit not found")

    message_id = await tag_wizard._render_wizard_message(bot, 77, state, AsyncMock(), message_id=55)

    assert message_id == 99
    assert state.data["wizard_message_id"] == 99
    bot.send_message.assert_awaited_once()


async def test_handle_custom_tags_text_appends_unique_tags_and_rerenders(monkeypatch) -> None:
    rerender = AsyncMock()
    monkeypatch.setattr(tag_wizard, "_render_wizard_message", rerender)
    monkeypatch.setattr(tag_wizard, "_extend_lock_from_state", AsyncMock(return_value=True))

    state = FakeState({"sub_id": 1, "wizard_custom": ["existing"]})
    message = make_message(text="#new existing")
    db_user = make_user()

    await tag_wizard.handle_custom_tags_text(message, AsyncMock(), state, db_user)

    assert state.data["wizard_custom"] == ["existing", "new"]
    message.delete.assert_awaited_once()
    rerender.assert_awaited_once()


async def test_handle_custom_finish_shows_alert_when_caption_is_too_long(monkeypatch) -> None:
    sections, grouped = _wizard_data()
    state = FakeState({
        "sub_id": 5,
        "wizard_sections": {"category": ["MineShieldArt"], "character": []},
        "wizard_custom": ["Custom"],
        "wizard_page_index": 0,
    })
    sub = make_submission(sub_id=5, status="pending", caption="\u041eписание", media=[make_media()])
    callback = make_callback(message=make_message())
    db_user = make_user()
    update_tags = AsyncMock()

    monkeypatch.setattr(tag_wizard, "_load_presets", AsyncMock(return_value=(sections, grouped)))
    monkeypatch.setattr(tag_wizard, "get_submission_with_user", AsyncMock(return_value=sub))
    monkeypatch.setattr(tag_wizard, "update_submission_tags", update_tags)
    monkeypatch.setattr(tag_wizard, "validate_caption_length", lambda *args, **kwargs: False)
    monkeypatch.setattr(tag_wizard, "compose_caption", lambda *args, **kwargs: "x" * 1030)
    monkeypatch.setattr(tag_wizard, "strip_html_for_length", lambda text: text)
    monkeypatch.setattr(tag_wizard.edit_lock, "extend_lock", AsyncMock(return_value=True))

    await tag_wizard.handle_custom_finish(callback, AsyncMock(), state, db_user)

    callback.answer.assert_awaited_once_with(
        msg.TAGS_TOO_LONG.format(length=1030, max_length=tag_wizard.MAX_MEDIA_CAPTION),
        show_alert=True,
    )
    update_tags.assert_not_awaited()


async def test_handle_custom_finish_updates_tags_and_refreshes_preview(monkeypatch) -> None:
    sections, grouped = _wizard_data()
    session = AsyncMock()
    state = FakeState({
        "sub_id": 5,
        "wizard_sections": {"category": ["MineShieldArt"], "character": []},
        "wizard_custom": ["Custom"],
        "wizard_page_index": 0,
    })
    sub = make_submission(sub_id=5, status="pending", caption="Описание", media=[])
    callback = make_callback(message=make_message())
    db_user = make_user()
    update_tags = AsyncMock()
    send_view = AsyncMock(return_value=True)

    monkeypatch.setattr(tag_wizard, "_load_presets", AsyncMock(return_value=(sections, grouped)))
    monkeypatch.setattr(tag_wizard, "get_submission_with_user", AsyncMock(side_effect=[sub, sub]))
    monkeypatch.setattr(tag_wizard, "update_submission_tags", update_tags)
    monkeypatch.setattr(tag_wizard.edit_lock, "extend_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(tag_wizard.topic_notifications, "notify_tags_changed", AsyncMock())
    monkeypatch.setattr(tag_wizard.topics, "update_submission_card", AsyncMock())
    monkeypatch.setattr(tag_wizard.topics, "request_topic_title_sync", AsyncMock())
    monkeypatch.setattr(moderator_helpers, "_send_submission_view", send_view)

    await tag_wizard.handle_custom_finish(callback, session, state, db_user)

    update_tags.assert_awaited_once_with(session, 5, ["MineShieldArt", "Custom"])
    send_view.assert_awaited_once()
    tag_wizard.topics.update_submission_card.assert_awaited_once()
    callback.answer.assert_awaited_once_with(msg.TAGS_UPDATED)


async def test_handle_custom_finish_unchanged_tags_skip_notification(monkeypatch) -> None:
    """Same tag set as before: no DB write, no topic notification, no repaint."""
    sections, grouped = _wizard_data()
    session = AsyncMock()
    state = FakeState({
        "sub_id": 5,
        "wizard_sections": {"category": ["MineShieldArt"], "character": []},
        "wizard_custom": ["Custom"],
        "wizard_page_index": 0,
    })
    sub = make_submission(
        sub_id=5, status="pending", caption="Описание", media=[],
        tags=["MineShieldArt", "Custom"],
    )
    callback = make_callback(message=make_message())
    db_user = make_user()
    update_tags = AsyncMock()
    send_view = AsyncMock(return_value=True)
    notify = AsyncMock()
    update_card = AsyncMock()
    title_sync = AsyncMock()

    monkeypatch.setattr(tag_wizard, "_load_presets", AsyncMock(return_value=(sections, grouped)))
    monkeypatch.setattr(tag_wizard, "get_submission_with_user", AsyncMock(side_effect=[sub, sub]))
    monkeypatch.setattr(tag_wizard, "update_submission_tags", update_tags)
    monkeypatch.setattr(tag_wizard.edit_lock, "extend_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(tag_wizard.topic_notifications, "notify_tags_changed", notify)
    monkeypatch.setattr(tag_wizard.topics, "update_submission_card", update_card)
    monkeypatch.setattr(tag_wizard.topics, "request_topic_title_sync", title_sync)
    monkeypatch.setattr(moderator_helpers, "_send_submission_view", send_view)

    await tag_wizard.handle_custom_finish(callback, session, state, db_user)

    update_tags.assert_not_awaited()
    notify.assert_not_awaited()
    update_card.assert_not_awaited()
    title_sync.assert_not_awaited()
    send_view.assert_awaited_once()
    # The lock extension is a write — it must still be committed before Telegram calls
    session.commit.assert_awaited()
    callback.answer.assert_awaited_once_with(msg.TAGS_UPDATED)


# ---------------------------------------------------------------------------
# Lock-guard tests for tag_wizard callbacks
# ---------------------------------------------------------------------------

async def test_handle_toggle_preset_lock_lost_clears_fsm(monkeypatch) -> None:
    """When lock is lost, toggle_preset clears FSM and shows alert."""
    from keyboards.callbacks import TagWizardCB

    state = FakeState({"sub_id": 5})
    state.state = ModeratorReview.editing_tags_presets.state
    callback = make_callback(message=make_message())
    session = AsyncMock()
    db_user = make_user()

    monkeypatch.setattr(
        tag_wizard,
        "_extend_lock_from_state",
        AsyncMock(return_value=False),
    )

    cb_data = TagWizardCB(action="toggle", value="TestTag")
    await tag_wizard.handle_toggle_preset(callback, cb_data, session, state, db_user)

    assert state.cleared is True
    callback.answer.assert_awaited_once_with(msg.MODERATOR_LOCK_LOST, show_alert=True)


async def test_handle_custom_tags_text_lock_lost_clears_fsm(monkeypatch) -> None:
    """When lock is lost, custom tags text handler clears FSM and notifies."""
    state = FakeState({"sub_id": 5})
    state.state = ModeratorReview.editing_tags_custom.state
    message = make_message(text="#tag")
    session = AsyncMock()
    db_user = make_user()

    monkeypatch.setattr(
        tag_wizard,
        "_extend_lock_from_state",
        AsyncMock(return_value=False),
    )

    await tag_wizard.handle_custom_tags_text(message, session, state, db_user)

    assert state.cleared is True
    message.answer.assert_awaited_once_with(msg.MODERATOR_LOCK_LOST)
