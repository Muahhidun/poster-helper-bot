from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from typing import List, Optional
from src.db.models import Project, ProjectStatus


def get_main_menu() -> ReplyKeyboardMarkup:
    """Главное меню"""
    kb = ReplyKeyboardBuilder()
    kb.button(text="🚗 Купить авто")
    kb.button(text="💸 Добавить расходы")
    kb.button(text="💰 Продать авто")
    kb.button(text="📊 Баланс капитала")
    kb.button(text="📈 Отчёты")
    kb.button(text="⚙️ Настройки")
    kb.adjust(2)  # 2 кнопки в ряд
    return kb.as_markup(resize_keyboard=True)


def get_cancel_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура с кнопкой отмены"""
    kb = ReplyKeyboardBuilder()
    kb.button(text="❌ Отмена")
    return kb.as_markup(resize_keyboard=True)


def get_expense_categories() -> InlineKeyboardMarkup:
    """Категории расходов"""
    kb = InlineKeyboardBuilder()
    categories = [
        "Запчасти",
        "Работы",
        "Такси",
        "Сервис",
        "Налоги/учёт",
        "Прочее",
    ]
    for cat in categories:
        kb.button(text=cat, callback_data=f"expense_cat:{cat}")
    kb.adjust(2)
    return kb.as_markup()


def get_payer_keyboard() -> InlineKeyboardMarkup:
    """Выбор плательщика"""
    kb = InlineKeyboardBuilder()
    kb.button(text="Жандос", callback_data="payer:author")
    kb.button(text="Серик", callback_data="payer:serik")
    kb.button(text="Общие", callback_data="payer:common")
    kb.adjust(2)
    return kb.as_markup()


def get_projects_keyboard(
    projects: List[Project],
    callback_prefix: str = "project",
    add_common: bool = False
) -> InlineKeyboardMarkup:
    """
    Клавиатура со списком проектов

    Args:
        projects: Список проектов
        callback_prefix: Префикс для callback_data
        add_common: Добавить кнопку "Общие расходы"
    """
    kb = InlineKeyboardBuilder()

    for project in projects:
        kb.button(
            text=f"{project.title}",
            callback_data=f"{callback_prefix}:{project.id}"
        )

    if add_common:
        kb.button(text="📦 Общие расходы", callback_data=f"{callback_prefix}:common")

    kb.adjust(1)  # По одной кнопке в ряд
    return kb.as_markup()


def get_reports_menu() -> InlineKeyboardMarkup:
    """Меню отчетов"""
    kb = InlineKeyboardBuilder()
    kb.button(text="📊 По проекту", callback_data="report:project")
    kb.button(text="📅 Все проекты", callback_data="report:all_projects")
    kb.button(text="💰 История капитала", callback_data="report:capital_history")
    kb.adjust(1)
    return kb.as_markup()


def get_settings_menu() -> InlineKeyboardMarkup:
    """Меню настроек"""
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Пополнить капитал", callback_data="capital:deposit")
    kb.button(text="💸 Инкассировать", callback_data="capital:withdrawal")
    kb.button(text="📊 История операций", callback_data="capital:history")
    kb.adjust(1)
    return kb.as_markup()


def get_confirmation_keyboard(action: str) -> InlineKeyboardMarkup:
    """Клавиатура подтверждения"""
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Подтвердить", callback_data=f"confirm:{action}")
    kb.button(text="❌ Отмена", callback_data=f"cancel:{action}")
    kb.adjust(2)
    return kb.as_markup()


def get_withdrawal_partner_keyboard() -> InlineKeyboardMarkup:
    """Выбор партнера для инкассации"""
    kb = InlineKeyboardBuilder()
    kb.button(text="Жандос", callback_data="withdraw:author")
    kb.button(text="Серик", callback_data="withdraw:serik")
    kb.adjust(2)
    return kb.as_markup()


def get_date_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура выбора даты"""
    kb = InlineKeyboardBuilder()
    kb.button(text="📅 Сегодня", callback_data="date:today")
    kb.button(text="📝 Ввести дату", callback_data="date:custom")
    kb.adjust(2)
    return kb.as_markup()
