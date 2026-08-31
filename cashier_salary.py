"""Модуль для расчёта зарплаты кассиров на основе продаж"""
import logging
from datetime import datetime
from typing import Dict, List
from poster_client import PosterClient, find_existing_finance_transaction

logger = logging.getLogger(__name__)


# Нормы зарплаты для 2 кассиров (сумма в тийинах → зарплата каждого)
CASHIER_SALARY_NORMS_2 = {
    # 0-599,999₸ → 6,000₸ каждому
    59999900: 6000,
    # 600,000-699,999₸ → 7,000₸ каждому
    69999900: 7000,
    # 700,000-799,999₸ → 8,000₸ каждому
    79999900: 8000,
    # 800,000-899,999₸ → 9,000₸ каждому
    89999900: 9000,
    # 900,000-999,999₸ → 10,000₸ каждому
    99999900: 10000,
    # 1,000,000-1,099,999₸ → 11,000₸ каждому
    109999900: 11000,
    # 1,100,000-1,199,999₸ → 12,000₸ каждому
    119999900: 12000,
    # 1,200,000-1,299,999₸ → 13,000₸ каждому
    129999900: 13000,
    # 1,300,000-1,399,999₸ → 14,000₸ каждому
    139999900: 14000,
    # 1,400,000-1,499,999₸ → 15,000₸ каждому
    149999900: 15000,
    # 1,500,000-1,599,999₸ → 16,000₸ каждому
    159999900: 16000,
    # 1,600,000-1,699,999₸ → 17,000₸ каждому
    169999900: 17000,
    # 1,700,000-1,799,999₸ → 18,000₸ каждому
    179999900: 18000,
    # 1,800,000-1,899,999₸ → 19,000₸ каждому
    189999900: 19000,
    # 1,900,000-1,999,999₸ → 20,000₸ каждому
    199999900: 20000,
}

# Нормы зарплаты для 3 кассиров (сумма в тийинах → зарплата каждого)
CASHIER_SALARY_NORMS_3 = {
    # До 799,999₸ → 6,000₸ каждому
    79999900: 6000,
    # 800,000-899,999₸ → 7,000₸ каждому
    89999900: 7000,
    # 900,000-999,999₸ → 8,000₸ каждому
    99999900: 8000,
    # 1,000,000-1,099,999₸ → 9,000₸ каждому
    109999900: 9000,
    # 1,100,000-1,199,999₸ → 10,000₸ каждому
    119999900: 10000,
    # 1,200,000-1,299,999₸ → 11,000₸ каждому
    129999900: 11000,
    # 1,300,000-1,399,999₸ → 12,000₸ каждому
    139999900: 12000,
    # 1,400,000-1,499,999₸ → 13,000₸ каждому
    149999900: 13000,
    # 1,500,000-1,599,999₸ → 14,000₸ каждому
    159999900: 14000,
    # 1,600,000-1,699,999₸ → 15,000₸ каждому
    169999900: 15000,
    # 1,700,000-1,799,999₸ → 16,000₸ каждому
    179999900: 16000,
    # 1,800,000-1,899,999₸ → 17,000₸ каждому
    189999900: 17000,
    # 1,900,000-1,999,999₸ → 18,000₸ каждому
    199999900: 18000,
    # 2,000,000-2,099,999₸ → 19,000₸ каждому
    209999900: 19000,
    # 2,100,000-2,199,999₸ → 20,000₸ каждому
    219999900: 20000,
}


class CashierSalaryCalculator:
    """Калькулятор зарплаты кассиров"""

    def __init__(self, telegram_user_id: int):
        self.telegram_user_id = telegram_user_id

    async def get_total_sales(self, date: str = None) -> Dict:
        """
        Получить общую сумму продаж за день

        Считаем: Kaspi + Halyk + Наличка + Wolt - Бонусы

        Args:
            date: Дата в формате "YYYYMMDD". Если None, используется сегодня

        Returns:
            Dict с данными:
            - total_sales: общая сумма продаж в тийинах
            - kaspi: сумма через Kaspi
            - halyk: сумма через Halyk
            - cash: наличные
            - wolt: сумма через Wolt
            - bonus: бонусы (вычитаются)
            - details: детальная разбивка
        """
        try:
            if date is None:
                date = datetime.now().strftime("%Y%m%d")

            poster_client = PosterClient(self.telegram_user_id)

            # Получить транзакции (заказы) за день
            result = await poster_client._request('GET', 'dash.getTransactions', params={
                'dateFrom': date,
                'dateTo': date
            })

            transactions = result.get('response', [])
            await poster_client.close()

            # Подсчёт сумм по типам оплаты
            # Поля в транзакции:
            # - payed_cash: наличные (в тийинах)
            # - payed_card: картой (в тийинах)
            # - payed_bonus: бонусы (в тийинах)
            # - payed_sum: общая сумма (в тийинах)

            total_cash = 0
            total_card = 0
            total_bonus = 0
            total_sum = 0

            # Фильтруем только закрытые заказы (status=2)
            closed_transactions = [tx for tx in transactions if tx.get('status') == '2']

            for tx in closed_transactions:
                cash = int(tx.get('payed_cash', 0))
                card = int(tx.get('payed_card', 0))
                bonus = int(tx.get('payed_bonus', 0))
                total = int(tx.get('payed_sum', 0))

                total_cash += cash
                total_card += card
                total_bonus += bonus
                total_sum += total

            # ВАЖНО: Общая сумма продаж = наличные + карта
            # payed_sum включает бонусы лояльности (онлайн-оплата), которые не нужно учитывать
            # Формула: total_sales = payed_cash + payed_card
            # Это исключает бонусы, которые равны: payed_sum - (payed_cash + payed_card)

            # Общая сумма продаж (без бонусов лояльности)
            total_sales = total_cash + total_card

            # Рассчитываем бонусы как разницу
            actual_bonus = total_sum - total_sales

            logger.info(
                f"📊 Продажи за {date}: "
                f"всего={total_sales/100:,.0f}₸, наличные={total_cash/100:,.0f}₸, "
                f"карта={total_card/100:,.0f}₸, бонусы={actual_bonus/100:,.0f}₸"
            )

            return {
                'total_sales': total_sales,
                'cash': total_cash,
                'card': total_card,
                'bonus': actual_bonus,
                'total_sum': total_sum,
                'transactions_count': len(closed_transactions),
                'date': date
            }

        except Exception as e:
            logger.error(f"❌ Ошибка получения данных продаж: {e}", exc_info=True)
            raise

    def calculate_salary(self, total_sales: int, cashier_count: int) -> int:
        """
        Рассчитать зарплату кассира по нормам

        Args:
            total_sales: Общая сумма продаж в тийинах
            cashier_count: Количество кассиров (2 или 3)

        Returns:
            Зарплата каждого кассира в тенге
        """
        if cashier_count == 2:
            norms = CASHIER_SALARY_NORMS_2
        elif cashier_count == 3:
            norms = CASHIER_SALARY_NORMS_3
        else:
            raise ValueError(f"Неверное количество кассиров: {cashier_count}. Должно быть 2 или 3.")

        # Найти подходящую норму
        for max_sales, salary in sorted(norms.items()):
            if total_sales <= max_sales:
                logger.info(
                    f"💰 Зарплата кассиров ({cashier_count} чел): "
                    f"{total_sales/100:,.0f}₸ продаж → {salary:,}₸ каждому"
                )
                return salary

        # Если больше максимума, берём последнюю норму
        max_salary = norms[max(norms.keys())]
        logger.warning(
            f"⚠️ Сумма продаж ({total_sales/100:,.0f}₸) превышает максимум. "
            f"Используем максимальную зарплату: {max_salary:,}₸"
        )
        return max_salary

    async def create_salary_transactions(
        self,
        cashier_count: int,
        date: str = None,
        cashier_names: List[str] = None
    ) -> Dict:
        """
        Создать транзакции зарплаты кассиров

        Args:
            cashier_count: Количество кассиров (2 или 3)
            date: Дата для расчёта в формате "YYYYMMDD". Если None, используется сегодня
            cashier_names: Список имен кассиров (опционально, для комментариев)

        Returns:
            Dict с результатом:
            - success: bool
            - salaries: List[Dict] - список с именами и зарплатами
            - cashier_count: int
        """
        try:
            # Получить данные о продажах
            sales_data = await self.get_total_sales(date)
            total_sales = sales_data['total_sales']

            # Рассчитать зарплату
            salary_per_cashier = self.calculate_salary(total_sales, cashier_count)

            # Создать транзакции
            poster_client = PosterClient(self.telegram_user_id)

            # Дата и время для транзакции
            if date:
                # Если указана дата, используем 21:30 этого дня
                transaction_date = datetime.strptime(date, "%Y%m%d").replace(hour=21, minute=30, second=0)
                transaction_date_str = transaction_date.strftime("%Y-%m-%d %H:%M:%S")
            else:
                # Если дата не указана, используем текущее время
                transaction_date = datetime.now()
                transaction_date_str = transaction_date.strftime("%Y-%m-%d %H:%M:%S")

            existing_transactions = await poster_client.get_transactions(
                transaction_date.strftime("%Y%m%d"),
                transaction_date.strftime("%Y%m%d"),
            )

            # Создать транзакции для каждого кассира
            salaries = []
            for i in range(cashier_count):
                # Получить имя кассира (если не хватает имен, использовать "Кассир {i+1}")
                cashier_name = cashier_names[i] if (cashier_names and i < len(cashier_names)) else f"Кассир {i+1}"

                # Счёт: "Оставил в кассе" (ID=4)
                # Категория: "Кассиры" (ID=16)
                existing = find_existing_finance_transaction(
                    existing_transactions,
                    transaction_type=0,
                    category_id=16,
                    account_from_id=4,
                    amount=salary_per_cashier,
                    comment=cashier_name,
                )
                if existing:
                    transaction_id = existing.get('transaction_id')
                    logger.info(
                        "⏭️ Зарплата кассира %s уже существует в Poster (ID=%s), пропускаю",
                        cashier_name,
                        transaction_id,
                    )
                else:
                    transaction_id = await poster_client.create_transaction(
                        transaction_type=0,  # expense
                        category_id=16,  # Кассиры
                        account_from_id=4,  # Оставил в кассе
                        amount=salary_per_cashier,
                        date=transaction_date_str,
                        comment=cashier_name  # ИМЯ В КОММЕНТАРИИ
                    )

                salaries.append({
                    'name': cashier_name,
                    'salary': salary_per_cashier,
                    'transaction_id': transaction_id
                })

                logger.info(
                    f"✅ Транзакция зарплаты кассира {cashier_name} создана: "
                    f"ID={transaction_id}, сумма={salary_per_cashier}₸"
                )

            await poster_client.close()

            logger.info(
                f"✅ Все транзакции зарплаты кассиров созданы: "
                f"количество={cashier_count}, зарплата={salary_per_cashier}₸ каждому"
            )

            # Вернуть МИНИМАЛЬНЫЕ данные (БЕЗ total_sales и sales_data)
            return {
                'success': True,
                'salaries': salaries,
                'cashier_count': cashier_count
            }

        except Exception as e:
            logger.error(f"❌ Ошибка создания транзакций зарплаты кассиров: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e)
            }


async def calculate_and_create_cashier_salary(
    telegram_user_id: int,
    cashier_count: int,
    date: str = None,
    cashier_names: List[str] = None
) -> Dict:
    """
    Рассчитать и создать транзакции зарплаты кассиров

    Args:
        telegram_user_id: ID пользователя Telegram
        cashier_count: Количество кассиров (2 или 3)
        date: Дата для расчёта в формате "YYYYMMDD". Если None, используется сегодня
        cashier_names: Список имен кассиров (опционально, для комментариев)

    Returns:
        Dict с результатом операции
    """
    calculator = CashierSalaryCalculator(telegram_user_id)
    return await calculator.create_salary_transactions(cashier_count, date, cashier_names)
