"""Per-chapter cache management: list chapters with status, warm up / evict one at a time."""
import logging
import math

from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy import select

from abooker.bot.keyboards import CHAPTERS_PER_PAGE, book_actions, chapter_list
from abooker.cache.manager import estimate_eta_seconds, evict_file, warmup_file
from abooker.core.database import get_session_factory
from abooker.core.models import AudioFile, FileStatus, Publication, User, UserBook
from abooker.format import fmt_bytes, fmt_duration
from abooker.torrent import client as qbt

log = logging.getLogger(__name__)

router = Router()


async def _can_manage(session, pub_id: int, user_role: str, db_user: User | None) -> bool:
    if user_role == "admin":
        return True
    if db_user is None:
        return False
    q = await session.execute(
        select(UserBook.id).where(UserBook.user_id == db_user.id, UserBook.publication_id == pub_id)
    )
    return q.scalar_one_or_none() is not None


async def _render_page(pub_id: int, page: int) -> tuple[str, object] | None:
    """Build (text, keyboard) for one page of a book's chapter list, or None if not found."""
    factory = get_session_factory()
    async with factory() as session:
        pub_q = await session.execute(select(Publication).where(Publication.id == pub_id))
        pub = pub_q.scalar_one_or_none()
        if pub is None:
            return None

        af_q = await session.execute(
            select(AudioFile).where(AudioFile.publication_id == pub_id).order_by(AudioFile.index)
        )
        chapters = af_q.scalars().all()

        progress_by_qbt_idx: dict[int, float] = {}
        speed = 0
        if pub.torrent_hash:
            try:
                statuses = await qbt.get_files_status(pub.torrent_hash)
                progress_by_qbt_idx = {s["index"]: s["progress"] for s in statuses}
                speed = await qbt.get_torrent_speed(pub.torrent_hash)
            except Exception:
                log.warning("Could not fetch qBittorrent status for %s", pub.slug, exc_info=True)

        total = len(chapters)
        total_pages = max(1, math.ceil(total / CHAPTERS_PER_PAGE))
        page = max(0, min(page, total_pages - 1))
        start = page * CHAPTERS_PER_PAGE
        page_chapters = chapters[start:start + CHAPTERS_PER_PAGE]

        cached_count = sum(1 for c in chapters if c.status == FileStatus.cached)
        cached_size = sum(c.size_bytes for c in chapters if c.status == FileStatus.cached)

        lines = [
            f"<b>{pub.title}</b>",
            f"Глав {total}, в кеше {cached_count} ({fmt_bytes(cached_size)})",
            "",
        ]

        keyboard_rows = []
        for c in page_chapters:
            live_progress = progress_by_qbt_idx.get(c.qbt_file_index) if c.qbt_file_index is not None else None
            status = c.status.value if hasattr(c.status, "value") else str(c.status)

            if status == "cached" or (live_progress is not None and live_progress >= 1.0):
                icon = "✅"
                suffix = fmt_bytes(c.size_bytes)
            elif status == "downloading" or (live_progress and live_progress > 0):
                icon = "⏳"
                pct = live_progress or 0.0
                remaining = c.size_bytes * (1.0 - pct)
                eta_str = fmt_duration(remaining / speed) if speed > 0 else "оценка недоступна"
                suffix = f"{pct:.0%}, ETA {eta_str}"
                status = "downloading"
            else:
                icon = "⬜"
                suffix = fmt_bytes(c.size_bytes)
                status = "pending"

            lines.append(f"{icon} Глава {c.index + 1} — {suffix}")
            keyboard_rows.append({"index": c.index, "status": status, "progress": live_progress})

        text = "\n".join(lines)
        keyboard = chapter_list(pub_id, keyboard_rows, page, total_pages)
        return text, keyboard


async def _show_page(callback: CallbackQuery, pub_id: int, page: int) -> None:
    result = await _render_page(pub_id, page)
    if result is None:
        await callback.answer("Книга не найдена", show_alert=True)
        return
    text, keyboard = result
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        # Message unchanged (e.g. same content re-rendered) — Telegram rejects identical edits.
        pass


@router.callback_query(F.data.startswith("ch:"))
async def cb_chapters(callback: CallbackQuery, user_role: str, db_user: User | None):
    _, pub_id_s, page_s = callback.data.split(":")
    pub_id, page = int(pub_id_s), int(page_s)

    factory = get_session_factory()
    async with factory() as session:
        if not await _can_manage(session, pub_id, user_role, db_user):
            await callback.answer("Недостаточно прав.", show_alert=True)
            return

    await callback.answer()
    await _show_page(callback, pub_id, page)


@router.callback_query(F.data.startswith("chnoop:"))
async def cb_chapters_noop(callback: CallbackQuery):
    await callback.answer()


@router.callback_query(F.data.startswith("chw:"))
async def cb_chapter_warmup(callback: CallbackQuery, user_role: str, db_user: User | None):
    _, pub_id_s, idx_s = callback.data.split(":")
    pub_id, idx = int(pub_id_s), int(idx_s)

    factory = get_session_factory()
    async with factory() as session:
        if not await _can_manage(session, pub_id, user_role, db_user):
            await callback.answer("Недостаточно прав.", show_alert=True)
            return

        pub_q = await session.execute(select(Publication).where(Publication.id == pub_id))
        pub = pub_q.scalar_one_or_none()
        af = await warmup_file(session, pub_id, idx)
        if af is None:
            await callback.answer("Глава не найдена", show_alert=True)
            return

        if af.status == FileStatus.cached:
            await session.commit()
            await callback.answer("Уже в кеше ✅")
        else:
            eta = await estimate_eta_seconds(pub, af, 0.0)
            await session.commit()
            msg = "Поставлено на закачку 🔥"
            if eta is not None:
                msg += f" (≈ {fmt_duration(eta)})"
            await callback.answer(msg)

    # Find current page for this chapter to re-render it.
    page = idx // CHAPTERS_PER_PAGE
    await _show_page(callback, pub_id, page)


@router.callback_query(F.data.startswith("chd:"))
async def cb_chapter_evict(callback: CallbackQuery, user_role: str, db_user: User | None):
    _, pub_id_s, idx_s = callback.data.split(":")
    pub_id, idx = int(pub_id_s), int(idx_s)

    factory = get_session_factory()
    async with factory() as session:
        if not await _can_manage(session, pub_id, user_role, db_user):
            await callback.answer("Недостаточно прав.", show_alert=True)
            return

        af = await evict_file(session, pub_id, idx)
        await session.commit()
        if af is None:
            await callback.answer("Глава не найдена", show_alert=True)
            return
        await callback.answer("Удалено из кеша 🗑")

    page = idx // CHAPTERS_PER_PAGE
    await _show_page(callback, pub_id, page)


@router.callback_query(F.data.startswith("chback:"))
async def cb_chapters_back(callback: CallbackQuery, user_role: str, db_user: User | None):
    pub_id = int(callback.data.split(":", 1)[1])
    factory = get_session_factory()
    async with factory() as session:
        pub_q = await session.execute(select(Publication).where(Publication.id == pub_id))
        pub = pub_q.scalar_one_or_none()
        if pub is None:
            await callback.answer("Книга не найдена", show_alert=True)
            return

    await callback.answer()
    is_admin = user_role == "admin"
    text = (
        f"<b>{pub.title}</b>\n"
        f"Файлов: {pub.total_files}"
    )
    try:
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=book_actions(pub.id, pub.slug, is_admin=is_admin, is_public=pub.is_public),
        )
    except Exception:
        pass
