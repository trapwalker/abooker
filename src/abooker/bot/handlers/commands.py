"""Bot commands: /list, /status, /search."""
import logging
import shutil

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy import exists, func, or_, select

from abooker.bot.keyboards import book_actions, search_results
from abooker.core.config import settings
from abooker.core.database import get_session_factory
from abooker.core.models import AudioFile, FileStatus, Publication, User, UserBook
from abooker.search.jackett import search as jackett_search

log = logging.getLogger(__name__)

router = Router()

# In-memory store for last search results (keyed by chat_id) for callback handling.
_last_search: dict[int, list[dict]] = {}


@router.message(Command("help", "start"))
async def cmd_help(message: Message, user_role: str):
    is_admin = user_role == "admin"
    is_auth = user_role in ("admin", "authorized")

    lines = [
        "<b>Доступные команды:</b>",
        "",
        "/search &lt;запрос&gt; — поиск аудиокниг через Jackett",
        "/help — это сообщение",
    ]

    if not is_auth:
        lines += [
            "",
            "/auth — запросить авторизацию (чтобы добавлять книги)",
        ]

    if is_auth:
        lines += [
            "",
            "/list — список ваших книг",
            "/status — статистика сервиса",
            "",
            "<b>Добавить книгу:</b> отправьте magnet-ссылку, .torrent файл или URL торрента",
        ]

    if is_admin:
        lines += [
            "",
            "<b>Только для администратора:</b>",
            "• В /list — кнопки «Сделать публичной», «Очистить кеш»",
            "• Одобрение/отклонение запросов /auth",
        ]

    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("list"))
async def cmd_list(message: Message, user_role: str, db_user: User | None):
    if user_role == "guest":
        await message.answer("Для просмотра книг нужна авторизация. Отправьте /auth")
        return

    is_admin = user_role == "admin"
    factory = get_session_factory()
    async with factory() as session:
        if is_admin:
            result = await session.execute(select(Publication))
            pubs = result.scalars().all()
        else:
            # Own books + public books
            result = await session.execute(
                select(Publication).where(
                    or_(
                        Publication.is_public == True,  # noqa: E712
                        exists(
                            select(UserBook.id).where(
                                UserBook.publication_id == Publication.id,
                                UserBook.user_id == db_user.id,
                            )
                        ),
                    )
                )
            )
            pubs = result.scalars().all()

    if not pubs:
        await message.answer("Нет книг.")
        return

    for pub in pubs:
        rss_url = f"{settings.WEB_BASE_URL}/rss/{pub.slug}"
        public_label = " 🌐" if pub.is_public else ""
        text = (
            f"<b>{pub.title}</b>{public_label}\n"
            f"Файлов: {pub.total_files} | RSS: <code>{rss_url}</code>"
        )
        await message.answer(
            text,
            parse_mode="HTML",
            reply_markup=book_actions(pub.id, pub.slug, is_admin=is_admin, is_public=pub.is_public),
        )


@router.message(Command("status"))
async def cmd_status(message: Message, user_role: str):
    if user_role == "guest":
        await message.answer("Для просмотра статуса нужна авторизация. Отправьте /auth")
        return

    disk = shutil.disk_usage(settings.DATA_DIR)
    factory = get_session_factory()
    async with factory() as session:
        pub_count = (await session.execute(select(func.count()).select_from(Publication))).scalar()
        cached_size = (
            await session.execute(
                select(func.sum(AudioFile.size_bytes)).where(AudioFile.status == FileStatus.cached)
            )
        ).scalar() or 0

    text = (
        f"<b>Статус сервиса</b>\n"
        f"Книг: {pub_count}\n"
        f"Кеш: {_fmt_bytes(cached_size)}\n"
        f"Свободно: {_fmt_bytes(disk.free)} / {_fmt_bytes(disk.total)}"
    )
    await message.answer(text, parse_mode="HTML")


@router.message(Command("search"))
async def cmd_search(message: Message):
    query = message.text.removeprefix("/search").strip()
    if not query:
        await message.answer("Использование: /search <запрос>")
        return

    await message.answer(f"Ищу: <i>{query}</i>…", parse_mode="HTML")
    try:
        results = await jackett_search(query)
    except Exception as exc:
        await message.answer(f"Ошибка поиска: {exc}")
        return

    if not results:
        await message.answer("Ничего не найдено.")
        return

    _last_search[message.chat.id] = results

    lines = []
    for i, r in enumerate(results[:8]):
        seeders = r.get("seeders", "?")
        size = _fmt_bytes(r.get("size_bytes", 0))
        lines.append(f"{i+1}. <b>{r['title']}</b>\n   {size} | {seeders} seed")

    text = "Результаты поиска:\n\n" + "\n".join(lines)
    await message.answer(text, parse_mode="HTML", reply_markup=search_results(results))


# --- Callback handlers ---

@router.callback_query(F.data.startswith("rss:"))
async def cb_rss(callback: CallbackQuery):
    slug = callback.data.split(":", 1)[1]
    rss_url = f"{settings.WEB_BASE_URL}/rss/{slug}"
    await callback.answer()
    await callback.message.answer(f"RSS: <code>{rss_url}</code>", parse_mode="HTML")


@router.callback_query(F.data.startswith("status:"))
async def cb_pub_status(callback: CallbackQuery):
    slug = callback.data.split(":", 1)[1]
    factory = get_session_factory()
    async with factory() as session:
        pub_q = await session.execute(select(Publication).where(Publication.slug == slug))
        pub = pub_q.scalar_one_or_none()
        if not pub:
            await callback.answer("Не найдено", show_alert=True)
            return

        cached_q = await session.execute(
            select(func.count(), func.sum(AudioFile.size_bytes))
            .where(AudioFile.publication_id == pub.id)
            .where(AudioFile.status == FileStatus.cached)
        )
        cached_count, cached_size = cached_q.one()

    text = (
        f"<b>{pub.title}</b>\n"
        f"Файлов: {pub.total_files}\n"
        f"Кеш: {cached_count or 0} ({_fmt_bytes(cached_size or 0)})"
    )
    await callback.answer()
    await callback.message.answer(text, parse_mode="HTML")


@router.callback_query(F.data.startswith("evict:"))
async def cb_evict(callback: CallbackQuery, user_role: str):
    if user_role != "admin":
        await callback.answer("Недостаточно прав.", show_alert=True)
        return

    from abooker.cache.manager import run_eviction
    slug = callback.data.split(":", 1)[1]
    factory = get_session_factory()
    async with factory() as session:
        pub_q = await session.execute(select(Publication).where(Publication.slug == slug))
        pub = pub_q.scalar_one_or_none()
        if not pub:
            await callback.answer("Не найдено", show_alert=True)
            return
        await run_eviction(session, pub.id)
        await session.commit()

    await callback.answer("Кеш очищен")


@router.callback_query(F.data.startswith("remove_book:"))
async def cb_remove_book(callback: CallbackQuery, user_role: str, db_user: User | None):
    if user_role == "guest" or db_user is None:
        await callback.answer("Нужна авторизация.", show_alert=True)
        return

    pub_id = int(callback.data.split(":", 1)[1])
    factory = get_session_factory()
    async with factory() as session:
        # Get UserBook for this user
        ub_q = await session.execute(
            select(UserBook).where(
                UserBook.user_id == db_user.id,
                UserBook.publication_id == pub_id,
            )
        )
        ub = ub_q.scalar_one_or_none()
        if not ub:
            await callback.answer("Книга не найдена в вашем списке.", show_alert=True)
            return

        await session.delete(ub)
        await session.flush()

        # Check remaining owners
        remaining_q = await session.execute(
            select(func.count()).select_from(UserBook).where(UserBook.publication_id == pub_id)
        )
        remaining = remaining_q.scalar()

        if remaining == 0:
            pub_q = await session.execute(select(Publication).where(Publication.id == pub_id))
            pub = pub_q.scalar_one_or_none()
            if pub and not pub.is_public:
                # No more owners and not public — delete everything
                slug = pub.slug
                torrent_hash = pub.torrent_hash
                await session.delete(pub)
                await session.commit()

                pub_dir = settings.DATA_DIR / slug
                if pub_dir.exists():
                    import shutil as _shutil
                    _shutil.rmtree(pub_dir, ignore_errors=True)

                try:
                    from abooker.torrent import client as qbt
                    await qbt.remove_torrent(torrent_hash, delete_files=False)
                except Exception:
                    log.warning("Could not remove torrent %s from qBittorrent", torrent_hash)

                await callback.answer("Книга удалена полностью.")
                await callback.message.edit_text(f"<s>{slug}</s> удалена", parse_mode="HTML")
                return
            else:
                await session.commit()
        else:
            await session.commit()

    await callback.answer("Книга удалена из вашего списка.")
    await callback.message.edit_reply_markup(reply_markup=None)


@router.callback_query(F.data.startswith("toggle_public:"))
async def cb_toggle_public(callback: CallbackQuery, user_role: str):
    if user_role != "admin":
        await callback.answer("Недостаточно прав.", show_alert=True)
        return

    pub_id = int(callback.data.split(":", 1)[1])
    factory = get_session_factory()
    async with factory() as session:
        pub_q = await session.execute(select(Publication).where(Publication.id == pub_id))
        pub = pub_q.scalar_one_or_none()
        if not pub:
            await callback.answer("Не найдено.", show_alert=True)
            return

        pub.is_public = not pub.is_public
        await session.commit()
        new_state = pub.is_public
        slug = pub.slug
        is_public = pub.is_public

    label = "публичной" if new_state else "приватной"
    await callback.answer(f"Книга сделана {label}.")
    await callback.message.edit_reply_markup(
        reply_markup=book_actions(pub_id, slug, is_admin=True, is_public=is_public)
    )


@router.callback_query(F.data.startswith("publish_idx:"))
async def cb_publish_idx(callback: CallbackQuery, user_role: str, db_user: User | None):
    if user_role == "guest":
        await callback.answer("Для добавления книг нужна авторизация. Отправьте /auth", show_alert=True)
        return

    chat_id = callback.message.chat.id
    idx = int(callback.data.split(":", 1)[1])
    results = _last_search.get(chat_id, [])
    if idx >= len(results):
        await callback.answer("Результат устарел, выполните поиск заново", show_alert=True)
        return

    source = results[idx].get("magnet") or results[idx].get("torrent_url")
    if not source:
        await callback.answer("Нет ссылки для скачивания", show_alert=True)
        return

    await callback.answer()
    status_msg = await callback.message.answer("Добавляю книгу…")
    try:
        factory = get_session_factory()
        async with factory() as session:
            pub, is_new = await create_publication(
                session,
                source,
                title=results[idx].get("title"),
                user_id=db_user.id if db_user else None,
            )
            await session.commit()

        rss_url = f"{settings.WEB_BASE_URL}/rss/{pub.slug}"
        is_admin = user_role == "admin"
        if is_new:
            text = (
                f"Книга добавлена: <b>{pub.title}</b>\n"
                f"RSS: <code>{rss_url}</code>"
            )
        else:
            text = (
                f"Книга уже есть, добавлена к вашим: <b>{pub.title}</b>\n"
                f"RSS: <code>{rss_url}</code>"
            )
        await callback.bot.edit_message_text(
            chat_id=chat_id,
            message_id=status_msg.message_id,
            text=text,
            parse_mode="HTML",
            reply_markup=book_actions(pub.id, pub.slug, is_admin=is_admin, is_public=pub.is_public),
        )
    except Exception as exc:
        log.exception("Failed to publish from search result")
        await callback.bot.edit_message_text(
            chat_id=chat_id,
            message_id=status_msg.message_id,
            text=f"Ошибка: {exc}",
        )


def _fmt_bytes(n: int) -> str:
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} ТБ"


# deferred import to avoid circular
from abooker.torrent.manager import create_publication  # noqa: E402
