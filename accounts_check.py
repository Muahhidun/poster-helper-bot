"""
Accounts Check Module - сверка счетов двух отделов

Суммирует балансы одноименных счетов из PizzBurg и Pizzburg-cafe (SunDay)
для сравнения с фактическими остатками.
"""

import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# Маппинг счетов между отделами (имена могут немного отличаться)
ACCOUNT_MAPPING = {
    # PizzBurg name -> Pizzburg-cafe name (если отличается)
    "Kaspi Pay": "Kaspi Pay",
    "Денежный ящик (Кассира)": "Денежный ящик (Кассира)",
    "Инкассация (вечером)": "Инкассация (вечером)",
    "Оставил в кассе (на закупы)": "Оставил в кассе (на закупы)",
    "Деньги дома (отложенные)": "Деньги дома (отложенные)",
    "Wolt доставка": "Wolt доставка",
    "Халык банк": "Халык банк",
    "Форте банк": "Форте банк",
    "Прибыль": "Прибыль",
    "На налоги": "На налоги",
}

# Счета, которые физически общие для двух отделов
SHARED_PHYSICAL_ACCOUNTS = [
    "Kaspi Pay",
    "Халык банк",
    "Форте банк",
    "Деньги дома (отложенные)",
    "Прибыль",
]


@dataclass
class AccountBalance:
    """Баланс счета"""
    account_id: int
    name: str
    balance: float
    account_type: str  # 'cash' or 'bank'


@dataclass
class CombinedAccountBalance:
    """Объединенный баланс из двух отделов"""
    name: str
    pizzburg_balance: float
    cafe_balance: float
    total_poster: float
    is_shared: bool  # True если физически один счет


async def get_all_account_balances(telegram_user_id: int) -> Tuple[
    List[AccountBalance],  # PizzBurg accounts
    List[AccountBalance],  # Pizzburg-cafe accounts
]:
    """
    Получить балансы счетов из обоих отделов

    Returns:
        Tuple of (pizzburg_accounts, cafe_accounts)
    """
    from database import get_database
    from poster_client import PosterClient

    db = get_database()
    accounts = db.get_accounts(telegram_user_id)

    pizzburg_balances = []
    cafe_balances = []

    for account in accounts:
        account_name = account['account_name']

        client = PosterClient(
            telegram_user_id=telegram_user_id,
            poster_token=account['poster_token'],
            poster_user_id=account['poster_user_id'],
            poster_base_url=account['poster_base_url']
        )

        try:
            poster_accounts = await client.get_accounts()

            balances = []
            for acc in poster_accounts:
                balance = AccountBalance(
                    account_id=int(acc.get('account_id', 0)),
                    name=acc.get('name', 'Unknown'),
                    balance=float(acc.get('balance', 0)),
                    account_type=acc.get('type', 'cash')
                )
                balances.append(balance)

            # Определяем какой это аккаунт
            if 'cafe' in account_name.lower() or 'sunday' in account_name.lower():
                cafe_balances = balances
            else:
                pizzburg_balances = balances

        finally:
            await client.close()

    return pizzburg_balances, cafe_balances


def combine_account_balances(
    pizzburg_accounts: List[AccountBalance],
    cafe_accounts: List[AccountBalance]
) -> List[CombinedAccountBalance]:
    """
    Объединить балансы из двух отделов

    Для физически общих счетов (Kaspi Pay, Халык банк) показываем сумму.
    """
    combined = {}

    # Добавляем счета PizzBurg
    for acc in pizzburg_accounts:
        name = acc.name
        combined[name] = CombinedAccountBalance(
            name=name,
            pizzburg_balance=acc.balance,
            cafe_balance=0,
            total_poster=acc.balance,
            is_shared=name in SHARED_PHYSICAL_ACCOUNTS
        )

    # Добавляем/объединяем счета Pizzburg-cafe
    for acc in cafe_accounts:
        name = acc.name
        if name in combined:
            combined[name].cafe_balance = acc.balance
            combined[name].total_poster = combined[name].pizzburg_balance + acc.balance
        else:
            combined[name] = CombinedAccountBalance(
                name=name,
                pizzburg_balance=0,
                cafe_balance=acc.balance,
                total_poster=acc.balance,
                is_shared=name in SHARED_PHYSICAL_ACCOUNTS
            )

    return list(combined.values())


def format_accounts_for_check(combined: List[CombinedAccountBalance]) -> str:
    """
    Форматировать счета для сверки

    Показывает:
    - Счета с разбивкой по отделам
    - Итого в Poster
    - Поле для ввода фактического остатка
    """
    lines = ["📊 *Сверка счетов*\n"]

    # Сначала общие физические счета
    shared = [c for c in combined if c.is_shared]
    if shared:
        lines.append("*Общие счета (один физический счет):*")
        for acc in sorted(shared, key=lambda x: x.name):
            lines.append(f"  `{acc.name}`")
            lines.append(f"    PizzBurg: {acc.pizzburg_balance:,.0f}₸")
            lines.append(f"    Cafe: {acc.cafe_balance:,.0f}₸")
            lines.append(f"    *ИТОГО в Poster: {acc.total_poster:,.0f}₸*")
            lines.append("")

    # Отдельные счета (разные физические)
    separate = [c for c in combined if not c.is_shared]
    if separate:
        lines.append("*Раздельные счета:*")
        for acc in sorted(separate, key=lambda x: x.name):
            if acc.pizzburg_balance != 0:
                lines.append(f"  `{acc.name}` (PB): {acc.pizzburg_balance:,.0f}₸")
            if acc.cafe_balance != 0:
                lines.append(f"  `{acc.name}` (Cafe): {acc.cafe_balance:,.0f}₸")
        lines.append("")

    return "\n".join(lines)


def format_accounts_simple(combined: List[CombinedAccountBalance]) -> str:
    """
    Простой формат для сверки - только общие счета с итогами
    """
    lines = ["📊 Сверка счетов\n"]
    lines.append("Общие счета (сумма двух отделов):\n")

    shared = [c for c in combined if c.is_shared]
    for acc in sorted(shared, key=lambda x: x.name):
        lines.append(f"  {acc.name}")
        lines.append(f"    PB: {acc.pizzburg_balance:,.0f}₸  Cafe: {acc.cafe_balance:,.0f}₸")
        lines.append(f"    ИТОГО: {acc.total_poster:,.0f}₸\n")

    lines.append("\nВведите фактические остатки через бота")
    lines.append("или скопируйте итоги для сравнения.")

    return "\n".join(lines)


async def get_accounts_summary(telegram_user_id: int) -> str:
    """
    Получить сводку по счетам для быстрой сверки

    Returns:
        Форматированная строка с балансами
    """
    try:
        pizzburg, cafe = await get_all_account_balances(telegram_user_id)
        combined = combine_account_balances(pizzburg, cafe)
        return format_accounts_simple(combined)
    except Exception as e:
        logger.error(f"Failed to get accounts summary: {e}", exc_info=True)
        return f"Ошибка получения счетов: {str(e)}"


async def calculate_discrepancy(
    telegram_user_id: int,
    account_name: str,
    actual_balance: float
) -> Tuple[float, str]:
    """
    Рассчитать расхождение по счету

    Args:
        telegram_user_id: ID пользователя
        account_name: Имя счета (например "Kaspi Pay")
        actual_balance: Фактический остаток

    Returns:
        Tuple of (discrepancy, formatted_message)
        Положительное значение = в Poster больше чем по факту
    """
    try:
        pizzburg, cafe = await get_all_account_balances(telegram_user_id)
        combined = combine_account_balances(pizzburg, cafe)

        # Найти счет
        for acc in combined:
            if acc.name.lower() == account_name.lower():
                discrepancy = acc.total_poster - actual_balance

                if discrepancy > 0:
                    sign = "+"
                    status = "переплата в Poster"
                elif discrepancy < 0:
                    sign = ""
                    status = "недостача в Poster"
                else:
                    sign = ""
                    status = "сходится"

                message = (
                    f"📊 Сверка: {acc.name}\n\n"
                    f"В Poster: {acc.total_poster:,.0f}₸\n"
                    f"  (PB: {acc.pizzburg_balance:,.0f}₸ + Cafe: {acc.cafe_balance:,.0f}₸)\n"
                    f"По факту: {actual_balance:,.0f}₸\n\n"
                    f"Расхождение: {sign}{discrepancy:,.0f}₸ ({status})"
                )

                return discrepancy, message

        return 0, f"Счет '{account_name}' не найден"

    except Exception as e:
        logger.error(f"Failed to calculate discrepancy: {e}", exc_info=True)
        return 0, f"Ошибка: {str(e)}"
