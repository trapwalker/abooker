"""/auth command and admin approve/reject callbacks."""
import logging
from datetime import datetime, timezone

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select

from abooker.core.config import settings
from abooker.core.database import get_session_factory
from abooker.core.models import AuthRequest, AuthRequestStatus, User

log = logging.getLogger(__name__)

router = Router()


def _auth_admin_keyboard(request_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Одобрить", callback_data=f"auth_approve:{request_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"auth_reject:{request_id}"),
    )
    return builder.as_markup()


@router.message(Command("auth"))
async def cmd_auth(message: Message, db_user: User | None, user_role: str, bot: Bot):
    if user_role in ("admin", "authorized"):
        await message.answer("Вы уже авторизованы.")
        return

    factory = get_session_factory()
    async with factory() as session:
        # Ensure DB user exists (middleware should have created it, but be safe)
        tg = message.from_user
        if db_user is None:
            db_user = User(
                telegram_id=tg.id,
                username=tg.username,
                first_name=tg.first_name,
                created_at=datetime.now(timezone.utc),
                last_seen=datetime.now(timezone.utc),
            )
            session.add(db_user)
            await session.flush()
        else:
            # Re-attach to this session
            db_user = await session.merge(db_user)

        # Check for existing pending request
        existing = await session.execute(
            select(AuthRequest).where(
                AuthRequest.user_id == db_user.id,
                AuthRequest.status == AuthRequestStatus.pending,
            )
        )
        if existing.scalar_one_or_none():
            await message.answer("Запрос авторизации уже отправлен, ожидайте одобрения администратора.")
            return

        req = AuthRequest(
            user_id=db_user.id,
            requested_at=datetime.now(timezone.utc),
        )
        session.add(req)
        await session.flush()

        if not settings.BOT_ADMIN_IDS:
            await message.answer("Администраторы не настроены. Обратитесь к владельцу сервиса.")
            await session.rollback()
            return

        # Send notification to all admins
        username_part = f"@{tg.username}" if tg.username else f"id:{tg.id}"
        name_part = tg.first_name or ""
        text = (
            f"<b>Запрос авторизации</b>\n"
            f"Пользователь: {name_part} {username_part}\n"
            f"ID: <code>{tg.id}</code>"
        )
        keyboard = _auth_admin_keyboard(req.id)
        msg_ids: dict[str, int] = {}
        for admin_id in settings.BOT_ADMIN_IDS:
            try:
                sent = await bot.send_message(admin_id, text, parse_mode="HTML", reply_markup=keyboard)
                msg_ids[str(admin_id)] = sent.message_id
            except Exception:
                log.warning("Could not notify admin %s", admin_id, exc_info=True)

        req.bot_message_ids = msg_ids
        await session.commit()

    await message.answer("Запрос авторизации отправлен. Вы получите уведомление после одобрения.")


@router.callback_query(F.data.startswith("auth_approve:"))
async def cb_auth_approve(callback: CallbackQuery, user_role: str, bot: Bot):
    if user_role != "admin":
        await callback.answer("Недостаточно прав.", show_alert=True)
        return

    request_id = int(callback.data.split(":", 1)[1])
    factory = get_session_factory()
    async with factory() as session:
        req_q = await session.execute(
            select(AuthRequest).where(AuthRequest.id == request_id)
        )
        req = req_q.scalar_one_or_none()
        if req is None:
            await callback.answer("Запрос не найден.", show_alert=True)
            return
        if req.status != AuthRequestStatus.pending:
            await callback.answer("Запрос уже обработан.", show_alert=True)
            return

        user_q = await session.execute(select(User).where(User.id == req.user_id))
        target_user = user_q.scalar_one_or_none()
        if target_user is None:
            await callback.answer("Пользователь не найден.", show_alert=True)
            return

        target_user.is_authorized = True
        req.status = AuthRequestStatus.approved
        req.resolved_by = callback.from_user.id
        req.resolved_at = datetime.now(timezone.utc)
        await session.commit()

        msg_ids = req.bot_message_ids

    # Update all admin messages
    for admin_id_str, msg_id in msg_ids.items():
        try:
            await bot.edit_message_text(
                chat_id=int(admin_id_str),
                message_id=msg_id,
                text=f"✅ Авторизация одобрена\nID: <code>{target_user.telegram_id}</code>",
                parse_mode="HTML",
            )
        except Exception:
            log.warning("Could not edit admin message %s/%s", admin_id_str, msg_id)

    # Notify the user
    try:
        await bot.send_message(
            target_user.telegram_id,
            "✅ Авторизация одобрена. Теперь вы можете добавлять книги.",
        )
    except Exception:
        log.warning("Could not notify user %s", target_user.telegram_id)

    await callback.answer("Пользователь авторизован.")


@router.callback_query(F.data.startswith("auth_reject:"))
async def cb_auth_reject(callback: CallbackQuery, user_role: str, bot: Bot):
    if user_role != "admin":
        await callback.answer("Недостаточно прав.", show_alert=True)
        return

    request_id = int(callback.data.split(":", 1)[1])
    factory = get_session_factory()
    async with factory() as session:
        req_q = await session.execute(
            select(AuthRequest).where(AuthRequest.id == request_id)
        )
        req = req_q.scalar_one_or_none()
        if req is None:
            await callback.answer("Запрос не найден.", show_alert=True)
            return
        if req.status != AuthRequestStatus.pending:
            await callback.answer("Запрос уже обработан.", show_alert=True)
            return

        user_q = await session.execute(select(User).where(User.id == req.user_id))
        target_user = user_q.scalar_one_or_none()

        req.status = AuthRequestStatus.rejected
        req.resolved_by = callback.from_user.id
        req.resolved_at = datetime.now(timezone.utc)
        await session.commit()

        msg_ids = req.bot_message_ids
        target_telegram_id = target_user.telegram_id if target_user else None

    # Update all admin messages
    for admin_id_str, msg_id in msg_ids.items():
        try:
            await bot.edit_message_text(
                chat_id=int(admin_id_str),
                message_id=msg_id,
                text=f"❌ Авторизация отклонена\nID: <code>{target_telegram_id}</code>",
                parse_mode="HTML",
            )
        except Exception:
            log.warning("Could not edit admin message %s/%s", admin_id_str, msg_id)

    # Notify the user
    if target_telegram_id:
        try:
            await bot.send_message(
                target_telegram_id,
                "❌ В авторизации отказано.",
            )
        except Exception:
            log.warning("Could not notify user %s", target_telegram_id)

    await callback.answer("Запрос отклонён.")
