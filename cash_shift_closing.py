"""Модуль для закрытия кассовой смены с автоматическими расчётами"""
import logging
from datetime import datetime
from typing import Dict, Optional
from poster_client import PosterClient

logger = logging.getLogger(__name__)


# Счета в системе
ACCOUNT_IDS = {
    'cash_left': 4,           # "Оставил в кассе" - основной кассовый счёт
    'kaspi': 1,               # "Каспи Пей" - терминал Kaspi
    'halyk': 2,               # "Halyk Bank" - терминал Halyk
    'cash_drawer': 5,         # "Денежный ящик кассира" - сдача
}

# Категории
CATEGORY_IDS = {
    'cash_shifts': 16,        # "Кассовые смены"
}


class CashShiftClosing:
    """Класс для закрытия кассовой смены"""

    def __init__(self, telegram_user_id: int):
        self.telegram_user_id = telegram_user_id
        self.poster_client = PosterClient(telegram_user_id)

    async def close(self):
        """Закрыть соединение с Poster"""
        await self.poster_client.close()

    async def get_poster_data(self, date: str = None) -> Dict:
        """
        ШАГ 2: Получить данные из Poster

        Args:
            date: Дата в формате "YYYYMMDD". Если None, используется сегодня

        Returns:
            Dict с данными:
            - trade_total: торговля за день (в тийинах)
            - bonus: бонусы/онлайн-оплата (в тийинах)
            - poster_cashless: безнал в Poster (в тийинах)
            - poster_cash: наличка в Poster (в тийинах)
            - shift_start: остаток на начало смены (TODO: откуда брать?)
        """
        try:
            if date is None:
                date = datetime.now().strftime("%Y%m%d")

            # Получить транзакции за день
            result = await self.poster_client._request('GET', 'dash.getTransactions', params={
                'dateFrom': date,
                'dateTo': date
            })

            transactions = result.get('response', [])

            # Фильтруем только закрытые заказы (status='2')
            closed_transactions = [tx for tx in transactions if tx.get('status') == '2']

            # Подсчёт сумм
            total_cash = 0
            total_card = 0
            total_sum = 0

            for tx in closed_transactions:
                cash = int(tx.get('payed_cash', 0))
                card = int(tx.get('payed_card', 0))
                total = int(tx.get('payed_sum', 0))

                total_cash += cash
                total_card += card
                total_sum += total

            # ВАЖНО: В Poster API уже учтены бонусы!
            # total_sum = общая сумма заказов (включая бонусы)
            # total_cash + total_card = торговля БЕЗ бонусов
            # Значит: trade_total уже = торговле без бонусов
            trade_total = total_cash + total_card

            # Бонусы = разница между общей суммой и (наличные + карта)
            bonus = total_sum - trade_total

            logger.info(
                f"📊 Данные Poster за {date}: "
                f"общая сумма={total_sum/100:,.0f}₸, "
                f"торговля (без бонусов)={trade_total/100:,.0f}₸, "
                f"бонусы={bonus/100:,.0f}₸, "
                f"наличные={total_cash/100:,.0f}₸, "
                f"безнал={total_card/100:,.0f}₸"
            )

            return {
                'success': True,
                'date': date,
                'total_sum': total_sum,           # Общая сумма (с бонусами)
                'trade_total': trade_total,       # Торговля за день (БЕЗ бонусов)
                'bonus': bonus,                   # Бонусы (онлайн-оплата)
                'poster_cashless': total_card,    # Безнал в Poster
                'poster_cash': total_cash,        # Наличка в Poster
                'shift_start': 0,                 # Будет задано пользователем
                'transactions_count': len(closed_transactions)
            }

        except Exception as e:
            logger.error(f"❌ Ошибка получения данных Poster: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e)
            }

    def calculate_totals(
        self,
        poster_data: Dict,
        wolt: int,
        halyk: int,
        kaspi: int,
        cash_bills: int,
        cash_coins: int,
        deposits: int = 0,
        expenses: int = 0
    ) -> Dict:
        """
        ШАГ 3: Расчёт итогов

        Args:
            poster_data: данные из Poster (из get_poster_data)
            wolt: сумма Wolt (в тенге)
            halyk: сумма Halyk (в тенге)
            kaspi: сумма Kaspi (в тенге)
            cash_bills: наличка бумажная (в тенге)
            cash_coins: наличка монеты (в тенге)
            deposits: внесения (в тенге)
            expenses: расходы с кассы (в тенге)

        Returns:
            Dict с расчётами (все суммы в тенге):
            - fact_cashless: итого безнал факт (Wolt + Halyk + Kaspi)
            - fact_total: фактические (безнал + бумажная + монеты)
            - fact_adjusted: итого фактически (факт - смена - внесения + расходы)
            - poster_total: итого Poster (торговля - бонусы)
            - day_diff: ИТОГО ДЕНЬ (фактически - Poster)
            - cashless_diff: разница безнал (факт безнал - Poster безнал)
        """
        # Переводим в тенге для расчётов
        total_sum = poster_data.get('total_sum', 0) / 100
        trade_total = poster_data['trade_total'] / 100
        bonus = poster_data['bonus'] / 100
        poster_cashless = poster_data['poster_cashless'] / 100
        poster_cash = poster_data['poster_cash'] / 100
        shift_start = poster_data['shift_start'] / 100

        # Расчёты (в тенге)
        fact_cashless = wolt + halyk + kaspi
        fact_total = fact_cashless + cash_bills + cash_coins
        fact_adjusted = fact_total - shift_start - deposits + expenses

        # ИСПРАВЛЕНО: trade_total уже БЕЗ бонусов! Не вычитаем второй раз
        poster_total = trade_total  # Уже = наличные + безнал (без бонусов)

        day_diff = fact_adjusted - poster_total
        cashless_diff = fact_cashless - poster_cashless

        logger.info(
            f"💰 Расчёты: факт безнал={fact_cashless:,.0f}₸, "
            f"факт всего={fact_total:,.0f}₸, "
            f"Poster всего={poster_total:,.0f}₸, "
            f"разница дня={day_diff:+,.0f}₸, "
            f"разница безнал={cashless_diff:+,.0f}₸"
        )

        return {
            'fact_cashless': fact_cashless,
            'fact_total': fact_total,
            'fact_adjusted': fact_adjusted,
            'poster_total': poster_total,
            'day_diff': day_diff,
            'cashless_diff': cashless_diff,
            'poster_cashless': poster_cashless,
            'poster_cash': poster_cash,
            'trade_total': trade_total,
            'bonus': bonus,
            'wolt': wolt,
            'halyk': halyk,
            'kaspi': kaspi,
            'cash_bills': cash_bills,
            'cash_coins': cash_coins,
            'deposits': deposits,
            'expenses': expenses,
            'shift_start': shift_start
        }

    async def create_transactions(
        self,
        calculations: Dict,
        cash_to_leave: int,
        date: str = None
    ) -> Dict:
        """
        ШАГ 4: Создание транзакций

        Args:
            calculations: результаты calculate_totals
            cash_to_leave: сколько наличных бумажных денег оставить (в тенге)
            date: дата для транзакций в формате "YYYYMMDD"

        Returns:
            Dict с результатом:
            - success: bool
            - transactions: List[int] - ID созданных транзакций
            - surplus_shortage_id: ID транзакции излишка/недостачи
            - correction_id: ID корректирующей транзакции (если была)
            - closing_id: ID транзакции "Закрытие смены"
        """
        try:
            if date is None:
                date = datetime.now().strftime("%Y%m%d")

            # Парсим дату для транзакций
            transaction_date = datetime.strptime(date, "%Y%m%d").replace(
                hour=21, minute=30, second=0
            ).strftime("%Y-%m-%d %H:%M:%S")

            day_diff = calculations['day_diff']
            cashless_diff = calculations['cashless_diff']
            transaction_ids = []

            # 1. Излишек/недостача - ТОЛЬКО показываем, транзакцию не создаем
            # Пользователь сам создаст её с планшета Poster
            surplus_shortage_id = None
            if abs(day_diff) >= 1:
                logger.info(f"📊 Излишек/недостача: {day_diff:+,.0f}₸ (транзакцию создайте вручную с планшета)")
            else:
                logger.info("📊 Излишек/недостача: 0₸")

            # 2. Корректировка безнала (если есть расхождение)
            correction_id = None
            if abs(cashless_diff) >= 1:  # Только если разница >= 1₸
                if cashless_diff > 0:
                    # Излишек безнала → перевод с "Оставил в кассе" на "Каспи Пей"
                    correction_id = await self.poster_client.create_transaction(
                        transaction_type=2,  # transfer
                        category_id=CATEGORY_IDS['cash_shifts'],
                        account_from_id=ACCOUNT_IDS['cash_left'],
                        account_to_id=ACCOUNT_IDS['kaspi'],
                        amount=abs(int(cashless_diff)),
                        date=transaction_date,
                        comment=f"Корректировка безнал {datetime.strptime(date, '%Y%m%d').strftime('%d.%m.%Y')}"
                    )
                else:
                    # Недостача безнала → перевод с "Каспи Пей" на "Оставил в кассе"
                    correction_id = await self.poster_client.create_transaction(
                        transaction_type=2,  # transfer
                        category_id=CATEGORY_IDS['cash_shifts'],
                        account_from_id=ACCOUNT_IDS['kaspi'],
                        account_to_id=ACCOUNT_IDS['cash_left'],
                        amount=abs(int(cashless_diff)),
                        date=transaction_date,
                        comment=f"Корректировка безнал {datetime.strptime(date, '%Y%m%d').strftime('%d.%m.%Y')}"
                    )
                transaction_ids.append(correction_id)
                logger.info(f"✅ Корректировка безнал: {cashless_diff:+,.0f}₸, ID={correction_id}")
            else:
                logger.info("✅ Корректировка безнал: не требуется")

            # 3. Расчёт остатка на завтра и суммы к инкассации
            # cash_to_leave (параметр) = бумажные деньги, которые ввёл пользователь
            # Но на завтра остаются: бумажные + монеты
            total_cash_remaining = cash_to_leave + calculations['cash_coins']

            # К инкассации = вся наличка - то что оставили на завтра
            total_cash = calculations['cash_bills'] + calculations['cash_coins']
            cash_for_collection = total_cash - total_cash_remaining

            closing_id = None  # Транзакцию закрытия смены не создаём
            logger.info(f"📝 На завтра остаётся: {total_cash_remaining:,.0f}₸ (бумажные {cash_to_leave:,.0f}₸ + монеты {calculations['cash_coins']:,.0f}₸)")
            logger.info(f"💰 К инкассации: {cash_for_collection:,.0f}₸")

            return {
                'success': True,
                'transactions': transaction_ids,
                'surplus_shortage_id': surplus_shortage_id,
                'correction_id': correction_id,
                'closing_id': closing_id,
                'cash_to_leave': cash_to_leave,
                'total_cash_remaining': total_cash_remaining,
                'cash_for_collection': cash_for_collection
            }

        except Exception as e:
            logger.error(f"❌ Ошибка создания транзакций: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e)
            }

    def format_report(
        self,
        poster_data: Dict,
        calculations: Dict,
        transactions: Dict
    ) -> str:
        """
        ШАГ 5: Форматирование итогового отчёта

        Args:
            poster_data: данные из get_poster_data
            calculations: результаты calculate_totals
            transactions: результаты create_transactions

        Returns:
            Форматированный отчёт (str)
        """
        date_str = datetime.strptime(poster_data['date'], '%Y%m%d').strftime('%d.%m.%Y')
        day_diff = calculations['day_diff']
        diff_emoji = "✅" if abs(day_diff) < 1 else ("📈" if day_diff > 0 else "📉")

        report = f"""
✅ **СМЕНА ЗАКРЫТА** ({date_str})

📊 **Итоги Poster:**
• Торговля за день (наличные + безнал): {calculations['trade_total']:,.0f}₸
• Бонусы: {calculations['bonus']:,.0f}₸
• **Итого Poster (без бонусов):** {calculations['poster_total']:,.0f}₸

💰 **Фактически:**
• Остаток на начало смены: {calculations['shift_start']:,.0f}₸
• Wolt: {calculations['wolt']:,.0f}₸
• Halyk: {calculations['halyk']:,.0f}₸
• Kaspi: {calculations['kaspi']:,.0f}₸
• Наличные (бумажные): {calculations['cash_bills']:,.0f}₸
• Наличные (монеты): {calculations['cash_coins']:,.0f}₸
• Внесения: {calculations['deposits']:,.0f}₸
• Расходы: {calculations['expenses']:,.0f}₸
• **Итого фактически (с вычетом остатка на начало):** {calculations['fact_adjusted']:,.0f}₸

{diff_emoji} **ИТОГО ДЕНЬ:** {day_diff:+,.0f}₸ {"(Излишек)" if day_diff > 0 else "(Недостача)" if day_diff < 0 else "(Идеально!)"}
"""

        # Излишек/недостача - напоминание создать вручную
        if abs(day_diff) >= 1:
            report += f"\n⚠️ **{'Излишек' if day_diff > 0 else 'Недостача'}: {abs(day_diff):,.0f}₸**\n"
            report += f"_Создайте эту транзакцию вручную с планшета Poster (счёт: Денежный ящик кассира)_\n"

        # Транзакции, созданные ботом
        if transactions['correction_id']:
            cashless_diff = calculations['cashless_diff']
            report += f"\n💵 **Транзакция создана:**\n"
            report += f"• Корректировка безнал ({cashless_diff:+,.0f}₸): ID {transactions['correction_id']}\n"

        report += f"""
📝 **На смену оставлено:** {transactions['total_cash_remaining']:,.0f}₸
   • Бумажные: {transactions['cash_to_leave']:,.0f}₸
   • Монеты: {calculations['cash_coins']:,.0f}₸

💰 **К инкассации:** {transactions['cash_for_collection']:,.0f}₸
"""

        return report
