from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

CHAPTERS_PER_PAGE = 12

STATUS_ICONS = {
    "pending": "⬜",
    "downloading": "⏳",
    "cached": "✅",
    "evicted": "⬜",
}


def book_actions(pub_id: int, slug: str, is_admin: bool = False, is_public: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="RSS", callback_data=f"rss:{slug}"),
        InlineKeyboardButton(text="Статус", callback_data=f"status:{slug}"),
    )
    builder.row(
        InlineKeyboardButton(text="📖 Главы", callback_data=f"ch:{pub_id}:0"),
    )
    builder.row(
        InlineKeyboardButton(text="Удалить у себя", callback_data=f"remove_book:{pub_id}"),
    )
    if is_admin:
        if is_public:
            builder.row(
                InlineKeyboardButton(text="Снять публичность", callback_data=f"toggle_public:{pub_id}"),
            )
        else:
            builder.row(
                InlineKeyboardButton(text="Сделать публичной", callback_data=f"toggle_public:{pub_id}"),
            )
        builder.row(
            InlineKeyboardButton(text="Очистить кеш", callback_data=f"evict:{slug}"),
        )
    return builder.as_markup()


def chapter_list(pub_id: int, chapters: list[dict], page: int, total_pages: int) -> InlineKeyboardMarkup:
    """chapters: list of {index, status: 'pending'|'downloading'|'cached'|'evicted'} for the current page."""
    builder = InlineKeyboardBuilder()
    for ch in chapters:
        idx = ch["index"]
        status = ch["status"]
        icon = STATUS_ICONS.get(status, "⬜")
        label = f"{icon} Глава {idx + 1}"
        if status == "downloading":
            pct = ch.get("progress")
            if pct is not None:
                label += f" ({pct:.0%})"
        builder.row(
            InlineKeyboardButton(text=label, callback_data=f"chnoop:{pub_id}"),
            InlineKeyboardButton(text="🔥", callback_data=f"chw:{pub_id}:{idx}"),
            InlineKeyboardButton(text="🗑", callback_data=f"chd:{pub_id}:{idx}"),
        )

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️", callback_data=f"ch:{pub_id}:{page - 1}"))
    nav_row.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data=f"chnoop:{pub_id}"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton(text="➡️", callback_data=f"ch:{pub_id}:{page + 1}"))
    builder.row(*nav_row)
    builder.row(InlineKeyboardButton(text="« К книге", callback_data=f"chback:{pub_id}"))

    return builder.as_markup()


def search_results(results: list[dict]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for i, r in enumerate(results[:8]):
        builder.button(
            text=f"{i+1}. {r['title'][:40]}",
            callback_data=f"publish_idx:{i}",
        )
    builder.adjust(1)
    return builder.as_markup()
