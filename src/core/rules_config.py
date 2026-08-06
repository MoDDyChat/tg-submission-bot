"""Viewer rules / welcome text configuration.

The text shown to viewers on ``/start`` (the "rules" / how-to-submit message) is
instance-specific: it mentions the publication channel, fandom name and links.
To let multiple bot instances share one codebase with different texts, the
message is loaded from a plain-text file whose path is configurable via
``config.rules_path`` (env ``RULES_PATH``, default ``config/rules.txt``).

The file content is treated as HTML (sent with ``parse_mode="HTML"``). Falls back
to the built-in :data:`core.messages.RULES` if the file is missing or empty.

Usage::

    from core.rules_config import get_rules

    await message.answer(get_rules(), parse_mode="HTML")
"""

from __future__ import annotations

from pathlib import Path

from core.logging import get_logger

logger = get_logger(__name__)

# Module-level singleton — initialised in ``core/bot.py`` via ``_init`` once
# settings are loaded. Stays ``None`` until then (lazy fallback in get_rules).
_rules_text: str | None = None


def _load(path: str | Path | None) -> str:
    """Return the rules text from *path*, falling back to ``messages.RULES``."""
    from core.messages import RULES as _default

    if path:
        p = Path(path)
        if p.is_file():
            raw = p.read_text(encoding="utf-8")
            # The file may use real line breaks OR literal ``\n`` escapes (handy
            # when copy-pasting from the Python source). Normalise the latter.
            text = raw.replace("\\n", "\n").strip("\n")
            if text:
                logger.info("Текст правил загружен из %s", p)
                return text
            logger.warning("Файл правил %s пуст — используем встроенный текст", p)
        else:
            logger.warning("Файл правил %s не найден — используем встроенный текст", path)
    return _default


def _init(path: str | None = None) -> None:
    """Initialise the module singleton. Called once at application start."""
    global _rules_text
    _rules_text = _load(path)


def get_rules() -> str:
    """Return the viewer rules text. Initialises with defaults if ``_init`` was
    not called yet."""
    if _rules_text is None:
        _init()
    assert _rules_text is not None
    return _rules_text
