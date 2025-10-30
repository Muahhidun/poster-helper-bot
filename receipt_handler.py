"""Модуль для обработки чеков: распознавание и удаление"""
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from pathlib import Path

from receipt_ocr import recognize_receipt, parse_date_time
from poster_client import PosterClient
from poster_links import generate_receipts_link, get_account_name

logger = logging.getLogger(__name__)


async def find_orders_by_receipt(
    telegram_user_id: int,
    receipt_data: Dict
) -> List[Dict]:
    """
    Найти заказы в Poster по данным чека

    Args:
        telegram_user_id: ID пользователя Telegram
        receipt_data: Данные чека (date, time, amount)

    Returns:
        Список найденных заказов
    """
    try:
        # Парсим дату и время
        date_str = receipt_data['date']
        time_str = receipt_data['time']
        amount = receipt_data['amount']  # в тийинах

        # Создаем datetime объект
        order_datetime = parse_date_time(date_str, time_str)

        # ВАЖНО: Время на чеке опережает время в Poster API на 3 часа
        # (Poster API возвращает время в UTC или UTC+3, чек печатается в UTC+6)
        # Вычитаем 3 часа из времени чека и ищем ±10 минут
        adjusted_datetime = order_datetime - timedelta(hours=3)
        time_from = adjusted_datetime - timedelta(minutes=10)
        time_to = adjusted_datetime + timedelta(minutes=10)

        # Получить транзакции за день
        poster_client = PosterClient(telegram_user_id)
        result = await poster_client._request('GET', 'dash.getTransactions', params={
            'dateFrom': date_str.replace('-', ''),
            'dateTo': date_str.replace('-', '')
        })

        transactions = result.get('response', [])
        await poster_client.close()

        # Фильтровать заказы по времени и сумме
        matching_orders = []

        for tx in transactions:
            # Проверяем статус: закрытый заказ (status='2')
            if tx.get('status') != '2':
                continue

            # Проверяем сумму (точное совпадение)
            tx_amount = int(tx.get('payed_sum', 0))
            if tx_amount != amount:
                continue

            # Проверяем время (±10 минут)
            tx_time_str = tx.get('date_close_date', '')
            if not tx_time_str:
                continue

            # Парсим время заказа (формат: "2025-10-10 20:49:00")
            try:
                tx_datetime = datetime.strptime(tx_time_str, '%Y-%m-%d %H:%M:%S')
            except ValueError:
                logger.warning(f"Не удалось распарсить дату заказа: {tx_time_str}")
                continue

            # Проверяем попадание в диапазон
            if time_from <= tx_datetime <= time_to:
                matching_orders.append(tx)

        logger.info(
            f"🔍 Поиск заказов по чеку: дата={date_str}, время на чеке={time_str}, "
            f"скорректированное время={adjusted_datetime.strftime('%H:%M')} (±10 мин), "
            f"сумма={amount/100:,.0f}₸ → найдено {len(matching_orders)} заказ(а/ов)"
        )

        return matching_orders

    except Exception as e:
        logger.error(f"❌ Ошибка поиска заказов по чеку: {e}", exc_info=True)
        return []


async def delete_order_by_id(telegram_user_id: int, transaction_id: int) -> bool:
    """
    Удалить заказ по ID

    Args:
        telegram_user_id: ID пользователя Telegram
        transaction_id: ID транзакции для удаления

    Returns:
        True если успешно удалено
    """
    try:
        poster_client = PosterClient(telegram_user_id)
        success = await poster_client.remove_transaction(transaction_id)
        await poster_client.close()
        return success
    except Exception as e:
        logger.error(f"❌ Ошибка удаления заказа {transaction_id}: {e}", exc_info=True)
        return False


async def process_receipt_photo(
    telegram_user_id: int,
    image_path: str
) -> Dict:
    """
    Обработать фото чека: распознать и найти заказы

    Args:
        telegram_user_id: ID пользователя Telegram
        image_path: Путь к фото чека

    Returns:
        Dict с результатом:
        - success: bool
        - receipt_data: Dict (если распознано)
        - orders: List[Dict] (если найдены)
        - error: str (если ошибка)
    """
    try:
        # Распознать чек
        logger.info(f"📸 Распознаём чек из {image_path}...")
        receipt_result = await recognize_receipt(image_path)

        if not receipt_result.get('success'):
            return {
                'success': False,
                'error': receipt_result.get('error', 'Не удалось распознать чек')
            }

        receipt_data = {
            'date': receipt_result['date'],
            'time': receipt_result['time'],
            'amount': receipt_result['amount']
        }

        logger.info(
            f"✅ Чек распознан: дата={receipt_data['date']}, "
            f"время={receipt_data['time']}, сумма={receipt_data['amount']/100:,.0f}₸"
        )

        # Найти заказы
        orders = await find_orders_by_receipt(telegram_user_id, receipt_data)

        return {
            'success': True,
            'receipt_data': receipt_data,
            'orders': orders
        }

    except Exception as e:
        logger.error(f"❌ Ошибка обработки фото чека: {e}", exc_info=True)
        return {
            'success': False,
            'error': str(e)
        }


def format_order_details(order: Dict) -> str:
    """
    Форматировать детали заказа для отображения

    Args:
        order: Данные заказа из Poster

    Returns:
        Форматированная строка с деталями
    """
    tx_id = order.get('transaction_id', 'N/A')
    date_close_raw = order.get('date_close_date', 'N/A')

    # Конвертируем время из Poster API (UTC+3) в локальный часовой пояс Алматы (UTC+5)
    # Разница: +2 часа
    try:
        dt = datetime.strptime(date_close_raw, '%Y-%m-%d %H:%M:%S')
        dt_local = dt + timedelta(hours=2)
        date_close = dt_local.strftime('%Y-%m-%d %H:%M:%S')
    except (ValueError, TypeError):
        date_close = date_close_raw

    # Суммы оплаты
    total = int(order.get('payed_sum', 0)) / 100
    cash = int(order.get('payed_cash', 0)) / 100
    card = int(order.get('payed_card', 0)) / 100

    # Статус
    status = order.get('status', 'N/A')
    status_text = {
        '0': 'Открыт',
        '1': 'Удалён',
        '2': 'Закрыт'
    }.get(status, f'Статус {status}')

    details = (
        f"🧾 **Заказ #{tx_id}**\n"
        f"📅 Дата закрытия: {date_close}\n"
        f"💰 Сумма: {total:,.0f}₸\n"
    )

    # Показываем методы оплаты если они есть
    if cash > 0:
        details += f"💵 Наличные: {cash:,.0f}₸\n"
    if card > 0:
        details += f"💳 Картой: {card:,.0f}₸\n"

    details += f"📊 Статус: {status_text}"

    return details
