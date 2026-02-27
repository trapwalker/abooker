"""Handle incoming torrent content: .torrent files, magnet links, URLs."""
import logging
import tempfile
from pathlib import Path

from aiogram import Bot, Router
from aiogram.types import Document, Message

from abooker.bot.keyboards import book_actions
from abooker.core.config import settings
from abooker.core.database import get_session_factory
from abooker.core.models import User
from abooker.torrent.manager import create_publication

log = logging.getLogger(__name__)

router = Router()


def _is_magnet(text: str) -> bool:
    return text.strip().startswith("magnet:")


def _is_url(text: str) -> bool:
    t = text.strip()
    return t.startswith("http://") or t.startswith("https://")


async def _create_and_notify(
    bot: Bot,
    chat_id: int,
    message_id: int,
    source: str,
    user_role: str,
    db_user: User | None,
):
    is_admin = user_role == "admin"
    status_msg = await bot.send_message(chat_id, "Добавляю книгу…")
    try:
        factory = get_session_factory()
        async with factory() as session:
            pub, is_new = await create_publication(
                session,
                source,
                user_id=db_user.id if db_user else None,
            )
            await session.commit()

        rss_url = f"{settings.WEB_BASE_URL}/rss/{pub.slug}"
        if is_new:
            text = (
                f"Книга добавлена: <b>{pub.title}</b>\n"
                f"RSS: <code>{rss_url}</code>\n"
                f"Файлов: {pub.total_files}"
            )
        else:
            text = (
                f"Книга уже есть, добавлена к вашим: <b>{pub.title}</b>\n"
                f"RSS: <code>{rss_url}</code>"
            )
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=status_msg.message_id,
            text=text,
            parse_mode="HTML",
            reply_markup=book_actions(pub.id, pub.slug, is_admin=is_admin, is_public=pub.is_public),
        )
    except Exception as exc:
        log.exception("Failed to create publication from %s", source)
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=status_msg.message_id,
            text=f"Ошибка: {exc}",
        )


@router.message(lambda m: m.document and m.document.file_name and m.document.file_name.endswith(".torrent"))
async def handle_torrent_file(message: Message, bot: Bot, user_role: str, db_user: User | None):
    if user_role == "guest":
        await message.answer("Для добавления книг нужна авторизация. Отправьте /auth")
        return

    doc: Document = message.document
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / doc.file_name
        await bot.download(doc, destination=dest)
        await _create_and_notify(bot, message.chat.id, message.message_id, str(dest), user_role, db_user)


@router.message(lambda m: m.text and (_is_magnet(m.text) or _is_url(m.text)))
async def handle_magnet_or_url(message: Message, bot: Bot, user_role: str, db_user: User | None):
    if user_role == "guest":
        await message.answer("Для добавления книг нужна авторизация. Отправьте /auth")
        return

    await _create_and_notify(bot, message.chat.id, message.message_id, message.text.strip(), user_role, db_user)
