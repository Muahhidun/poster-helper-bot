"""Обработчики диалога расчета зарплат в 21:30"""
import re
import logging
from datetime import datetime
from typing import Dict, Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, Application
from database import get_database

logger = logging.getLogger(__name__)


# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===

def parse_staff_names(text: str) -> Optional[Dict[str, str]]:
    """
    Парсинг текста формата "помощник X, донерщик Y"

    Поддерживаемые форматы:
    - "помощник Сержан, донерщик Бека"
    - "донерщик Бека, помощник Сержан"
    - "Сержан Бека" (первый = помощник, второй = донерщик)

    Returns:
        Dict с ключами 'assistant' и 'doner' или None при ошибке
    """
    text = text.strip()

    # Попытка 1: Распознать ключевые слова
    doner_match = re.search(r'донер[щш]ик[:\s]+(\w+)', text, re.IGNORECASE)
    assistant_match = re.search(r'помо[щш]ник[:\s]+(\w+)', text, re.IGNORECASE)

    if doner_match and assistant_match:
        return {
            'assistant': assistant_match.group(1).strip().capitalize(),
            'doner': doner_match.group(1).strip().capitalize()
        }

    # Попытка 2: Простой формат "Имя1 Имя2"
    words = text.split()
    if len(words) >= 2:
        return {
            'assistant': words[0].strip().capitalize(),
            'doner': words[1].strip().capitalize()
        }

    # Ошибка парсинга
    return None


async def save_mentioned_employees(
    telegram_user_id: int,
    cashiers: list,
    doner: str,
    assistant: str
) -> None:
    """
    Сохранить упомянутых сотрудников в БД

    Args:
        telegram_user_id: ID пользователя
        cashiers: Список имен кассиров
        doner: Имя донерщика
        assistant: Имя помощника
    """
    db = get_database()
    today = datetime.now().strftime("%Y-%m-%d")

    # Сохранить кассиров
    for cashier_name in cashiers:
        db.add_employee(telegram_user_id, cashier_name, 'cashier', today)

    # Сохранить донерщика
    if doner:
        db.add_employee(telegram_user_id, doner, 'doner_maker', today)

    # Сохранить помощника
    if assistant:
        db.add_employee(telegram_user_id, assistant, 'assistant', today)

    logger.info(f"✅ Сохранены сотрудники для пользователя {telegram_user_id}")


# === ОБРАБОТЧИКИ ДИАЛОГА ===

async def handle_salary_flow_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало диалога расчета зарплат"""
    query = update.callback_query
    await query.answer()

    # Инициализировать состояние
    context.user_data['salary_flow'] = {
        'step': 'waiting_cashier_count'
    }

    # Показать кнопки выбора количества кассиров
    keyboard = [
        [
            InlineKeyboardButton("2 кассира", callback_data="salary_flow_cashiers_2"),
            InlineKeyboardButton("3 кассира", callback_data="salary_flow_cashiers_3")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        "💰 **Расчет зарплат**\n\n"
        "Сколько было сегодня кассиров?",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )


async def handle_salary_flow_cashiers(update: Update, context: ContextTypes.DEFAULT_TYPE, cashier_count: int):
    """Обработка выбора количества кассиров"""
    query = update.callback_query
    await query.answer()

    # Сохранить количество кассиров
    context.user_data['salary_flow']['cashier_count'] = cashier_count
    context.user_data['salary_flow']['step'] = 'waiting_cashier_names'

    await query.edit_message_text(
        f"✅ Выбрано: **{cashier_count} кассира**\n\n"
        f"Введите имена кассиров через пробел:\n"
        f"_Например: Меруерт Вика Батима_",
        parse_mode='Markdown'
    )


async def handle_cashier_names(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    """Обработка ввода имен кассиров"""
    salary_flow = context.user_data.get('salary_flow', {})
    cashier_count = salary_flow.get('cashier_count')

    # Парсинг имен
    cashier_names = [name.strip().capitalize() for name in text.split() if name.strip()]

    # Валидация количества
    if len(cashier_names) != cashier_count:
        await update.message.reply_text(
            f"❌ Вы указали {len(cashier_names)} имен(и), а нужно {cashier_count}.\n\n"
            f"Попробуйте еще раз, введите {cashier_count} имен(и) через пробел:",
            parse_mode='Markdown'
        )
        return

    # Сохранить имена
    salary_flow['cashier_names'] = cashier_names
    salary_flow['step'] = 'waiting_assistant_time'

    # Показать кнопки выбора времени помощника
    keyboard = [
        [
            InlineKeyboardButton("10:00", callback_data="salary_flow_assistant_10"),
            InlineKeyboardButton("12:00", callback_data="salary_flow_assistant_12"),
            InlineKeyboardButton("14:00", callback_data="salary_flow_assistant_14")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    names_str = ", ".join(cashier_names)
    await update.message.reply_text(
        f"✅ Принято!\n"
        f"Кассиры: **{names_str}**\n\n"
        f"Во сколько вышел помощник донерщика?",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )


async def handle_assistant_time(update: Update, context: ContextTypes.DEFAULT_TYPE, time: str):
    """Обработка выбора времени помощника"""
    query = update.callback_query
    await query.answer()

    # Сохранить время
    context.user_data['salary_flow']['assistant_time'] = time
    context.user_data['salary_flow']['step'] = 'waiting_staff_names'

    await query.edit_message_text(
        f"✅ Время выхода помощника: **{time}**\n\n"
        f"Укажите донерщика и помощника:\n"
        f"_Например: помощник Сержан, донерщик Бека_\n"
        f"_Или просто: Сержан Бека_",
        parse_mode='Markdown'
    )


async def handle_staff_names(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    """Обработка ввода имен донерщика и помощника"""
    staff = parse_staff_names(text)

    if not staff:
        await update.message.reply_text(
            "❌ Не удалось распознать имена.\n\n"
            "Попробуйте формат:\n"
            "• помощник Сержан, донерщик Бека\n"
            "• или просто: Сержан Бека"
        )
        return

    # Сохранить имена
    salary_flow = context.user_data['salary_flow']
    salary_flow['doner_name'] = staff['doner']
    salary_flow['assistant_name'] = staff['assistant']
    salary_flow['step'] = 'calculating'

    # Показать что начинаем расчет
    await update.message.reply_text(
        f"✅ Принято!\n\n"
        f"Донерщик: **{staff['doner']}**\n"
        f"Помощник: **{staff['assistant']}**\n\n"
        f"⏳ Рассчитываю зарплаты...",
        parse_mode='Markdown'
    )

    # Выполнить расчет
    await execute_salary_calculation(update, context)


async def execute_salary_calculation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выполнить расчет зарплат и вывести результат БЕЗ конфиденциальных данных"""
    telegram_user_id = update.effective_user.id
    salary_flow = context.user_data.get('salary_flow', {})

    try:
        from cashier_salary import calculate_and_create_cashier_salary
        from doner_salary import calculate_and_create_doner_salary

        # 1. Рассчитать зарплату кассиров
        cashier_result = await calculate_and_create_cashier_salary(
            telegram_user_id=telegram_user_id,
            cashier_count=salary_flow['cashier_count'],
            cashier_names=salary_flow['cashier_names']
        )

        # 2. Рассчитать зарплату донерщика
        doner_result = await calculate_and_create_doner_salary(
            telegram_user_id=telegram_user_id,
            doner_name=salary_flow['doner_name'],
            assistant_name=salary_flow['assistant_name'],
            assistant_start_time=salary_flow['assistant_time']
        )

        # 3. Сохранить сотрудников в БД
        await save_mentioned_employees(
            telegram_user_id,
            cashiers=salary_flow['cashier_names'],
            doner=salary_flow['doner_name'],
            assistant=salary_flow['assistant_name']
        )

        # 4. Сформировать МИНИМАЛЬНЫЙ отчет (БЕЗ выручки и донеров)
        message_lines = ["✅ **Зарплаты рассчитаны и транзакции созданы**\n"]

        if cashier_result.get('success'):
            message_lines.append("👥 **Кассиры:**")
            for salary_info in cashier_result.get('salaries', []):
                formatted_salary = f"{salary_info['salary']:,}".replace(',', ' ')
                message_lines.append(f"• {salary_info['name']} - {formatted_salary}₸")
            message_lines.append("")

        if doner_result.get('success'):
            message_lines.append("🌮 **Донерщик:**")
            formatted_doner_salary = f"{doner_result['salary']:,}".replace(',', ' ')
            message_lines.append(f"• {doner_result['doner_name']} - {formatted_doner_salary}₸")
            message_lines.append("")

            message_lines.append("👷 **Помощник:**")
            formatted_assistant_salary = f"{doner_result['assistant_salary']:,}".replace(',', ' ')
            message_lines.append(f"• {doner_result['assistant_name']} - {formatted_assistant_salary}₸")

        await update.message.reply_text(
            '\n'.join(message_lines),
            parse_mode='Markdown'
        )

        # Очистить состояние
        context.user_data.pop('salary_flow', None)

    except Exception as e:
        logger.error(f"❌ Ошибка расчета зарплат: {e}", exc_info=True)
        await update.message.reply_text(
            "❌ Произошла ошибка при расчете зарплат.\n"
            "Попробуйте позже или обратитесь в поддержку."
        )
        # Не очищаем состояние, чтобы можно было повторить


# === ПЛАНИРОВЩИК ===

async def send_salary_reminder_for_user(telegram_user_id: int, app: Application):
    """Отправка напоминания о расчете зарплат в 21:30"""
    try:
        keyboard = [
            [InlineKeyboardButton("💰 Рассчитать зарплаты", callback_data="salary_flow_start")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await app.bot.send_message(
            chat_id=telegram_user_id,
            text="⏰ **Пора рассчитать зарплаты!**\n\n"
                 "Нажмите кнопку ниже, чтобы начать диалог расчета зарплат за сегодня.",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

        logger.info(f"✅ Отправлено напоминание о зарплатах пользователю {telegram_user_id}")

    except Exception as e:
        logger.error(f"❌ Ошибка отправки напоминания о зарплатах пользователю {telegram_user_id}: {e}")
