"""Bot commands: /list, /status, /search."""
import logging
import shutil

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select

from abooker.bot.keyboards import publication_actions, search_results
from abooker.core.config import settings
from abooker.core.database import get_session_factory
from abooker.core.models import AudioFile, FileStatus, Publication
from abooker.search.jackett import search as jackett_search

log = logging.getLogger(__name__)

router = Router()

# In-memory store for last search results (keyed by chat_id) for callback handling.
_last_search: dict[int, list[dict]] = {}


@router.message(Command("list"))
async def cmd_list(message: Message):
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(select(Publication))
        pubs = result.scalars().all()

    if not pubs:
        await message.answer("Нет публикаций.")
        return

    for pub in pubs:
        rss_url = f"{settings.WEB_BASE_URL}/rss/{pub.slug}"
        text = (
            f"<b>{pub.title}</b>\n"
            f"Файлов: {pub.total_files} | RSS: <code>{rss_url}</code>"
        )
        await message.answer(text, parse_mode="HTML", reply_markup=publication_actions(pub.slug))


@router.message(Command("status"))
async def cmd_status(message: Message):
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
        f"Публикаций: {pub_count}\n"
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

@router.callback_query(lambda c: c.data and c.data.startswith("rss:"))
async def cb_rss(callback: CallbackQuery):
    slug = callback.data.split(":", 1)[1]
    rss_url = f"{settings.WEB_BASE_URL}/rss/{slug}"
    await callback.answer()
    await callback.message.answer(f"RSS: <code>{rss_url}</code>", parse_mode="HTML")


@router.callback_query(lambda c: c.data and c.data.startswith("status:"))
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


@router.callback_query(lambda c: c.data and c.data.startswith("evict:"))
async def cb_evict(callback: CallbackQuery):
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


@router.callback_query(lambda c: c.data and c.data.startswith("delete:"))
async def cb_delete(callback: CallbackQuery):
    import shutil as _shutil
    slug = callback.data.split(":", 1)[1]
    factory = get_session_factory()
    async with factory() as session:
        pub_q = await session.execute(select(Publication).where(Publication.slug == slug))
        pub = pub_q.scalar_one_or_none()
        if not pub:
            await callback.answer("Не найдено", show_alert=True)
            return
        await session.delete(pub)
        await session.commit()

    pub_dir = settings.DATA_DIR / slug
    if pub_dir.exists():
        _shutil.rmtree(pub_dir, ignore_errors=True)

    await callback.answer("Удалено")
    await callback.message.edit_text(f"<s>{slug}</s> удалён", parse_mode="HTML")


@router.callback_query(lambda c: c.data and c.data.startswith("publish_idx:"))
async def cb_publish_idx(callback: CallbackQuery):
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
    status_msg = await callback.message.answer("Добавляю торрент…")
    try:
        factory = get_session_factory()
        async with factory() as session:
            pub = await create_publication(session, source, title=results[idx].get("title"))
            await session.commit()

        rss_url = f"{settings.WEB_BASE_URL}/rss/{pub.slug}"
        await callback.bot.edit_message_text(
            chat_id=chat_id,
            message_id=status_msg.message_id,
            text=(
                f"Публикация создана: <b>{pub.title}</b>\n"
                f"RSS: <code>{rss_url}</code>"
            ),
            parse_mode="HTML",
            reply_markup=publication_actions(pub.slug),
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
