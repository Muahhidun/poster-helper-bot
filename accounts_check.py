"""
Accounts Check Module - интерактивная сверка счетов двух отделов

Сверяет 3 основных счета:
- Оставил в кассе (на закупы)
- Kaspi Pay
- Халык банк
"""

import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# Счета для сверки (в порядке запроса)
ACCOUNTS_TO_CHECK = [
    "Оставил в кассе (на закупы)",
    "Kaspi Pay",
    "Халык банк",
]


@dataclass
class AccountCheckResult:
    """Результат сверки одного счета"""
    name: str
    poster_pb: float  # Баланс в PizzBurg
    poster_cafe: float  # Баланс в Cafe
    poster_total: float  # Сумма в Poster
    actual: float  # Фактический баланс
    discrepancy: float  # Расхождение (poster - actual)


async def get_poster_balances(telegram_user_id: int) -> Dict[str, Tuple[float, float]]:
    """
    Получить балансы нужных счетов из обоих отделов

    Returns:
        Dict[account_name] -> (pizzburg_balance, cafe_balance)
    """
    from database import get_database
    from poster_client import PosterClient

    db = get_database()
    accounts = db.get_accounts(telegram_user_id)

    balances = {name: [0.0, 0.0] for name in ACCOUNTS_TO_CHECK}

    for account in accounts:
        account_name = account['account_name']
        is_cafe = 'cafe' in account_name.lower() or 'sunday' in account_name.lower()

        client = PosterClient(
            telegram_user_id=telegram_user_id,
            poster_token=account['poster_token'],
            poster_user_id=account['poster_user_id'],
            poster_base_url=account['poster_base_url']
        )

        try:
            poster_accounts = await client.get_accounts()

            for acc in poster_accounts:
                acc_name = acc.get('name', '')
                if acc_name in ACCOUNTS_TO_CHECK:
                    # API возвращает в тиынах, делим на 100
                    balance = float(acc.get('balance', 0)) / 100
                    if is_cafe:
                        balances[acc_name][1] = balance
                    else:
                        balances[acc_name][0] = balance

        finally:
            await client.close()

    # Конвертируем в tuple
    return {name: (pb, cafe) for name, (pb, cafe) in balances.items()}


def calculate_all_discrepancies(
    poster_balances: Dict[str, Tuple[float, float]],
    actual_balances: Dict[str, float]
) -> List[AccountCheckResult]:
    """
    Рассчитать расхождения по всем счетам

    Args:
        poster_balances: {account_name: (pb_balance, cafe_balance)}
        actual_balances: {account_name: actual_balance}

    Returns:
        List of AccountCheckResult
    """
    results = []

    for name in ACCOUNTS_TO_CHECK:
        pb, cafe = poster_balances.get(name, (0, 0))
        poster_total = pb + cafe
        actual = actual_balances.get(name, 0)
        discrepancy = poster_total - actual

        results.append(AccountCheckResult(
            name=name,
            poster_pb=pb,
            poster_cafe=cafe,
            poster_total=poster_total,
            actual=actual,
            discrepancy=discrepancy
        ))

    return results


def format_discrepancy_report(results: List[AccountCheckResult]) -> str:
    """
    Форматировать отчет о расхождениях
    """
    lines = ["📊 Результаты сверки\n"]

    total_discrepancy = 0

    for r in results:
        # Определяем статус
        if abs(r.discrepancy) < 1:
            status = "✅"
            disc_str = "сходится"
        elif r.discrepancy > 0:
            # В Poster больше чем по факту = недостача
            status = "🔴"
            disc_str = f"недостача {r.discrepancy:,.0f}₸"
        else:
            # В Poster меньше чем по факту = излишек
            status = "🔴"
            disc_str = f"излишек {abs(r.discrepancy):,.0f}₸"

        lines.append(f"{status} {r.name}: {disc_str}")
        lines.append("")

        total_discrepancy += r.discrepancy

    # Итого
    lines.append("─" * 25)
    if abs(total_discrepancy) < 1:
        lines.append("✅ Всё сходится!")
    elif total_discrepancy > 0:
        lines.append(f"Общая недостача: {total_discrepancy:,.0f}₸")
    else:
        lines.append(f"Общий излишек: {abs(total_discrepancy):,.0f}₸")

    return "\n".join(lines)


def get_short_name(account_name: str) -> str:
    """Короткое название для запроса"""
    if "закуп" in account_name.lower():
        return "Закуп"
    elif "kaspi" in account_name.lower():
        return "Kaspi Pay"
    elif "халык" in account_name.lower():
        return "Халык"
    return account_name


async def send_accounts_check_reminder(telegram_user_id: int, app):
    """Отправка напоминания о сверке счетов в 22:30"""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    try:
        keyboard = [
            [InlineKeyboardButton("🔄 Сверка счетов", callback_data="accounts_check_start")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await app.bot.send_message(
            chat_id=telegram_user_id,
            text="⏰ **Пора сверить счета!**\n\n"
                 "Нажмите кнопку ниже или воспользуйтесь кнопкой в главном меню.",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

        logger.info(f"✅ Отправлено напоминание о сверке счетов пользователю {telegram_user_id}")

    except Exception as e:
        logger.error(f"❌ Ошибка отправки напоминания о сверке счетов: {e}")
