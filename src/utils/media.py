from aiogram.types import Message


def extract_media_info(message: Message) -> tuple[str, str, str] | None:
    """Extract (file_id, file_unique_id, media_type) from a message.

    Returns None if the message contains no supported media.
    """
    if message.photo:
        largest = message.photo[-1]
        return largest.file_id, largest.file_unique_id, "photo"
    if message.video:
        return message.video.file_id, message.video.file_unique_id, "video"
    if message.animation:
        return message.animation.file_id, message.animation.file_unique_id, "animation"
    if message.document:
        return message.document.file_id, message.document.file_unique_id, "document"
    return None


def has_supported_media(message: Message) -> bool:
    return extract_media_info(message) is not None


def validate_media_group_composition(media_types: list[str]) -> bool:
    """True if these media types can coexist in one submission/Telegram group.

    Telegram rules: photo+video mix freely; documents only with documents;
    animation (GIF) cannot share a group — allowed only as the single item.
    A set of 0 or 1 item is always valid.
    """
    if len(media_types) <= 1:
        return True
    kinds = set(media_types)
    if kinds <= {"photo", "video"}:
        return True
    if kinds == {"document"}:
        return True
    return False
