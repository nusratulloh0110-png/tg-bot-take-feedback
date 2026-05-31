from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.db.models import Employee
from app.domain.criteria import IMPLEMENTATION_TAGS, label_for_rating
from app.locales import normalize_lang, t


def language_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Русский", callback_data="lang:ru"),
                InlineKeyboardButton(text="O'zbekcha", callback_data="lang:uz"),
            ]
        ]
    )


def main_menu_keyboard(language: str | None) -> InlineKeyboardMarkup:
    lang = normalize_lang(language)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t(lang, "employee_feedback"), callback_data="main:employee"),
                InlineKeyboardButton(text=t(lang, "implementation_feedback"), callback_data="main:implementation"),
            ],
            [
                InlineKeyboardButton(text=t(lang, "my_reviews"), callback_data="main:my_reviews"),
                InlineKeyboardButton(text=t(lang, "settings"), callback_data="main:settings"),
            ],
        ]
    )


def employee_keyboard(employees: list[Employee], language: str | None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for employee in employees:
        builder.button(text=employee.full_name, callback_data=f"employee:{employee.id}")
    builder.button(text=t(language, "unknown_employee"), callback_data="employee:none")
    builder.adjust(1)
    return builder.as_markup()


def rating_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=f"{label_for_rating(value)} {value}", callback_data=f"rating:{value}")
                for value in range(1, 6)
            ]
        ]
    )


def comment_keyboard(language: str | None) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t(language, "write_comment"), callback_data="comment:write"),
                InlineKeyboardButton(text=t(language, "skip"), callback_data="comment:skip"),
            ]
        ]
    )


def confirm_keyboard(language: str | None) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t(language, "send"), callback_data="confirm:send"),
                InlineKeyboardButton(text=t(language, "edit"), callback_data="confirm:edit"),
            ]
        ]
    )


def tags_keyboard(selected: set[str], language: str | None) -> InlineKeyboardMarkup:
    lang = normalize_lang(language)
    builder = InlineKeyboardBuilder()
    for tag in IMPLEMENTATION_TAGS:
        label = tag.ru if lang == "ru" else tag.uz
        prefix = "✅ " if tag.code in selected else ""
        builder.button(text=f"{prefix}{label}", callback_data=f"tag:{tag.code}")
    builder.button(text=t(lang, "done"), callback_data="tag:done")
    builder.adjust(1)
    return builder.as_markup()

