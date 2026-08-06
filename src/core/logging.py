"""Centralized logging configuration for the submission bot."""

import logging
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
LOG_FILE = LOG_DIR / "submission_bot.log"

_ROOT_LOGGER_NAME = "submission_bot"

# Libraries that spam at INFO level
_NOISY_LOGGERS = (
    "aiogram",
    "aiohttp",
    "apscheduler",
    "sqlalchemy.engine",
)

# Mapping: logger suffix → short display name (for log output)
_SHORT_NAMES: dict[str, str] = {
    "core.bot": "Bot",
    "db.session": "Database",
    "services.scheduler": "Scheduler",
    "services.publisher": "Publisher",
    "handlers.moderator": "Moderator",
    "handlers.viewer": "Viewer",
    "handlers.contact": "Contact",
    "handlers.common": "Common",
    "middlewares.auth": "Auth",
}

_TOKEN_RE = re.compile(r"bot\d{8,10}:[A-Za-z0-9_-]{35,}")


class _ShortNameFilter(logging.Filter):
    """Inject ``short_name`` attribute into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        full = record.name  # e.g. "submission_bot.handlers.viewer"
        suffix = full.removeprefix(f"{_ROOT_LOGGER_NAME}.")
        record.short_name = _SHORT_NAMES.get(suffix, suffix.rsplit(".", 1)[-1].capitalize())  # type: ignore[attr-defined]
        return True


class _TokenMaskFilter(logging.Filter):
    """Mask Telegram bot tokens in log messages to prevent secret leakage."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _TOKEN_RE.sub("bot***:***", record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: _TOKEN_RE.sub("bot***:***", v) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    _TOKEN_RE.sub("bot***:***", a) if isinstance(a, str) else a
                    for a in record.args
                )
        return True


def fmt_user(db_user) -> str:
    """Format a DB User object for log messages.

    Returns ``[id:42 (@username)]`` or ``[id:42 (Full Name)]``.
    """
    if db_user.username:
        return f"[id:{db_user.id} (@{db_user.username})]"
    return f"[id:{db_user.id} ({db_user.full_name})]"


def create_logger(level: str = "INFO") -> logging.Logger:
    """Configure the root ``submission_bot`` logger with console and file handlers.

    Idempotent: subsequent calls with the same root logger are a no-op.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger(_ROOT_LOGGER_NAME)
    if root.handlers:
        return root

    root.setLevel(level.upper())

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(short_name)-10s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    short_name_filter = _ShortNameFilter()
    token_mask_filter = _TokenMaskFilter()

    # Console handler
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level.upper())
    console.setFormatter(fmt)
    console.addFilter(short_name_filter)
    console.addFilter(token_mask_filter)
    root.addHandler(console)

    # Rotating file handler — 5 MB, 5 backups
    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)
    file_handler.addFilter(short_name_filter)
    file_handler.addFilter(token_mask_filter)
    root.addHandler(file_handler)

    # Silence noisy libraries
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    # Catch unhandled exceptions
    def _excepthook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        root.critical("Необработанное исключение", exc_info=(exc_type, exc_value, exc_tb))

    sys.excepthook = _excepthook

    return root


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the ``submission_bot`` namespace."""
    return logging.getLogger(f"{_ROOT_LOGGER_NAME}.{name}")
