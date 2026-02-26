"""Handle incoming torrent content: .torrent files, magnet links, URLs."""
import logging
import tempfile
from pathlib import Path

from aiogram import Bot, Router
from aiogram.types import Document, Message

from abooker.bot.keyboards import publication_actions
from abooker.core.config import settings
from abooker.core.database import get_session_factory
from abooker.torrent.manager import create_publication

log = logging.getLogger(__name__)

router = Router()


def _is_magnet(text: str) -> bool:
    return text.strip().startswith("magnet:")


def _is_url(text: str) -> bool:
    t = text.strip()
    return t.startswith("http://") or t.startswith("https://")


async def _create_and_notify(bot: Bot, chat_id: int, message_id: int, source: str):
    status_msg = await bot.send_message(chat_id, "Добавляю торрент…")
    try:
        factory = get_session_factory()
        async with factory() as session:
            pub = await create_publication(session, source)
            await session.commit()

        rss_url = f"{settings.WEB_BASE_URL}/rss/{pub.slug}"
        text = (
            f"Публикация создана: <b>{pub.title}</b>\n"
            f"RSS: <code>{rss_url}</code>\n"
            f"Файлов: {pub.total_files}"
        )
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=status_msg.message_id,
            text=text,
            parse_mode="HTML",
            reply_markup=publication_actions(pub.slug),
        )
    except Exception as exc:
        log.exception("Failed to create publication from %s", source)
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=status_msg.message_id,
            text=f"Ошибка: {exc}",
        )


@router.message(lambda m: m.document and m.document.file_name and m.document.file_name.endswith(".torrent"))
async def handle_torrent_file(message: Message, bot: Bot):
    doc: Document = message.document
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / doc.file_name
        await bot.download(doc, destination=dest)
        await _create_and_notify(bot, message.chat.id, message.message_id, str(dest))


@router.message(lambda m: m.text and (_is_magnet(m.text) or _is_url(m.text)))
async def handle_magnet_or_url(message: Message, bot: Bot):
    await _create_and_notify(bot, message.chat.id, message.message_id, message.text.strip())
