from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from sqlalchemy import select
from datetime import date, datetime
from decimal import Decimal

from src.bot.states import ProjectCreation, ProjectSale
from src.bot.keyboards import (
    get_main_menu,
    get_cancel_keyboard,
    get_projects_keyboard,
    get_date_keyboard,
    get_confirmation_keyboard
)
from src.db.database import get_db_session
from src.db.models import Project, ProjectStatus, Expense, Income
from src.utils.formatters import format_money, format_date, parse_money, parse_date
from src.config import get_partner_label

router = Router()


# ============ СОЗДАНИЕ ПРОЕКТА ============

@router.message(F.text == "➕ Создать проект")
async def create_project_start(message: Message, state: FSMContext):
    """Начало создания проекта"""
    await state.set_state(ProjectCreation.title)
    await message.answer(
        "🚗 <b>Создание нового проекта</b>\n\n"
        "Введите название автомобиля:\n"
        "<i>Например: Toyota Camry, Nissan Maxima</i>",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML"
    )


@router.message(ProjectCreation.title)
async def project_title(message: Message, state: FSMContext):
    """Ввод названия проекта"""
    await state.update_data(title=message.text)
    await state.set_state(ProjectCreation.buy_date)
    await message.answer(
        "📅 Дата покупки?\n\n"
        "Используйте кнопку <b>Сегодня</b> или введите дату в формате ДД.ММ.ГГГГ",
        reply_markup=get_date_keyboard(),
        parse_mode="HTML"
    )


@router.callback_query(F.data == "date:today", ProjectCreation.buy_date)
async def project_buy_date_today(callback: CallbackQuery, state: FSMContext):
    """Выбор сегодняшней даты"""
    await state.update_data(buy_date=date.today())
    await callback.message.edit_text(
        f"✅ Дата покупки: {format_date(date.today())}"
    )
    await state.set_state(ProjectCreation.buy_price)
    await callback.message.answer(
        "💰 Сумма покупки (в KZT)?\n\n"
        "<i>Можно вводить: 3200000, 3 200 000, 3.2m</i>",
        parse_mode="HTML"
    )


@router.message(ProjectCreation.buy_date)
async def project_buy_date_custom(message: Message, state: FSMContext):
    """Ввод пользовательской даты"""
    try:
        buy_date = parse_date(message.text)
        await state.update_data(buy_date=buy_date)
        await state.set_state(ProjectCreation.buy_price)
        await message.answer(
            "💰 Сумма покупки (в KZT)?\n\n"
            "<i>Можно вводить: 3200000, 3 200 000, 3.2m</i>",
            parse_mode="HTML"
        )
    except ValueError as e:
        await message.answer(
            f"❌ Не удалось распознать дату: {message.text}\n\n"
            "Используйте формат: ДД.ММ.ГГГГ или кнопку 'Сегодня'",
            reply_markup=get_date_keyboard()
        )


@router.message(ProjectCreation.buy_price)
async def project_buy_price(message: Message, state: FSMContext):
    """Ввод цены покупки"""
    try:
        buy_price = parse_money(message.text)
        await state.update_data(buy_price=buy_price)
        await state.set_state(ProjectCreation.vin)
        await message.answer(
            "🔢 VIN или примечание?\n\n"
            "<i>Можно пропустить, отправив '-'</i>",
            parse_mode="HTML"
        )
    except Exception:
        await message.answer(
            "❌ Не удалось распознать сумму. Введите число:\n"
            "Например: 3200000 или 3 200 000"
        )


@router.message(ProjectCreation.vin)
async def project_vin(message: Message, state: FSMContext):
    """Ввод VIN / примечания"""
    vin = None if message.text == "-" else message.text
    await state.update_data(vin=vin, notes=vin)

    # Показываем подтверждение
    data = await state.get_data()
    await state.set_state(ProjectCreation.confirm)

    confirm_text = (
        "📋 <b>Подтверждение создания проекта</b>\n\n"
        f"🚗 Название: {data['title']}\n"
        f"📅 Дата покупки: {format_date(data['buy_date'])}\n"
        f"💰 Сумма покупки: {format_money(data['buy_price'])}\n"
    )
    if vin:
        confirm_text += f"🔢 VIN: {vin}\n"

    await message.answer(
        confirm_text,
        reply_markup=get_confirmation_keyboard("create_project"),
        parse_mode="HTML"
    )


@router.callback_query(F.data == "confirm:create_project", ProjectCreation.confirm)
async def project_confirm(callback: CallbackQuery, state: FSMContext):
    """Подтверждение создания проекта"""
    data = await state.get_data()

    async with get_db_session() as session:
        project = Project(
            title=data['title'],
            vin=data.get('vin'),
            buy_date=data['buy_date'],
            buy_price=data['buy_price'],
            status=ProjectStatus.active,
            notes=data.get('notes'),
            created_by=callback.from_user.id
        )
        session.add(project)
        await session.commit()
        await session.refresh(project)

        await callback.message.edit_text(
            f"✅ Проект создан!\n\n"
            f"🚗 {project.title}\n"
            f"🆔 ID: {project.id}\n"
            f"💰 Вложено: {format_money(project.buy_price)}"
        )

    await state.clear()
    await callback.message.answer(
        "Выберите следующее действие:",
        reply_markup=get_main_menu()
    )


# ============ ПРОДАЖА ПРОЕКТА ============

@router.message(F.text == "💰 Продать авто")
async def sell_project_start(message: Message, state: FSMContext):
    """Начало продажи проекта"""
    async with get_db_session() as session:
        query = select(Project).where(Project.status == ProjectStatus.active)
        result = await session.execute(query)
        projects = result.scalars().all()

        if not projects:
            await message.answer(
                "❌ Нет активных проектов для продажи",
                reply_markup=get_main_menu()
            )
            return

        await state.set_state(ProjectSale.project)
        await message.answer(
            "🚗 Какой проект продаём?",
            reply_markup=get_projects_keyboard(projects, "sell_project")
        )


@router.callback_query(F.data.startswith("sell_project:"), ProjectSale.project)
async def sell_project_selected(callback: CallbackQuery, state: FSMContext):
    """Выбран проект для продажи"""
    project_id = int(callback.data.split(":")[1])
    await state.update_data(project_id=project_id)

    async with get_db_session() as session:
        query = select(Project).where(Project.id == project_id)
        result = await session.execute(query)
        project = result.scalar_one()

        await callback.message.edit_text(
            f"✅ Выбран: {project.title}"
        )

    await state.set_state(ProjectSale.sell_date)
    await callback.message.answer(
        "📅 Дата продажи?",
        reply_markup=get_date_keyboard()
    )


@router.callback_query(F.data == "date:today", ProjectSale.sell_date)
async def sell_date_today(callback: CallbackQuery, state: FSMContext):
    """Выбор сегодняшней даты продажи"""
    await state.update_data(sell_date=date.today())
    await callback.message.edit_text(
        f"✅ Дата продажи: {format_date(date.today())}"
    )
    await state.set_state(ProjectSale.sell_price)
    await callback.message.answer(
        "💰 Сумма продажи (в KZT)?",
        parse_mode="HTML"
    )


@router.message(ProjectSale.sell_date)
async def sell_date_custom(message: Message, state: FSMContext):
    """Ввод пользовательской даты продажи"""
    try:
        sell_date = parse_date(message.text)
        await state.update_data(sell_date=sell_date)
        await state.set_state(ProjectSale.sell_price)
        await message.answer("💰 Сумма продажи (в KZT)?")
    except ValueError:
        await message.answer(
            "❌ Не удалось распознать дату\n\n"
            "Используйте формат: ДД.ММ.ГГГГ",
            reply_markup=get_date_keyboard()
        )


@router.message(ProjectSale.sell_price)
async def sell_price(message: Message, state: FSMContext):
    """Ввод цены продажи"""
    try:
        sell_price = parse_money(message.text)
        await state.update_data(sell_price=sell_price)

        # Показываем подтверждение с расчетом прибыли
        data = await state.get_data()

        async with get_db_session() as session:
            query = select(Project).where(Project.id == data['project_id'])
            result = await session.execute(query)
            project = result.scalar_one()

            # Расходы на проект
            expenses_query = select(Expense).where(Expense.project_id == project.id)
            expenses_result = await session.execute(expenses_query)
            expenses = expenses_result.scalars().all()
            total_expenses = sum(e.amount for e in expenses)

            total_invested = project.buy_price + total_expenses
            profit = sell_price - total_invested
            profit_per_partner = profit / 2

            duration_days = (data['sell_date'] - project.buy_date).days

            confirm_text = (
                "📋 <b>Подтверждение продажи</b>\n\n"
                f"🚗 Проект: {project.title}\n"
                f"📅 Куплено: {format_date(project.buy_date)} за {format_money(project.buy_price)}\n"
                f"💸 Вложения (без покупки): {format_money(total_expenses)}\n"
                f"💼 Итого вложено: {format_money(total_invested)}\n\n"
                f"📅 Продано: {format_date(data['sell_date'])} за {format_money(sell_price)}\n"
                f"⏱ Срок: {duration_days} дней\n\n"
                f"{'💰' if profit > 0 else '❌'} <b>Прибыль: {format_money(profit)}</b>\n"
                f"   └ Жандос: {format_money(profit_per_partner)}\n"
                f"   └ Серик: {format_money(profit_per_partner)}\n"
            )

            await state.set_state(ProjectSale.confirm)
            await message.answer(
                confirm_text,
                reply_markup=get_confirmation_keyboard("sell_project"),
                parse_mode="HTML"
            )

    except Exception as e:
        await message.answer(
            f"❌ Не удалось распознать сумму. Введите число:\n"
            f"Например: 4350000"
        )


@router.callback_query(F.data == "confirm:sell_project", ProjectSale.confirm)
async def sell_confirm(callback: CallbackQuery, state: FSMContext):
    """Подтверждение продажи проекта"""
    data = await state.get_data()

    async with get_db_session() as session:
        # Обновляем проект
        query = select(Project).where(Project.id == data['project_id'])
        result = await session.execute(query)
        project = result.scalar_one()

        project.sell_date = data['sell_date']
        project.sell_price = data['sell_price']
        project.status = ProjectStatus.sold

        # Создаем доход
        income = Income(
            date=data['sell_date'],
            amount=data['sell_price'],
            source="Продажа авто",
            project_id=project.id,
            notes=f"Продажа {project.title}",
            created_by=callback.from_user.id
        )
        session.add(income)

        await session.commit()

        await callback.message.edit_text(
            f"✅ Проект продан!\n\n"
            f"🚗 {project.title}\n"
            f"💰 Сумма: {format_money(project.sell_price)}"
        )

    await state.clear()
    await callback.message.answer(
        "Выберите следующее действие:",
        reply_markup=get_main_menu()
    )


@router.callback_query(F.data.startswith("cancel:"))
async def cancel_action(callback: CallbackQuery, state: FSMContext):
    """Отмена действия"""
    await state.clear()
    await callback.message.edit_text("❌ Действие отменено")
    await callback.message.answer(
        "Выберите действие:",
        reply_markup=get_main_menu()
    )
