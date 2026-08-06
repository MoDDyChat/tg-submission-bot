from __future__ import annotations

from utils import media


def test_empty_list() -> None:
    assert media.validate_media_group_composition([]) is True


def test_single_photo() -> None:
    assert media.validate_media_group_composition(["photo"]) is True


def test_single_animation() -> None:
    assert media.validate_media_group_composition(["animation"]) is True


def test_single_document() -> None:
    assert media.validate_media_group_composition(["document"]) is True


def test_photo_and_video_mixed() -> None:
    assert media.validate_media_group_composition(["photo", "video"]) is True


def test_video_photo_photo() -> None:
    assert media.validate_media_group_composition(["video", "photo", "photo"]) is True


def test_multi_document() -> None:
    assert media.validate_media_group_composition(["document", "document"]) is True


def test_photo_with_document_invalid() -> None:
    assert media.validate_media_group_composition(["photo", "document"]) is False


def test_photo_with_animation_invalid() -> None:
    assert media.validate_media_group_composition(["photo", "animation"]) is False


def test_document_with_video_invalid() -> None:
    assert media.validate_media_group_composition(["document", "video"]) is False


def test_multi_animation_invalid() -> None:
    assert media.validate_media_group_composition(["animation", "animation"]) is False
