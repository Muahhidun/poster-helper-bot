from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from src.bot.keyboards import get_main_menu

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    """Обработчик команды /start"""
    await state.clear()

    welcome_text = (
        "👋 Добро пожаловать в <b>Авто-Мушарака</b>!\n\n"
        "🚗 Бот для учета перепродажи автомобилей\n\n"
        "Основные функции:\n"
        "• ➕ Создать проект - добавить новый автомобиль\n"
        "• 💸 Добавить расход - фиксировать затраты\n"
        "• 💰 Продать авто - закрыть проект и посчитать прибыль\n"
        "• 📊 Баланс капитала - текущее состояние\n"
        "• 📈 Отчёты - детальная аналитика\n\n"
        "Выберите действие в меню ниже:"
    )

    await message.answer(
        welcome_text,
        reply_markup=get_main_menu(),
        parse_mode="HTML"
    )


@router.message(F.text == "❌ Отмена")
async def cmd_cancel(message: Message, state: FSMContext):
    """Отмена текущего действия"""
    await state.clear()
    await message.answer(
        "❌ Действие отменено",
        reply_markup=get_main_menu()
    )
