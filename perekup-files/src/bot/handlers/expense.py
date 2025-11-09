from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from sqlalchemy import select
from datetime import date
from decimal import Decimal

from src.bot.states import ExpenseCreation
from src.bot.keyboards import (
    get_main_menu,
    get_cancel_keyboard,
    get_expense_categories,
    get_payer_keyboard,
    get_projects_keyboard,
    get_date_keyboard,
    get_confirmation_keyboard
)
from src.db.database import get_db_session
from src.db.models import Project, ProjectStatus, Expense, Payer
from src.utils.formatters import format_money, format_date, parse_money, parse_date
from src.config import get_partner_label

router = Router()


@router.message(F.text == "💸 Добавить расход")
async def add_expense_start(message: Message, state: FSMContext):
    """Начало добавления расхода"""
    await state.set_state(ExpenseCreation.amount)
    await message.answer(
        "💸 <b>Добавление расхода</b>\n\n"
        "Введите сумму (в KZT):\n"
        "<i>Например: 50000, 50 000, 50k</i>",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML"
    )


@router.message(ExpenseCreation.amount)
async def expense_amount(message: Message, state: FSMContext):
    """Ввод суммы расхода"""
    try:
        amount = parse_money(message.text)
        await state.update_data(amount=amount)
        await state.set_state(ExpenseCreation.category)
        await message.answer(
            "📁 Выберите категорию расхода:",
            reply_markup=get_expense_categories()
        )
    except Exception:
        await message.answer(
            "❌ Не удалось распознать сумму. Введите число:\n"
            "Например: 50000 или 50 000"
        )


@router.callback_query(F.data.startswith("expense_cat:"), ExpenseCreation.category)
async def expense_category(callback: CallbackQuery, state: FSMContext):
    """Выбор категории расхода"""
    category = callback.data.split(":")[1]
    await state.update_data(category=category)

    await callback.message.edit_text(f"✅ Категория: {category}")

    # Получаем активные проекты
    async with get_db_session() as session:
        query = select(Project).where(Project.status == ProjectStatus.active)
        result = await session.execute(query)
        projects = result.scalars().all()

        await state.set_state(ExpenseCreation.project)
        await callback.message.answer(
            "🚗 Куда отнести расход?",
            reply_markup=get_projects_keyboard(projects, "expense_project", add_common=True)
        )


@router.callback_query(F.data.startswith("expense_project:"), ExpenseCreation.project)
async def expense_project(callback: CallbackQuery, state: FSMContext):
    """Выбор проекта для расхода"""
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

    await state.set_state(ExpenseCreation.payer)
    await callback.message.answer(
        "👤 Кто оплатил?",
        reply_markup=get_payer_keyboard()
    )


@router.callback_query(F.data.startswith("payer:"), ExpenseCreation.payer)
async def expense_payer(callback: CallbackQuery, state: FSMContext):
    """Выбор плательщика"""
    payer = callback.data.split(":")[1]
    await state.update_data(payer=payer)

    payer_names = {"author": "Жандос", "serik": "Серик", "common": "Общие"}
    await callback.message.edit_text(f"✅ Плательщик: {payer_names[payer]}")

    await state.set_state(ExpenseCreation.date)
    await callback.message.answer(
        "📅 Дата расхода?",
        reply_markup=get_date_keyboard()
    )


@router.callback_query(F.data == "date:today", ExpenseCreation.date)
async def expense_date_today(callback: CallbackQuery, state: FSMContext):
    """Выбор сегодняшней даты"""
    await state.update_data(date=date.today())
    await callback.message.edit_text(f"✅ Дата: {format_date(date.today())}")

    await state.set_state(ExpenseCreation.description)
    await callback.message.answer(
        "📝 Описание расхода?\n\n"
        "<i>Кратко опишите на что потрачено</i>",
        parse_mode="HTML"
    )


@router.message(ExpenseCreation.date)
async def expense_date_custom(message: Message, state: FSMContext):
    """Ввод пользовательской даты"""
    try:
        expense_date = parse_date(message.text)
        await state.update_data(date=expense_date)
        await state.set_state(ExpenseCreation.description)
        await message.answer(
            "📝 Описание расхода?\n\n"
            "<i>Кратко опишите на что потрачено</i>",
            parse_mode="HTML"
        )
    except ValueError:
        await message.answer(
            "❌ Не удалось распознать дату\n\n"
            "Используйте формат: ДД.ММ.ГГГГ",
            reply_markup=get_date_keyboard()
        )


@router.message(ExpenseCreation.description)
async def expense_description(message: Message, state: FSMContext):
    """Ввод описания расхода"""
    await state.update_data(description=message.text)

    # Показываем подтверждение
    data = await state.get_data()
    await state.set_state(ExpenseCreation.confirm)

    payer_names = {"author": "Жандос", "serik": "Серик", "common": "Общие"}

    confirm_text = (
        "📋 <b>Подтверждение расхода</b>\n\n"
        f"💰 Сумма: {format_money(data['amount'])}\n"
        f"📁 Категория: {data['category']}\n"
        f"🚗 Проект: {data['project_name']}\n"
        f"👤 Плательщик: {payer_names[data['payer']]}\n"
        f"📅 Дата: {format_date(data['date'])}\n"
        f"📝 Описание: {data['description']}\n"
    )

    await message.answer(
        confirm_text,
        reply_markup=get_confirmation_keyboard("add_expense"),
        parse_mode="HTML"
    )


@router.callback_query(F.data == "confirm:add_expense", ExpenseCreation.confirm)
async def expense_confirm(callback: CallbackQuery, state: FSMContext):
    """Подтверждение добавления расхода"""
    data = await state.get_data()

    async with get_db_session() as session:
        expense = Expense(
            date=data['date'],
            amount=data['amount'],
            category=data['category'],
            description=data['description'],
            project_id=data.get('project_id'),
            payer=Payer[data['payer']],
            created_by=callback.from_user.id
        )
        session.add(expense)
        await session.commit()

        await callback.message.edit_text(
            f"✅ Расход добавлен!\n\n"
            f"💸 {format_money(expense.amount)}\n"
            f"📁 {expense.category}\n"
            f"🚗 {data['project_name']}"
        )

    await state.clear()
    await callback.message.answer(
        "Выберите следующее действие:",
        reply_markup=get_main_menu()
    )
