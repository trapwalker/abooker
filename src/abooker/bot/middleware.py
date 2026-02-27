"""Bot middleware: resolve Telegram user → DB User, attach role to handler data."""
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update
from sqlalchemy import select

from abooker.core.config import settings
from abooker.core.database import get_session_factory
from abooker.core.models import User

log = logging.getLogger(__name__)


class UserMiddleware(BaseMiddleware):
    """Resolve Telegram user to DB User and determine role.

    Sets in handler data:
        db_user: User | None  — DB record (None for channels/anonymous)
        user_role: "admin" | "authorized" | "guest"
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user = data.get("event_from_user")
        db_user: User | None = None
        user_role = "guest"

        # Debug: log who sent what
        if tg_user is not None and isinstance(event, Update):
            if event.message:
                msg = event.message
                content = msg.text or msg.caption or f"[{msg.content_type}]"
                log.debug("msg  uid=%s (@%s): %s", tg_user.id, tg_user.username, content)
            elif event.callback_query:
                log.debug("cb   uid=%s (@%s): %s", tg_user.id, tg_user.username, event.callback_query.data)

        if tg_user is not None:
            if tg_user.id in settings.BOT_ADMIN_IDS:
                user_role = "admin"

            factory = get_session_factory()
            try:
                async with factory() as session:
                    result = await session.execute(
                        select(User).where(User.telegram_id == tg_user.id)
                    )
                    db_user = result.scalar_one_or_none()
                    if db_user is None:
                        db_user = User(
                            telegram_id=tg_user.id,
                            username=tg_user.username,
                            first_name=tg_user.first_name,
                            is_authorized=(user_role == "admin"),
                            created_at=datetime.now(timezone.utc),
                            last_seen=datetime.now(timezone.utc),
                        )
                        session.add(db_user)
                    else:
                        db_user.last_seen = datetime.now(timezone.utc)
                        db_user.username = tg_user.username
                        db_user.first_name = tg_user.first_name
                    await session.commit()
                    await session.refresh(db_user)
            except Exception:
                log.exception("UserMiddleware: failed to resolve user %s", tg_user.id)

            if user_role != "admin" and db_user and db_user.is_authorized:
                user_role = "authorized"

        data["db_user"] = db_user
        data["user_role"] = user_role
        return await handler(event, data)
