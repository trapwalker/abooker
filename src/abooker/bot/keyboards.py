from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def book_actions(pub_id: int, slug: str, is_admin: bool = False, is_public: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="RSS", callback_data=f"rss:{slug}"),
        InlineKeyboardButton(text="Статус", callback_data=f"status:{slug}"),
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


def search_results(results: list[dict]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for i, r in enumerate(results[:8]):
        builder.button(
            text=f"{i+1}. {r['title'][:40]}",
            callback_data=f"publish_idx:{i}",
        )
    builder.adjust(1)
    return builder.as_markup()
