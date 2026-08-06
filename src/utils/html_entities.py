"""Helpers for extracting HTML-safe text/caption from aiogram Message objects."""

import html

from aiogram.types import Message
from aiogram.utils.text_decorations import html_decoration


def get_html_caption(message: Message) -> str | None:
    """Extract caption with formatting preserved as HTML."""
    if not message.caption:
        return None
    if message.caption_entities:
        return html_decoration.unparse(message.caption, message.caption_entities)
    return html.escape(message.caption)


def get_html_text(message: Message) -> str:
    """Extract text with formatting preserved as HTML."""
    if message.entities:
        return html_decoration.unparse(message.text, message.entities)
    return html.escape(message.text or "")
