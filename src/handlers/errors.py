"""Global error handler for exceptions uncaught by individual handlers.

Registered on the Dispatcher so a network hiccup (TelegramNetworkError,
TelegramRetryAfter) or any other unhandled exception during update processing
is logged in a controlled way and — where possible — the user gets feedback
instead of a silently hanging button.
"""

from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from aiogram.types import CallbackQuery, ErrorEvent

import core.messages as msg
from core.logging import get_logger

logger = get_logger(__name__)

# Transient errors: network glitches / rate limits. Logged compactly (no traceback
# spam) — the bot stays alive and the action can simply be retried.
_TRANSIENT_ERRORS = (TelegramNetworkError, TelegramRetryAfter)


async def handle_global_error(event: ErrorEvent) -> bool:
    """Catch-all error handler for the Dispatcher.

    Returns ``True`` to mark the error as handled, which suppresses aiogram's
    own default traceback logging (we log it ourselves here).
    """
    exc = event.exception
    update = event.update
    update_id = getattr(update, "update_id", "?")

    if isinstance(exc, _TRANSIENT_ERRORS):
        logger.warning(
            "Временная ошибка связи с Telegram при обработке update id=%s: %s",
            update_id, exc,
        )
    else:
        logger.exception(
            "Необработанное исключение при обработке update id=%s",
            update_id, exc_info=exc,
        )

    # Best-effort feedback on callback queries so the button doesn't hang.
    callback = getattr(update, "callback_query", None)
    if isinstance(callback, CallbackQuery):
        text = (
            msg.NETWORK_ERROR_RETRY
            if isinstance(exc, _TRANSIENT_ERRORS)
            else msg.SOMETHING_WENT_WRONG
        )
        try:
            await callback.answer(text, show_alert=True)
        except Exception:
            # Connection still down or callback already answered — nothing to do.
            pass

    return True
