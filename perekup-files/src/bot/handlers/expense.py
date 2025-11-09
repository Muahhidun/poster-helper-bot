from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from sqlalchemy import select
from datetime import date
from decimal import Decimal
import re
from typing import List, Dict

from src.bot.states import ExpenseCreation
from src.bot.keyboards import (
    get_main_menu,
    get_cancel_keyboard,
    get_projects_keyboard,
    get_confirmation_keyboard
)
from src.db.database import get_db_session
from src.db.models import Project, ProjectStatus, Expense, Payer
from src.utils.formatters import format_money
from src.config import PARTNER_AUTHOR_ID, PARTNER_SERIK_ID

router = Router()


def parse_expenses(text: str) -> List[Dict]:
    """
    Парсит список расходов из текста

    Формат:
    3000 Индрайвер
    15000 Заправка кондей

    Returns:
        List[Dict]: Список словарей с amount и description
    """
    expenses = []
    lines = text.strip().split('\n')

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Ищем первое число в строке
        match = re.match(r'^(\d+[\s\d]*?)\s+(.+)$', line)
        if match:
            amount_str = match.group(1).replace(' ', '').replace('\t', '')
            description = match.group(2).strip()

            try:
                amount = Decimal(amount_str)
                expenses.append({
                    'amount': amount,
                    'description': description
                })
            except (ValueError, Exception):
                continue

    return expenses


@router.message(F.text == "💸 Добавить расходы")
async def add_expenses_start(message: Message, state: FSMContext):
    """Начало добавления расходов"""
    await state.set_state(ExpenseCreation.expenses_text)
    await message.answer(
        "💸 <b>Добавление расходов</b>\n\n"
        "Введите список расходов в формате:\n"
        "<code>3000 Индрайвер\n"
        "15000 Заправка кондей\n"
        "3330 Магазин</code>\n\n"
        "<i>Каждая строка: сумма и описание</i>",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML"
    )


@router.message(ExpenseCreation.expenses_text)
async def expenses_text_handler(message: Message, state: FSMContext):
    """Обработка списка расходов"""
    expenses = parse_expenses(message.text)

    if not expenses:
        await message.answer(
            "❌ Не удалось распознать расходы\n\n"
            "Используйте формат:\n"
            "<code>3000 Индрайвер\n"
            "15000 Заправка</code>\n\n"
            "Каждая строка: сумма пробел описание",
            parse_mode="HTML"
        )
        return

    # Сохраняем распарсенные расходы
    await state.update_data(expenses=expenses)

    # Показываем что распознали
    expenses_preview = "\n".join([
        f"• {format_money(e['amount'])} — {e['description']}"
        for e in expenses
    ])

    total = sum(e['amount'] for e in expenses)

    await message.answer(
        f"✅ Распознано расходов: {len(expenses)}\n\n"
        f"{expenses_preview}\n\n"
        f"<b>Итого: {format_money(total)}</b>",
        parse_mode="HTML"
    )

    # Получаем активные проекты
    async with get_db_session() as session:
        query = select(Project).where(Project.status == ProjectStatus.active)
        result = await session.execute(query)
        projects = result.scalars().all()

        await state.set_state(ExpenseCreation.project)
        await message.answer(
            "🚗 Куда отнести эти расходы?",
            reply_markup=get_projects_keyboard(projects, "expense_project", add_common=True)
        )


@router.callback_query(F.data.startswith("expense_project:"), ExpenseCreation.project)
async def expense_project_handler(callback: CallbackQuery, state: FSMContext):
    """Выбор проекта для расходов"""
    project_value = callback.data.split(":")[1]

    if project_value == "common":
        await state.update_data(project_id=None, project_name="Общие расходы")
        await callback.message.edit_text("✅ Выбрано: Общие расходы")
    else:
        project_id = int(project_value)
        async with get_db_session() as session:
            query = select(Project).where(Project.id == project_id)
            result = await session.execute(query)
            project = result.scalar_one()
            await state.update_data(project_id=project_id, project_name=project.title)
            await callback.message.edit_text(f"✅ Выбран проект: {project.title}")

    # Показываем подтверждение
    data = await state.get_data()
    expenses = data['expenses']

    expenses_list = "\n".join([
        f"• {format_money(e['amount'])} — {e['description']}"
        for e in expenses
    ])

    total = sum(e['amount'] for e in expenses)

    # Определяем плательщика по user_id
    user_id = callback.from_user.id
    if user_id == PARTNER_AUTHOR_ID:
        payer_name = "Жандос"
    elif user_id == PARTNER_SERIK_ID:
        payer_name = "Серик"
    else:
        payer_name = "Неизвестный"

    confirm_text = (
        "📋 <b>Подтверждение расходов</b>\n\n"
        f"🚗 Проект: {data['project_name']}\n"
        f"👤 Плательщик: {payer_name}\n"
        f"📅 Дата: сегодня\n\n"
        f"<b>Расходы ({len(expenses)} шт):</b>\n"
        f"{expenses_list}\n\n"
        f"<b>💰 Итого: {format_money(total)}</b>"
    )

    await state.set_state(ExpenseCreation.confirm)
    await callback.message.answer(
        confirm_text,
        reply_markup=get_confirmation_keyboard("add_expenses"),
        parse_mode="HTML"
    )


@router.callback_query(F.data == "confirm:add_expenses", ExpenseCreation.confirm)
async def expenses_confirm(callback: CallbackQuery, state: FSMContext):
    """Подтверждение добавления расходов"""
    data = await state.get_data()
    expenses = data['expenses']
    project_id = data.get('project_id')

    # Определяем плательщика по user_id
    user_id = callback.from_user.id
    if user_id == PARTNER_AUTHOR_ID:
        payer = Payer.author
    elif user_id == PARTNER_SERIK_ID:
        payer = Payer.serik
    else:
        payer = Payer.common

    # Сохраняем все расходы
    async with get_db_session() as session:
        today = date.today()
        saved_count = 0

        for expense_data in expenses:
            expense = Expense(
                date=today,
                amount=expense_data['amount'],
                category="Расход",  # Фиксированная категория
                description=expense_data['description'],
                project_id=project_id,
                payer=payer,
                created_by=user_id
            )
            session.add(expense)
            saved_count += 1

        await session.commit()

        total = sum(e['amount'] for e in expenses)

        await callback.message.edit_text(
            f"✅ Добавлено расходов: {saved_count}\n\n"
            f"💰 На сумму: {format_money(total)}\n"
            f"🚗 {data['project_name']}"
        )

    await state.clear()
    await callback.message.answer(
        "Выберите следующее действие:",
        reply_markup=get_main_menu()
    )
