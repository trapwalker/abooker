from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def publication_actions(slug: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="RSS", callback_data=f"rss:{slug}"),
        InlineKeyboardButton(text="Статус", callback_data=f"status:{slug}"),
    )
    builder.row(
        InlineKeyboardButton(text="Очистить кеш", callback_data=f"evict:{slug}"),
        InlineKeyboardButton(text="Удалить", callback_data=f"delete:{slug}"),
    )
    return builder.as_markup()


def search_result(title: str, magnet_or_url: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="Опубликовать",
        callback_data=f"publish:{magnet_or_url[:200]}",
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
