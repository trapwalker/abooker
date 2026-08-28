"""aiogram HTTP session tuned for this VDS's intermittently flaky route to
api.telegram.org (see the network-block-sl2 memory): some requests hang
completely instead of failing fast, and aiogram's default 60s per-request
timeout means a single blip on any API call — send_message,
edit_message_text, answer_callback_query, anything — silently stalls that
whole update for up to a minute with nothing to show for it.

Wrapping retry at the *session* level (instead of individual call sites, as
an earlier version of this code did for just two calls) covers every Bot
API call transparently, including ones made deep inside aiogram itself
(e.g. callback.answer()) that application code never explicitly wraps.
"""
import asyncio
import logging
from typing import Any, TypeVar

from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import TelegramMethod
from aiogram.methods.base import TelegramType

log = logging.getLogger(__name__)

T = TypeVar("T")

# Short enough that a stalled connection fails fast instead of eating a full
# minute; still comfortably above how long a real (working) API call takes.
# Long-polling's own getUpdates call is unaffected — the dispatcher computes
# its timeout as session.timeout + polling_timeout, not session.timeout alone.
REQUEST_TIMEOUT = 10.0
RETRY_ATTEMPTS = 3
RETRY_DELAY = 1.5


class RetryingAiohttpSession(AiohttpSession):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("timeout", REQUEST_TIMEOUT)
        super().__init__(*args, **kwargs)

    async def make_request(
        self, bot: Any, method: TelegramMethod[TelegramType], timeout: int | None = None
    ) -> TelegramType:
        for attempt in range(RETRY_ATTEMPTS):
            try:
                return await super().make_request(bot, method, timeout=timeout)
            except TelegramNetworkError:
                if attempt == RETRY_ATTEMPTS - 1:
                    raise
                log.warning(
                    "Telegram network blip on %s (attempt %d/%d), retrying...",
                    type(method).__name__, attempt + 1, RETRY_ATTEMPTS,
                )
                await asyncio.sleep(RETRY_DELAY)
        raise AssertionError("unreachable")  # loop always returns or raises
