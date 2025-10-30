"""Модуль для закрытия смены"""
import logging
from datetime import datetime
from typing import Dict, List
from poster_client import PosterClient
from cashier_salary import CashierSalaryCalculator
from doner_salary import DonerSalaryCalculator

logger = logging.getLogger(__name__)


class ShiftClosing:
    """Закрытие смены с отчётом и созданием транзакций"""

    def __init__(self, telegram_user_id: int):
        self.telegram_user_id = telegram_user_id
        self.cashier_calculator = CashierSalaryCalculator(telegram_user_id)
        self.doner_calculator = DonerSalaryCalculator(telegram_user_id)

    async def get_shift_report(self, date: str = None) -> Dict:
        """
        Получить отчёт о смене

        Args:
            date: Дата в формате "YYYYMMDD". Если None, используется сегодня

        Returns:
            Dict с данными:
            - sales_data: данные о продажах (из cashier_salary)
            - doner_data: данные о донерах (из doner_salary)
            - cashier_salary_2: зарплата для 2 кассиров
            - cashier_salary_3: зарплата для 3 кассиров
            - doner_salary: зарплата донерщика
            - date: дата отчёта
        """
        try:
            if date is None:
                date = datetime.now().strftime("%Y%m%d")

            # Получить данные о продажах
            sales_data = await self.cashier_calculator.get_total_sales(date)

            # Получить данные о донерах
            doner_data = await self.doner_calculator.get_doner_sales_count(date)

            # Рассчитать зарплаты
            total_sales = sales_data['total_sales']
            cashier_salary_2 = self.cashier_calculator.calculate_salary(total_sales, 2)
            cashier_salary_3 = self.cashier_calculator.calculate_salary(total_sales, 3)

            doner_count = int(doner_data['total_count'])
            doner_salary = self.doner_calculator.calculate_salary(doner_count)

            logger.info(
                f"📊 Отчёт о смене за {date}: "
                f"продажи={total_sales/100:,.0f}₸, донеров={doner_count}"
            )

            return {
                'success': True,
                'date': date,
                'sales_data': sales_data,
                'doner_data': doner_data,
                'cashier_salary_2': cashier_salary_2,
                'cashier_salary_3': cashier_salary_3,
                'doner_salary': doner_salary
            }

        except Exception as e:
            logger.error(f"❌ Ошибка получения отчёта о смене: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e)
            }

    async def close_shift(
        self,
        cashier_count: int,
        date: str = None
    ) -> Dict:
        """
        Закрыть смену: создать все транзакции зарплат

        Args:
            cashier_count: Количество кассиров (2 или 3)
            date: Дата для расчёта в формате "YYYYMMDD". Если None, используется сегодня

        Returns:
            Dict с результатом:
            - success: bool
            - cashier_transactions: List[int] - ID транзакций кассиров
            - doner_transaction: int - ID транзакции донерщика
            - report: Dict - отчёт о смене
        """
        try:
            # Получить отчёт о смене
            report = await self.get_shift_report(date)

            if not report['success']:
                return report

            # Создать транзакции зарплаты кассиров
            cashier_result = await self.cashier_calculator.create_salary_transactions(
                cashier_count, date
            )

            if not cashier_result['success']:
                return {
                    'success': False,
                    'error': f"Ошибка создания зарплаты кассиров: {cashier_result.get('error')}"
                }

            # Создать транзакцию зарплаты донерщика
            doner_result = await self.doner_calculator.create_salary_transaction(date)

            if not doner_result['success']:
                return {
                    'success': False,
                    'error': f"Ошибка создания зарплаты донерщика: {doner_result.get('error')}"
                }

            logger.info(
                f"✅ Смена закрыта: кассиров={cashier_count}, "
                f"транзакций кассиров={len(cashier_result['transaction_ids'])}, "
                f"транзакция донерщика={doner_result['transaction_id']}"
            )

            return {
                'success': True,
                'cashier_transactions': cashier_result['transaction_ids'],
                'doner_transaction': doner_result['transaction_id'],
                'cashier_count': cashier_count,
                'cashier_salary': cashier_result['salary_per_cashier'],
                'doner_salary': doner_result['salary'],
                'report': report
            }

        except Exception as e:
            logger.error(f"❌ Ошибка закрытия смены: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e)
            }

    def format_shift_report(self, report: Dict) -> str:
        """
        Форматировать отчёт о смене для отправки в Telegram

        Args:
            report: Результат get_shift_report()

        Returns:
            Форматированный текст отчёта
        """
        if not report.get('success'):
            return f"❌ Ошибка получения отчёта: {report.get('error')}"

        sales_data = report['sales_data']
        doner_data = report['doner_data']
        date_obj = datetime.strptime(report['date'], "%Y%m%d")
        date_str = date_obj.strftime("%d.%m.%Y")

        # Форматируем числа с пробелами вместо запятых
        def format_money(amount):
            return f"{amount:,}".replace(',', ' ')

        text = f"📊 **ОТЧЁТ О СМЕНЕ**\n"
        text += f"📅 Дата: {date_str}\n"
        text += f"\n"

        # Продажи
        text += f"💰 **ПРОДАЖИ**\n"
        text += f"├ Общая сумма: {format_money(sales_data['total_sum']//100)}₸\n"
        text += f"├ Наличные: {format_money(sales_data['cash']//100)}₸\n"
        text += f"├ Картой: {format_money(sales_data['card']//100)}₸\n"
        text += f"├ Бонусы (вычитаются): {format_money(sales_data['bonus']//100)}₸\n"
        text += f"└ **ИТОГО для расчёта:** {format_money(sales_data['total_sales']//100)}₸\n"
        text += f"   Закрытых заказов: {sales_data['transactions_count']}\n"
        text += f"\n"

        # Донеры
        text += f"🌮 **ДОНЕРЫ**\n"
        text += f"├ Категория 'Донер': {doner_data['category_count']} шт\n"
        text += f"├ Комбо Донер: {doner_data['combo_count']} шт\n"
        text += f"├ Донерная пицца: {doner_data['pizza_count']} шт\n"
        text += f"└ **ИТОГО:** {doner_data['total_count']} шт\n"
        text += f"\n"

        # Зарплаты
        text += f"💵 **ЗАРПЛАТЫ**\n"
        text += f"👥 Кассиры (2 чел): {format_money(report['cashier_salary_2'])}₸ каждому\n"
        text += f"   Общая сумма: {format_money(report['cashier_salary_2'] * 2)}₸\n"
        text += f"\n"
        text += f"👥👥 Кассиры (3 чел): {format_money(report['cashier_salary_3'])}₸ каждому\n"
        text += f"   Общая сумма: {format_money(report['cashier_salary_3'] * 3)}₸\n"
        text += f"\n"
        text += f"🌮 Донерщик: {format_money(report['doner_salary'])}₸\n"

        return text


async def get_shift_report_for_user(telegram_user_id: int, date: str = None) -> Dict:
    """
    Получить отчёт о смене для пользователя

    Args:
        telegram_user_id: ID пользователя Telegram
        date: Дата в формате "YYYYMMDD". Если None, используется сегодня

    Returns:
        Dict с результатом операции
    """
    shift_closing = ShiftClosing(telegram_user_id)
    return await shift_closing.get_shift_report(date)


async def close_shift_for_user(
    telegram_user_id: int,
    cashier_count: int,
    date: str = None
) -> Dict:
    """
    Закрыть смену для пользователя

    Args:
        telegram_user_id: ID пользователя Telegram
        cashier_count: Количество кассиров (2 или 3)
        date: Дата в формате "YYYYMMDD". Если None, используется сегодня

    Returns:
        Dict с результатом операции
    """
    shift_closing = ShiftClosing(telegram_user_id)
    return await shift_closing.close_shift(cashier_count, date)
