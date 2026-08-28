"""Handle incoming torrent content: .torrent files, magnet links, URLs."""
import logging
import tempfile
from pathlib import Path

from aiogram import Bot, Router
from aiogram.types import Document, Message

from abooker.bot.formatting import format_metadata_progress, format_ready_text
from abooker.bot.keyboards import book_actions
from abooker.core.database import get_session_factory
from abooker.core.models import Publication, User
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
    # reply_to_message_id threads this under the message that triggered it,
    # instead of just appearing as a bare new message in the chat.
    status_msg = await bot.send_message(chat_id, "Добавляю книгу…", reply_to_message_id=message_id)

    delivered_ready = False  # whether on_ready already edited status_msg

    async def on_metadata_progress(elapsed: float, diag: dict) -> None:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_msg.message_id,
                text=format_metadata_progress(elapsed, diag),
            )
        except Exception:
            # Telegram rate limit / "message not modified" — not worth
            # failing the whole publication for a missed status update.
            log.debug("Could not edit status message with metadata progress", exc_info=True)

    async def on_ready(pub: Publication) -> None:
        # Fires once metadata resolved and RSS actually exists on disk —
        # edit the status message right away instead of waiting for warmup
        # to finish too, and put the RSS link straight in the text so
        # there's no extra button tap to get it.
        nonlocal delivered_ready
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_msg.message_id,
                text=format_ready_text(pub, is_new=True),
                parse_mode="HTML",
                reply_markup=book_actions(pub.id, pub.slug, is_admin=is_admin, is_public=pub.is_public),
            )
            delivered_ready = True
        except Exception:
            log.warning("Could not deliver ready notification for %s", pub.slug, exc_info=True)

    try:
        factory = get_session_factory()
        async with factory() as session:
            pub, is_new = await create_publication(
                session,
                source,
                user_id=db_user.id if db_user else None,
                on_metadata_progress=on_metadata_progress,
                on_ready=on_ready,
            )
            await session.commit()

        if not delivered_ready:
            # Either a dedup return (on_ready never fires — the existing
            # publication resolved before ever waiting on metadata) or
            # on_ready itself failed to deliver — show the final state now.
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_msg.message_id,
                text=format_ready_text(pub, is_new=is_new),
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
