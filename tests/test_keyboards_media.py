"""Tests for media-related keyboards, callbacks, and formatting."""

from keyboards.callbacks import MediaCB
from keyboards.moderator import media_manager_kb, submission_actions_kb
from tests.helpers import make_media
from utils.formatting import format_media_manager_text


class TestSubmissionActionsKb:
    def test_contains_edit_media_button(self):
        kb = submission_actions_kb(1, "pending")
        found = False
        for row in kb.inline_keyboard:
            for btn in row:
                if btn.callback_data and "edit_media" in btn.callback_data:
                    found = True
                    break
        assert found, "edit_media button not found in submission_actions_kb"


class TestMediaManagerKb:
    def test_structure(self):
        media = [
            make_media(media_id=1),
            make_media(media_id=2, media_type="video"),
        ]
        kb = media_manager_kb(7, media)
        rows = kb.inline_keyboard
        # 2 delete rows + 1 add row + 1 done row = 4 rows
        assert len(rows) == 4, f"Expected 4 rows, got {len(rows)}"

        # First row: delete for media_id=1
        cb = MediaCB.unpack(rows[0][0].callback_data)
        assert cb.action == "delete"
        assert cb.sub_id == 7
        assert cb.media_id == 1

        # Second row: delete for media_id=2
        cb2 = MediaCB.unpack(rows[1][0].callback_data)
        assert cb2.action == "delete"
        assert cb2.sub_id == 7
        assert cb2.media_id == 2

        # Third row: add
        cb3 = MediaCB.unpack(rows[2][0].callback_data)
        assert cb3.action == "add"
        assert cb3.sub_id == 7

        # Fourth row: done
        cb4 = MediaCB.unpack(rows[3][0].callback_data)
        assert cb4.action == "done"
        assert cb4.sub_id == 7


class TestFormatMediaManagerText:
    def test_format(self):
        media = [
            make_media(media_id=1),
            make_media(media_id=2, media_type="video"),
        ]
        text = format_media_manager_text(7, media)
        assert "#7" in text
        assert "1." in text
        assert "2." in text
