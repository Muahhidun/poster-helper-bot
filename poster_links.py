"""Модуль для генерации ссылок на страницы Poster"""
import json
import urllib.parse
from datetime import datetime
from typing import Optional


def generate_receipts_link(
    account_name: str,
    date: datetime,
    search_query: Optional[str] = None
) -> str:
    """
    Генерировать ссылку на страницу чеков с фильтром по дате

    Args:
        account_name: Название аккаунта Poster (например, "pizz-burg")
        date: Дата для фильтра
        search_query: Опциональный поисковый запрос

    Returns:
        Полная ссылка на страницу чеков с фильтрами
    """
    date_str = date.strftime('%Y-%m-%d')

    # Создаем конфигурацию фильтров (как в веб-интерфейсе Poster)
    filter_config = {
        'select': [
            'transaction_id',
            'user_id',
            'date_start_new',
            'date_close',
            'payed_sum',
            'discount',
            'total_profit',
            'status'
        ],
        'filter': [
            {
                'field': 'date_close',
                'condition': '<=',
                'value': date_str,
                'type': 'date_range'
            },
            {
                'field': 'date_close',
                'condition': '>=',
                'value': date_str,
                'type': 'date_range',
                'lastInRow': True
            },
            {
                'field': 'cash_shift_id',
                'condition': 'IN',
                'type': 'predefined',
                'firstInRow': True
            },
            {
                'field': 'user_id',
                'condition': 'IN',
                'type': 'predefined'
            },
            {
                'field': 'pay_types',
                'condition': 'IN',
                'type': 'predefined'
            },
            {
                'field': 'status_selector',
                'condition': 'IN',
                'type': 'predefined'
            },
            {
                'field': 'auto_accept',
                'condition': 'IN',
                'type': 'predefined'
            },
            {}
        ],
        'search': search_query or '',
        'sort': {
            'field': 'transaction_id',
            'type': 'desc'
        },
        'paginate': {
            'rows': '100',
            'page': '1'
        }
    }

    # Кодируем конфигурацию в JSON, затем в URL-безопасный формат
    json_str = json.dumps(filter_config, separators=(',', ':'))
    encoded = urllib.parse.quote(json_str)

    # Формируем полную ссылку
    base_url = f"https://{account_name}.joinposter.com/manage/dash/receipts"
    return f"{base_url}#{encoded}"


def generate_orders_link(account_name: str) -> str:
    """
    Генерировать ссылку на страницу заказов

    Args:
        account_name: Название аккаунта Poster

    Returns:
        Ссылка на страницу заказов
    """
    return f"https://{account_name}.joinposter.com/manage/orders"


def generate_transactions_link(
    account_name: str,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None
) -> str:
    """
    Генерировать ссылку на страницу финансовых транзакций

    Args:
        account_name: Название аккаунта Poster
        date_from: Начальная дата (опционально)
        date_to: Конечная дата (опционально)

    Returns:
        Ссылка на страницу транзакций
    """
    base_url = f"https://{account_name}.joinposter.com/manage/transactions"

    if date_from and date_to:
        date_from_str = date_from.strftime('%d-%m-%Y')
        date_to_str = date_to.strftime('%d-%m-%Y')
        return f"{base_url}/0/0/0/{date_from_str}/{date_to_str}"

    return base_url


# Маппинг telegram_user_id -> account_name
ACCOUNT_NAMES = {
    167084307: "pizz-burg",
    8010984368: "pittsburgh-sushi"  # Второй аккаунт (нужно уточнить название)
}


def get_account_name(telegram_user_id: int) -> str:
    """
    Получить название аккаунта Poster по telegram_user_id

    Args:
        telegram_user_id: ID пользователя Telegram

    Returns:
        Название аккаунта Poster

    Raises:
        ValueError: Если аккаунт не найден
    """
    account = ACCOUNT_NAMES.get(telegram_user_id)
    if not account:
        raise ValueError(f"Аккаунт для пользователя {telegram_user_id} не найден")
    return account


if __name__ == "__main__":
    # Пример использования
    from datetime import datetime

    user_id = 167084307
    account = get_account_name(user_id)
    date = datetime(2025, 10, 28)

    print("📋 Примеры ссылок:")
    print()
    print("1. Чеки за день:")
    print(generate_receipts_link(account, date))
    print()
    print("2. Заказы:")
    print(generate_orders_link(account))
    print()
    print("3. Транзакции за день:")
    print(generate_transactions_link(account, date, date))
