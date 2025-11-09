from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from sqlalchemy import select
from datetime import date
from decimal import Decimal

from src.bot.states import CapitalDeposit, CapitalWithdrawal
from src.bot.keyboards import (
    get_main_menu,
    get_cancel_keyboard,
    get_reports_menu,
    get_settings_menu,
    get_withdrawal_partner_keyboard,
    get_confirmation_keyboard,
    get_projects_keyboard
)
from src.db.database import get_db_session
from src.db.models import Project, ProjectStatus, CapitalOperation, CapitalOperationType
from src.services.report_service import ReportService
from src.utils.formatters import format_money, parse_money

router = Router()


# ============ БАЛАНС КАПИТАЛА ============

@router.message(F.text == "📊 Баланс капитала")
async def show_balance(message: Message):
    """Показать текущий баланс капитала"""
    async with get_db_session() as session:
        balance_data = await ReportService.get_capital_balance(session)
        sold_stats = await ReportService.get_sold_projects_stats(session)
        withdrawals = await ReportService.get_withdrawals_by_partner(session)

        report_text = ReportService.format_daily_report(balance_data, sold_stats, withdrawals)

        await message.answer(report_text, reply_markup=get_main_menu())


# ============ ОТЧЕТЫ ============

@router.message(F.text == "📈 Отчёты")
async def show_reports_menu(message: Message):
    """Меню отчетов"""
    await message.answer(
        "📈 <b>Отчёты</b>\n\nВыберите тип отчёта:",
        reply_markup=get_reports_menu(),
        parse_mode="HTML"
    )


@router.callback_query(F.data == "report:project")
async def report_project_list(callback: CallbackQuery):
    """Список проектов для отчета"""
    async with get_db_session() as session:
        # Получаем все проекты (активные и проданные)
        query = select(Project).order_by(Project.created_at.desc())
        result = await session.execute(query)
        projects = result.scalars().all()

        if not projects:
            await callback.message.edit_text("❌ Нет проектов")
            return

        await callback.message.edit_text(
            "🚗 Выберите проект для отчёта:",
            reply_markup=get_projects_keyboard(projects, "view_project")
        )


@router.callback_query(F.data.startswith("view_project:"))
async def view_project_report(callback: CallbackQuery):
    """Показать отчет по проекту"""
    project_id = int(callback.data.split(":")[1])

    async with get_db_session() as session:
        report = await ReportService.get_project_report(session, project_id)

        if not report:
            await callback.answer("❌ Проект не найден", show_alert=True)
            return

        project = report['project']

        # Формируем отчет
        report_text = f"📊 <b>Отчёт по проекту</b>\n\n"
        report_text += f"🚗 {project.title}\n"
        if project.vin:
            report_text += f"🔢 VIN: {project.vin}\n"
        report_text += f"\n<b>ПОКУПКА:</b>\n"
        report_text += f"📅 Дата: {project.buy_date.strftime('%d.%m.%Y')}\n"
        report_text += f"💰 Сумма: {format_money(project.buy_price)}\n"

        report_text += f"\n<b>РАСХОДЫ НА ПРОЕКТ:</b>\n"
        if report['expenses']:
            for exp in report['expenses']:
                report_text += f"• {exp.date.strftime('%d.%m')} - {format_money(exp.amount)} - {exp.category}\n"
                report_text += f"  {exp.description}\n"
        else:
            report_text += "Нет расходов\n"

        report_text += f"\n💸 Расходы (без покупки): {format_money(report['total_expenses'])}\n"
        report_text += f"💼 Итого вложено: {format_money(report['total_invested'])}\n"

        if project.status == ProjectStatus.sold:
            report_text += f"\n<b>ПРОДАЖА:</b>\n"
            report_text += f"📅 Дата: {project.sell_date.strftime('%d.%m.%Y')}\n"
            report_text += f"💰 Сумма: {format_money(project.sell_price)}\n"
            report_text += f"⏱ Срок: {report['duration_days']} дней\n"

            profit_emoji = "💰" if report['profit'] > 0 else "❌"
            report_text += f"\n{profit_emoji} <b>Прибыль: {format_money(report['profit'])}</b>\n"
            report_text += f"   └ Жандос: {format_money(report['profit_per_partner'])}\n"
            report_text += f"   └ Серик: {format_money(report['profit_per_partner'])}\n"
        else:
            report_text += f"\n📌 Статус: <b>АКТИВНЫЙ</b>\n"

        await callback.message.edit_text(report_text, parse_mode="HTML")


@router.callback_query(F.data == "report:all_projects")
async def report_all_projects(callback: CallbackQuery):
    """Отчёт по всем проектам"""
    async with get_db_session() as session:
        # Активные проекты
        active_query = select(Project).where(Project.status == ProjectStatus.active)
        active_result = await session.execute(active_query)
        active_projects = active_result.scalars().all()

        # Проданные проекты
        sold_query = select(Project).where(Project.status == ProjectStatus.sold)
        sold_result = await session.execute(sold_query)
        sold_projects = sold_result.scalars().all()

        report_text = "📊 <b>Все проекты</b>\n\n"

        if active_projects:
            report_text += f"🚗 <b>Активные проекты ({len(active_projects)}):</b>\n"
            for proj in active_projects:
                report_text += f"• {proj.title} (ID: {proj.id})\n"
                report_text += f"  Куплено: {format_money(proj.buy_price)}\n"
            report_text += "\n"

        if sold_projects:
            report_text += f"✅ <b>Проданные проекты ({len(sold_projects)}):</b>\n"
            for proj in sold_projects:
                report_text += f"• {proj.title} (ID: {proj.id})\n"
                report_text += f"  Куплено: {format_money(proj.buy_price)}\n"
                report_text += f"  Продано: {format_money(proj.sell_price)}\n"
            report_text += "\n"

        if not active_projects and not sold_projects:
            report_text += "Нет проектов\n"

        await callback.message.edit_text(report_text, parse_mode="HTML")


# ============ НАСТРОЙКИ / КАПИТАЛ ============

@router.message(F.text == "⚙️ Настройки")
async def show_settings_menu(message: Message):
    """Меню настроек"""
    await message.answer(
        "⚙️ <b>Операции с капиталом</b>\n\nВыберите действие:",
        reply_markup=get_settings_menu(),
        parse_mode="HTML"
    )


# ============ ПОПОЛНЕНИЕ КАПИТАЛА ============

@router.callback_query(F.data == "capital:deposit")
async def capital_deposit_start(callback: CallbackQuery, state: FSMContext):
    """Начало пополнения капитала"""
    await state.set_state(CapitalDeposit.amount)
    await callback.message.edit_text(
        "💰 <b>Пополнение капитала</b>\n\n"
        "Введите сумму пополнения (в KZT):",
        parse_mode="HTML"
    )


@router.message(CapitalDeposit.amount)
async def deposit_amount(message: Message, state: FSMContext):
    """Ввод суммы пополнения"""
    try:
        amount = parse_money(message.text)
        await state.update_data(amount=amount)
        await state.set_state(CapitalDeposit.notes)
        await message.answer(
            "📝 Примечание?\n\n<i>Или отправьте '-' чтобы пропустить</i>",
            parse_mode="HTML"
        )
    except Exception:
        await message.answer("❌ Не удалось распознать сумму. Введите число:")


@router.message(CapitalDeposit.notes)
async def deposit_notes(message: Message, state: FSMContext):
    """Ввод примечания для пополнения"""
    notes = None if message.text == "-" else message.text
    await state.update_data(notes=notes)

    data = await state.get_data()
    await state.set_state(CapitalDeposit.confirm)

    confirm_text = (
        "📋 <b>Подтверждение пополнения капитала</b>\n\n"
        f"💰 Сумма: {format_money(data['amount'])}\n"
    )
    if notes:
        confirm_text += f"📝 Примечание: {notes}\n"

    await message.answer(
        confirm_text,
        reply_markup=get_confirmation_keyboard("deposit"),
        parse_mode="HTML"
    )


@router.callback_query(F.data == "confirm:deposit", CapitalDeposit.confirm)
async def deposit_confirm(callback: CallbackQuery, state: FSMContext):
    """Подтверждение пополнения"""
    data = await state.get_data()

    async with get_db_session() as session:
        operation = CapitalOperation(
            date=date.today(),
            type=CapitalOperationType.deposit,
            amount=data['amount'],
            who="author",  # Всегда автор пополняет
            notes=data.get('notes'),
            created_by=callback.from_user.id
        )
        session.add(operation)
        await session.commit()

        await callback.message.edit_text(
            f"✅ Капитал пополнен!\n\n"
            f"💰 Сумма: {format_money(operation.amount)}"
        )

    await state.clear()
    await callback.message.answer(
        "Выберите следующее действие:",
        reply_markup=get_main_menu()
    )


# ============ ИНКАССАЦИЯ ============

@router.callback_query(F.data == "capital:withdrawal")
async def capital_withdrawal_start(callback: CallbackQuery, state: FSMContext):
    """Начало инкассации"""
    await state.set_state(CapitalWithdrawal.who)
    await callback.message.edit_text(
        "💸 <b>Инкассация (вывод средств)</b>\n\n"
        "Кто инкассирует?",
        reply_markup=get_withdrawal_partner_keyboard(),
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("withdraw:"), CapitalWithdrawal.who)
async def withdrawal_who(callback: CallbackQuery, state: FSMContext):
    """Выбор кто инкассирует"""
    who = callback.data.split(":")[1]
    await state.update_data(who=who)

    names = {"author": "Жандос", "serik": "Серик"}
    await callback.message.edit_text(f"✅ Инкассирует: {names[who]}")

    await state.set_state(CapitalWithdrawal.amount)
    await callback.message.answer(
        "💰 Сумма для инкассации (в KZT)?",
    )


@router.message(CapitalWithdrawal.amount)
async def withdrawal_amount(message: Message, state: FSMContext):
    """Ввод суммы инкассации"""
    try:
        amount = parse_money(message.text)
        await state.update_data(amount=amount)
        await state.set_state(CapitalWithdrawal.notes)
        await message.answer(
            "📝 Примечание?\n\n<i>Или отправьте '-' чтобы пропустить</i>",
            parse_mode="HTML"
        )
    except Exception:
        await message.answer("❌ Не удалось распознать сумму. Введите число:")


@router.message(CapitalWithdrawal.notes)
async def withdrawal_notes(message: Message, state: FSMContext):
    """Ввод примечания для инкассации"""
    notes = None if message.text == "-" else message.text
    await state.update_data(notes=notes)

    data = await state.get_data()
    await state.set_state(CapitalWithdrawal.confirm)

    names = {"author": "Жандос", "serik": "Серик"}

    confirm_text = (
        "📋 <b>Подтверждение инкассации</b>\n\n"
        f"👤 Кто: {names[data['who']]}\n"
        f"💰 Сумма: {format_money(data['amount'])}\n"
    )
    if notes:
        confirm_text += f"📝 Примечание: {notes}\n"

    await message.answer(
        confirm_text,
        reply_markup=get_confirmation_keyboard("withdrawal"),
        parse_mode="HTML"
    )


@router.callback_query(F.data == "confirm:withdrawal", CapitalWithdrawal.confirm)
async def withdrawal_confirm(callback: CallbackQuery, state: FSMContext):
    """Подтверждение инкассации"""
    data = await state.get_data()

    async with get_db_session() as session:
        operation = CapitalOperation(
            date=date.today(),
            type=CapitalOperationType.withdrawal,
            amount=data['amount'],
            who=data['who'],
            notes=data.get('notes'),
            created_by=callback.from_user.id
        )
        session.add(operation)
        await session.commit()

        names = {"author": "Жандос", "serik": "Серик"}

        await callback.message.edit_text(
            f"✅ Инкассация выполнена!\n\n"
            f"👤 {names[data['who']]}\n"
            f"💰 Сумма: {format_money(operation.amount)}"
        )

    await state.clear()
    await callback.message.answer(
        "Выберите следующее действие:",
        reply_markup=get_main_menu()
    )
