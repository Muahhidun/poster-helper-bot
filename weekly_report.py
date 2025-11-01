"""Модуль для генерации еженедельных отчётов"""
import logging
from datetime import datetime, timedelta
from typing import Dict, List
from collections import defaultdict
from poster_client import PosterClient

logger = logging.getLogger(__name__)


class WeeklyReportGenerator:
    """Генератор еженедельных отчётов"""

    def __init__(self, telegram_user_id: int):
        self.telegram_user_id = telegram_user_id

    async def generate_weekly_report(self) -> Dict:
        """
        Генерировать отчёт за прошлую неделю (пн 10:00 - вс 22:00)

        Returns:
            Dict с данными отчёта и текстом для отправки
        """
        try:
            # Рассчитать даты прошлой недели (пн-вс)
            today = datetime.now()
            # Найти прошлый понедельник
            days_since_monday = today.weekday()  # 0=пн, 6=вс
            if days_since_monday == 0:
                # Сегодня понедельник, берём неделю с прошлого понедельника
                week_start = today - timedelta(days=7)
            else:
                # Берём прошлый понедельник
                week_start = today - timedelta(days=days_since_monday + 7)

            week_end = week_start + timedelta(days=6)  # Воскресенье

            # Установить время: понедельник 10:00 - воскресенье 22:00
            week_start = week_start.replace(hour=10, minute=0, second=0, microsecond=0)
            week_end = week_end.replace(hour=22, minute=0, second=0, microsecond=0)

            logger.info(f"Генерация отчёта за {week_start} - {week_end}")

            # Получить транзакции
            poster_client = PosterClient(self.telegram_user_id)

            transactions = await poster_client.get_transactions(
                date_from=week_start.strftime("%Y%m%d"),
                date_to=week_end.strftime("%Y%m%d")
            )

            # Также получить данные за предыдущую неделю для сравнения
            prev_week_start = week_start - timedelta(days=7)
            prev_week_end = week_end - timedelta(days=7)

            prev_transactions = await poster_client.get_transactions(
                date_from=prev_week_start.strftime("%Y%m%d"),
                date_to=prev_week_end.strftime("%Y%m%d")
            )

            await poster_client.close()

            # Анализ транзакций
            report_data = self._analyze_transactions(transactions, week_start, week_end)
            prev_report_data = self._analyze_transactions(prev_transactions, prev_week_start, prev_week_end)

            # Генерация текста отчёта
            report_text = self._format_report_text(report_data, prev_report_data, week_start, week_end)

            return {
                'success': True,
                'report_text': report_text,
                'data': report_data,
                'prev_data': prev_report_data,
                'period': {
                    'start': week_start,
                    'end': week_end
                }
            }

        except Exception as e:
            logger.error(f"Ошибка генерации отчёта: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e)
            }

    def _analyze_transactions(self, transactions: List[Dict], week_start: datetime, week_end: datetime) -> Dict:
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

        # Топ-5 расходов (исключаем поставки)
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

                # Добавляем в категории, но исключаем поставки из категорий
                if category_name != 'Поставки':
                    expenses_by_category[category_name] += amount

                # Добавляем в топ расходов, но исключаем поставки
                # Поставки имеют supplier_name или категорию "Поставки"
                is_supply = tx.get('supplier_name') or category_name == 'Поставки'
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

        # Топ-5 расходов
        top_expenses = sorted(top_expenses, key=lambda x: x['amount'], reverse=True)[:5]

        return {
            'total_expenses': total_expenses,
            'total_incomes': total_incomes,
            'expenses_by_category': expenses_by_category,
            'top_expenses': top_expenses,
            'transactions_count': len(transactions)
        }

    def _format_report_text(self, data: Dict, prev_data: Dict, week_start: datetime, week_end: datetime) -> str:
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
        report_lines.append("📊 **ЕЖЕНЕДЕЛЬНЫЙ ОТЧЁТ**")
        report_lines.append(f"📅 {week_start.strftime('%d.%m')} 10:00 - {week_end.strftime('%d.%m.%Y')} 22:00")
        report_lines.append("")

        # Общая статистика с сравнением
        report_lines.append("💰 **Итого:**")

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

        # Расходы по категориям
        if data['expenses_by_category']:
            report_lines.append("📂 **Расходы по категориям:**")
            for category, amount in list(data['expenses_by_category'].items())[:10]:
                percentage = (amount / data['total_expenses'] * 100) if data['total_expenses'] > 0 else 0
                report_lines.append(f"  • {category}: {format_amount(amount)} ({percentage:.1f}%)")
            report_lines.append("")

        # Топ-5 крупных расходов (без поставок)
        if data['top_expenses']:
            report_lines.append("🔝 **Топ-5 крупных расходов:**")
            for i, expense in enumerate(data['top_expenses'], 1):
                comment = expense['comment'][:30] if expense['comment'] else "без комментария"
                report_lines.append(
                    f"  {i}. {format_amount(expense['amount'])} - {expense['category']}\n"
                    f"     💬 {comment}"
                )
            report_lines.append("")

        # Итоговое сообщение
        if balance >= 0:
            report_lines.append("✅ Неделя прибыльная! 🎉")
        else:
            report_lines.append("⚠️ Расходы превысили доходы")

        return "\n".join(report_lines)


async def send_weekly_report_to_user(telegram_user_id: int, bot_application):
    """
    Отправить еженедельный отчёт пользователю

    Args:
        telegram_user_id: ID пользователя Telegram
        bot_application: Объект приложения Telegram бота
    """
    try:
        logger.info(f"Генерация еженедельного отчёта для пользователя {telegram_user_id}")

        generator = WeeklyReportGenerator(telegram_user_id)
        result = await generator.generate_weekly_report()

        if result['success']:
            # Отправить отчёт пользователю
            await bot_application.bot.send_message(
                chat_id=telegram_user_id,
                text=result['report_text'],
                parse_mode='Markdown'
            )
            logger.info(f"✅ Отчёт отправлен пользователю {telegram_user_id}")
        else:
            # Отправить сообщение об ошибке
            await bot_application.bot.send_message(
                chat_id=telegram_user_id,
                text=f"❌ Ошибка генерации отчёта:\n{result.get('error', 'Неизвестная ошибка')}"
            )
            logger.error(f"❌ Ошибка отчёта для пользователя {telegram_user_id}: {result.get('error')}")

    except Exception as e:
        logger.error(f"❌ Ошибка отправки отчёта пользователю {telegram_user_id}: {e}", exc_info=True)
