"""A bot command must never be swallowed as content by an FSM text handler.

The bot's own deep-link buttons open the DM with the plain text
``/start review_<id>``. Before this filter existed, whatever text handler matched
the moderator's current state ate it — post #241 was published with the tags
``#Арт | #MoDDyChat | #/start | #review_242`` because the tag wizard's custom-tag
page happened to be open.
"""

from __future__ import annotations

import pytest

from filters.not_command import NotCommand
from handlers.moderator import author_card, ban, edit, management, media, moderators, reject
from handlers import contact, tag_wizard
from states.contact import ContactViewer
from states.moderator import AuthorCard, ModeratorReview
from tests.helpers import make_message

# (module, handler name, the state the handler listens in)
TEXT_HANDLERS = [
    (tag_wizard, "handle_custom_tags_text", ModeratorReview.editing_tags_custom),
    (ban, "handle_ban_reason", ModeratorReview.entering_ban_reason),
    (reject, "handle_reject_reason", ModeratorReview.entering_reject_reason),
    (edit, "handle_edit_caption_text", ModeratorReview.editing_caption),
    (media, "handle_adding_media_unexpected", ModeratorReview.adding_media),
    (management, "handle_add_section_label_input", ModeratorReview.management_add_section_label),
    (management, "handle_edit_section_label_input", ModeratorReview.management_edit_section_label),
    (management, "handle_add_preset_label_input", ModeratorReview.management_add_preset_label),
    (management, "handle_edit_label_input", ModeratorReview.management_edit_preset_label),
    (management, "handle_edit_tag_input", ModeratorReview.management_edit_preset_tag),
    (moderators, "handle_moderator_enter_id_input", ModeratorReview.management_enter_moderator_id),
    (author_card, "handle_note_text", AuthorCard.entering_note),
    (author_card, "handle_ban_reason", AuthorCard.entering_ban_reason),
    (author_card, "handle_contact_text", AuthorCard.writing_direct_message),
    (contact, "handle_moderator_message", ContactViewer.writing_message),
]


async def test_not_command_filter() -> None:
    assert await NotCommand()(make_message(text="Арт MoDDyChat"))
    assert await NotCommand()(make_message(text="ссылка/со/слешем"))
    assert not await NotCommand()(make_message(text="/start review_242"))
    assert not await NotCommand()(make_message(text="/cancel"))


def _find_handler(module, name):
    for handler in module.router.message.handlers:
        if handler.callback.__name__ == name:
            return handler
    raise AssertionError(f"{name} is not registered in {module.__name__}")


@pytest.mark.parametrize(
    "module, name, state",
    TEXT_HANDLERS,
    ids=[f"{m.__name__.rsplit('.', 1)[-1]}.{n}" for m, n, _ in TEXT_HANDLERS],
)
async def test_deep_link_falls_through_to_command_start(module, name, state) -> None:
    """``/start review_242`` must be refused so ``CommandStart`` can open the post."""
    handler = _find_handler(module, name)
    kwargs = {"raw_state": state.state, "db_user": _moderator()}

    accepted, _ = await handler.check(make_message(text="/start review_242"), **kwargs)
    assert not accepted

    accepted, _ = await handler.check(make_message(text="обычный текст"), **kwargs)
    assert accepted


def _moderator():
    from tests.helpers import make_user

    user = make_user()
    user.is_moderator = True
    return user
