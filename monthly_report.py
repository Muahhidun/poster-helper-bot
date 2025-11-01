"""Модуль для генерации ежемесячных отчётов"""
import logging
from datetime import datetime, timedelta
from typing import Dict, List
from collections import defaultdict
from poster_client import PosterClient
from calendar import monthrange

logger = logging.getLogger(__name__)


class MonthlyReportGenerator:
    """Генератор ежемесячных отчётов"""

    def __init__(self, telegram_user_id: int):
        self.telegram_user_id = telegram_user_id

    async def generate_monthly_report(self) -> Dict:
        """
        Генерировать отчёт за прошлый месяц (1 число 10:00 - последнее число 22:00)

        Returns:
            Dict с данными отчёта и текстом для отправки
        """
        try:
            # Рассчитать даты прошлого месяца
            today = datetime.now()

            # Первое число прошлого месяца
            if today.month == 1:
                month_start = datetime(today.year - 1, 12, 1, 10, 0, 0)
            else:
                month_start = datetime(today.year, today.month - 1, 1, 10, 0, 0)

            # Последнее число прошлого месяца
            last_day = monthrange(month_start.year, month_start.month)[1]
            month_end = datetime(month_start.year, month_start.month, last_day, 22, 0, 0)

            logger.info(f"Генерация месячного отчёта за {month_start} - {month_end}")

            # Получить транзакции
            poster_client = PosterClient(self.telegram_user_id)

            transactions = await poster_client.get_transactions(
                date_from=month_start.strftime("%Y%m%d"),
                date_to=month_end.strftime("%Y%m%d")
            )

            # Получить заказы (dash.getTransactions) для расчёта выручки, чеков, AOV
            orders = await poster_client._request('GET', 'dash.getTransactions', params={
                'dateFrom': month_start.strftime("%Y%m%d"),
                'dateTo': month_end.strftime("%Y%m%d")
            })
            orders = orders.get('response', [])

            # Также получить данные за предыдущий месяц для сравнения
            if month_start.month == 1:
                prev_month_start = datetime(month_start.year - 1, 12, 1, 10, 0, 0)
            else:
                prev_month_start = datetime(month_start.year, month_start.month - 1, 1, 10, 0, 0)

            prev_last_day = monthrange(prev_month_start.year, prev_month_start.month)[1]
            prev_month_end = datetime(prev_month_start.year, prev_month_start.month, prev_last_day, 22, 0, 0)

            prev_transactions = await poster_client.get_transactions(
                date_from=prev_month_start.strftime("%Y%m%d"),
                date_to=prev_month_end.strftime("%Y%m%d")
            )

            prev_orders = await poster_client._request('GET', 'dash.getTransactions', params={
                'dateFrom': prev_month_start.strftime("%Y%m%d"),
                'dateTo': prev_month_end.strftime("%Y%m%d")
            })
            prev_orders = prev_orders.get('response', [])

            await poster_client.close()

            # Анализ транзакций
            report_data = self._analyze_transactions(transactions, month_start, month_end)
            prev_report_data = self._analyze_transactions(prev_transactions, prev_month_start, prev_month_end)

            # Анализ заказов
            orders_data = self._analyze_orders(orders, month_start, month_end)
            prev_orders_data = self._analyze_orders(prev_orders, prev_month_start, prev_month_end)

            # Объединяем данные
            report_data.update(orders_data)
            prev_report_data.update(prev_orders_data)

            # Генерация текста отчёта
            report_text = self._format_report_text(report_data, prev_report_data, month_start, month_end)

            return {
                'success': True,
                'report_text': report_text,
                'data': report_data,
                'prev_data': prev_report_data,
                'period': {
                    'start': month_start,
                    'end': month_end
                }
            }

        except Exception as e:
            logger.error(f"Ошибка генерации месячного отчёта: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e)
            }

    def _analyze_orders(self, orders: List[Dict], period_start: datetime, period_end: datetime) -> Dict:
        """Анализировать заказы для расчёта выручки, чеков, AOV"""

        # Фильтруем только закрытые заказы (status=2) в нужном временном диапазоне
        closed_orders = []
        revenue_by_day = defaultdict(int)  # Выручка по дням

        for order in orders:
            status = order.get('status', '')
            if status != '2':  # Только закрытые заказы
                continue

            # Парсим дату закрытия заказа
            date_close = order.get('date_close_date', '')
            if date_close:
                try:
                    order_datetime = datetime.strptime(date_close, '%Y-%m-%d %H:%M:%S')
                    # Проверяем, что заказ в нужном временном диапазоне (10:00 - 22:00)
                    if 10 <= order_datetime.hour < 22 or (order_datetime.hour == 22 and order_datetime.minute == 0):
                        closed_orders.append(order)

                        # Собираем выручку по дням
                        day_key = order_datetime.strftime('%Y-%m-%d')
                        payed_sum = int(order.get('payed_sum', 0))  # В тийинах
                        revenue_by_day[day_key] += payed_sum
                except ValueError:
                    # Если не удалось распарсить дату, включаем заказ
                    closed_orders.append(order)
            else:
                closed_orders.append(order)

        # Рассчитываем общую выручку
        total_revenue = sum(int(order.get('payed_sum', 0)) for order in closed_orders)

        # Количество чеков
        num_checks = len(closed_orders)

        # Средний чек (AOV)
        average_check = total_revenue / num_checks if num_checks > 0 else 0

        return {
            'revenue': total_revenue,
            'num_checks': num_checks,
            'average_check': average_check,
            'revenue_by_day': dict(sorted(revenue_by_day.items()))  # Сортируем по дате
        }

    def _analyze_transactions(self, transactions: List[Dict], period_start: datetime, period_end: datetime) -> Dict:
        """Анализировать транзакции и собрать статистику"""

        # Фильтруем удалённые транзакции
        transactions = [tx for tx in transactions if tx.get('delete') == '0']

        # Фильтруем по времени (10:00 - 22:00 каждого дня)
        filtered_transactions = []
        for tx in transactions:
            tx_date_str = tx.get('date', '')
            if tx_date_str:
                try:
                    tx_datetime = datetime.strptime(tx_date_str, '%Y-%m-%d %H:%M:%S')
                    # Проверяем, что транзакция в нужном временном диапазоне
                    if 10 <= tx_datetime.hour < 22 or (tx_datetime.hour == 22 and tx_datetime.minute == 0):
                        filtered_transactions.append(tx)
                except ValueError:
                    # Если не удалось распарсить дату, включаем транзакцию
                    filtered_transactions.append(tx)
            else:
                filtered_transactions.append(tx)

        transactions = filtered_transactions

        # Статистика по категориям (исключаем переводы)
        expenses_by_category = defaultdict(int)

        # Общие суммы
        total_expenses = 0
        total_incomes = 0
        total_supplies = 0  # Себестоимость (поставки)

        # Топ-10 расходов для месячного отчёта (исключаем поставки)
        top_expenses = []

        for tx in transactions:
            tx_type = int(tx.get('type', 0))
            amount = abs(int(tx.get('amount', 0)))  # Суммы в тийинах
            category_name = tx.get('category_name', 'Без категории')

            # ДОХОД: только кассовые смены
            if category_name == 'Кассовые смены' and tx_type == 1:
                total_incomes += amount

            # РАСХОД: все транзакции с type=0, исключая ТОЛЬКО переводы
            elif tx_type == 0 and category_name != 'Переводы':
                total_expenses += amount

                # Отдельно считаем поставки (себестоимость)
                is_supply = tx.get('supplier_name') or category_name == 'Поставки'
                if is_supply:
                    total_supplies += amount

                # Добавляем в категории, но исключаем поставки из категорий
                if category_name != 'Поставки':
                    expenses_by_category[category_name] += amount

                # Добавляем в топ расходов, но исключаем поставки
                if not is_supply:
                    top_expenses.append({
                        'amount': amount,
                        'category': category_name,
                        'comment': tx.get('comment', ''),
                        'date': tx.get('date', '')
                    })

            # Переводы (type=2) полностью игнорируем - не учитываем нигде

        # Сортировать категории по сумме
        expenses_by_category = dict(sorted(
            expenses_by_category.items(),
            key=lambda x: x[1],
            reverse=True
        ))

        # Топ-10 расходов для месячного отчёта
        top_expenses = sorted(top_expenses, key=lambda x: x['amount'], reverse=True)[:10]

        return {
            'total_expenses': total_expenses,
            'total_incomes': total_incomes,
            'total_supplies': total_supplies,
            'expenses_by_category': expenses_by_category,
            'top_expenses': top_expenses,
            'transactions_count': len(transactions)
        }

    def _format_report_text(self, data: Dict, prev_data: Dict, month_start: datetime, month_end: datetime) -> str:
        """Форматировать текст отчёта для Telegram"""

        # Конвертация из тийинов в тенге
        def format_amount(amount_tiyin: int) -> str:
            amount_tenge = amount_tiyin / 100
            return f"{amount_tenge:,.0f}₸".replace(',', ' ')

        # Функция для расчета изменения в процентах
        def calc_change(current: int, previous: int) -> str:
            if previous == 0:
                if current > 0:
                    return "📈 +∞%"
                else:
                    return "—"
            change_pct = ((current - previous) / previous) * 100
            if change_pct > 0:
                return f"📈 +{change_pct:.1f}%"
            elif change_pct < 0:
                return f"📉 {change_pct:.1f}%"
            else:
                return "➡️ 0%"

        report_lines = []

        # Заголовок
        report_lines.append("📊 **ЕЖЕМЕСЯЧНЫЙ ОТЧЁТ**")
        month_name = month_start.strftime('%B %Y')  # Название месяца
        report_lines.append(f"📅 {month_start.strftime('%d.%m')} 10:00 - {month_end.strftime('%d.%m.%Y')} 22:00")
        report_lines.append("")

        # === БЛОК 1: ВЫРУЧКА И ПРОДАЖИ ===
        report_lines.append("💰 **ВЫРУЧКА И ПРОДАЖИ:**")

        # Выручка
        revenue = data.get('revenue', 0)
        prev_revenue = prev_data.get('revenue', 0)
        revenue_change = calc_change(revenue, prev_revenue)
        report_lines.append(f"💵 Выручка: **{format_amount(revenue)}** ({revenue_change})")

        # Количество чеков
        num_checks = data.get('num_checks', 0)
        prev_num_checks = prev_data.get('num_checks', 0)
        checks_change = calc_change(num_checks, prev_num_checks)
        report_lines.append(f"🧾 Количество чеков: **{num_checks}** ({checks_change})")

        # Средний чек (AOV)
        avg_check = data.get('average_check', 0)
        prev_avg_check = prev_data.get('average_check', 0)
        avg_check_change = calc_change(avg_check, prev_avg_check)
        report_lines.append(f"📊 Средний чек: **{format_amount(int(avg_check))}** ({avg_check_change})")
        report_lines.append("")

        # === БЛОК 2: РЕНТАБЕЛЬНОСТЬ ===
        report_lines.append("📈 **РЕНТАБЕЛЬНОСТЬ:**")

        # Food Cost %
        total_supplies = data.get('total_supplies', 0)
        if revenue > 0:
            food_cost_pct = (total_supplies / revenue) * 100
            food_cost_emoji = "✅" if food_cost_pct <= 32 else ("⚠️" if food_cost_pct <= 35 else "🚨")
            report_lines.append(f"{food_cost_emoji} Food Cost: **{food_cost_pct:.1f}%** (норма 28-32%)")
        else:
            report_lines.append("⚠️ Food Cost: N/A (нет данных о выручке)")

        # Валовая маржа %
        if revenue > 0:
            gross_margin_pct = ((revenue - total_supplies) / revenue) * 100
            margin_emoji = "✅" if gross_margin_pct >= 60 else "⚠️"
            report_lines.append(f"{margin_emoji} Валовая маржа: **{gross_margin_pct:.1f}%** (норма 60-70%)")
        else:
            report_lines.append("⚠️ Валовая маржа: N/A (нет данных о выручке)")
        report_lines.append("")

        # === БЛОК 3: РАСХОДЫ И БАЛАНС ===
        report_lines.append("💸 **РАСХОДЫ И БАЛАНС:**")

        # Расходы
        expenses_change = calc_change(data['total_expenses'], prev_data['total_expenses'])
        report_lines.append(f"📉 Расходы: **{format_amount(data['total_expenses'])}** ({expenses_change})")

        # Доходы (кассовые смены)
        incomes_change = calc_change(data['total_incomes'], prev_data['total_incomes'])
        report_lines.append(f"📈 Доходы: **{format_amount(data['total_incomes'])}** ({incomes_change})")

        # Баланс
        balance = data['total_incomes'] - data['total_expenses']
        prev_balance = prev_data['total_incomes'] - prev_data['total_expenses']
        balance_change = calc_change(balance, prev_balance)
        balance_emoji = "✅" if balance >= 0 else "⚠️"
        report_lines.append(f"{balance_emoji} Баланс: **{format_amount(abs(balance))}** ({balance_change})")

        report_lines.append(f"📝 Транзакций: {data['transactions_count']}")
        report_lines.append("")

        # Средние показатели в день
        days_in_month = (month_end - month_start).days + 1
        avg_daily_revenue = revenue / days_in_month
        avg_daily_expenses = data['total_expenses'] / days_in_month
        avg_daily_incomes = data['total_incomes'] / days_in_month
        report_lines.append("📊 **Средние показатели в день:**")
        report_lines.append(f"  • Выручка: {format_amount(int(avg_daily_revenue))}")
        report_lines.append(f"  • Расходы: {format_amount(int(avg_daily_expenses))}")
        report_lines.append(f"  • Доходы: {format_amount(int(avg_daily_incomes))}")
        report_lines.append("")

        # Расходы по категориям
        if data['expenses_by_category']:
            report_lines.append("📂 **Расходы по категориям:**")
            for category, amount in list(data['expenses_by_category'].items())[:15]:
                percentage = (amount / data['total_expenses'] * 100) if data['total_expenses'] > 0 else 0
                report_lines.append(f"  • {category}: {format_amount(amount)} ({percentage:.1f}%)")
            report_lines.append("")

        # Топ-10 крупных расходов (без поставок)
        if data['top_expenses']:
            report_lines.append("🔝 **Топ-10 крупных расходов:**")
            for i, expense in enumerate(data['top_expenses'], 1):
                comment = expense['comment'][:30] if expense['comment'] else "без комментария"
                report_lines.append(
                    f"  {i}. {format_amount(expense['amount'])} - {expense['category']}\n"
                    f"     💬 {comment}"
                )
            report_lines.append("")

        # Итоговое сообщение
        if balance >= 0:
            report_lines.append("✅ Месяц прибыльный! 🎉")
        else:
            report_lines.append("⚠️ Расходы превысили доходы")

        return "\n".join(report_lines)


async def send_monthly_report_to_user(telegram_user_id: int, bot_application):
    """
    Отправить ежемесячный отчёт пользователю

    Args:
        telegram_user_id: ID пользователя Telegram
        bot_application: Объект приложения Telegram бота
    """
    try:
        logger.info(f"Генерация ежемесячного отчёта для пользователя {telegram_user_id}")

        generator = MonthlyReportGenerator(telegram_user_id)
        result = await generator.generate_monthly_report()

        if result['success']:
            # Отправить отчёт пользователю
            await bot_application.bot.send_message(
                chat_id=telegram_user_id,
                text=result['report_text'],
                parse_mode='Markdown'
            )
            logger.info(f"✅ Месячный отчёт отправлен пользователю {telegram_user_id}")
        else:
            # Отправить сообщение об ошибке
            await bot_application.bot.send_message(
                chat_id=telegram_user_id,
                text=f"❌ Ошибка генерации месячного отчёта:\n{result.get('error', 'Неизвестная ошибка')}"
            )
            logger.error(f"❌ Ошибка месячного отчёта для пользователя {telegram_user_id}: {result.get('error')}")

    except Exception as e:
        logger.error(f"❌ Ошибка отправки месячного отчёта пользователю {telegram_user_id}: {e}", exc_info=True)
