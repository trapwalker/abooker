"""Retry wrapper for Telegram API calls on transient network blips.

This VDS's route to api.telegram.org is intermittently flaky — some requests
time out completely while most succeed in well under a second (see the
network-block-sl2 memory for the related /etc/hosts pin). Without a retry, a
single unlucky blip on e.g. the very first `bot.send_message("Добавляю
книгу…")` of a flow drops it silently before create_publication is even
called: the user sees no reaction at all, not even an error.
"""
import asyncio
import logging
from collections.abc import Awaitable, Callable, Coroutine
from typing import TypeVar

from aiogram.exceptions import TelegramNetworkError

log = logging.getLogger(__name__)

T = TypeVar("T")


async def with_retry(
    call: Callable[[], Coroutine[None, None, T]] | Callable[[], Awaitable[T]],
    attempts: int = 3,
    delay: float = 1.5,
) -> T:
    """Call `call()` (a zero-arg async callable, typically a lambda wrapping
    a bot.send_message/edit_message_text/etc. call), retrying on
    TelegramNetworkError. Any other exception propagates immediately —
    only the network blip is worth retrying, not e.g. a bad request.
    """
    for attempt in range(attempts):
        try:
            return await call()
        except TelegramNetworkError:
            if attempt == attempts - 1:
                raise
            log.warning("Telegram network blip (attempt %d/%d), retrying...", attempt + 1, attempts)
            await asyncio.sleep(delay)
    raise AssertionError("unreachable")  # loop always returns or raises
