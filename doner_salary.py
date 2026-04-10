"""Модуль для расчёта зарплаты донерщика на основе продаж"""
import logging
from datetime import datetime
from typing import Dict, List
from poster_client import PosterClient

logger = logging.getLogger(__name__)


# Нормы зарплаты донерщика по количеству проданных донеров
DONER_SALARY_NORMS = {
    # До 200 шт → 12,000₸
    199: 12000,
    # 200-219 → 13,000₸
    219: 13000,
    # 220-239 → 14,000₸
    239: 14000,
    # 240-259 → 15,000₸
    259: 15000,
    # 260-279 → 16,000₸
    279: 16000,
    # 280-299 → 17,000₸
    299: 17000,
    # 300-319 → 18,000₸
    319: 18000,
    # 320-339 → 19,000₸
    339: 19000,
    # 340-359 → 20,000₸
    359: 20000,
    # 360-379 → 21,000₸
    379: 21000,
    # 380-399 → 22,000₸
    399: 22000,
}


class DonerSalaryCalculator:
    """Калькулятор зарплаты донерщика"""

    # ID категории "Донер" в Poster
    DONER_CATEGORY_ID = 6

    # Названия специальных товаров для подсчёта
    COMBO_DONER_NAME = "Комбо Донер"
    PIZZA_DONER_NAME = "Донерная пицца"

    def __init__(self, telegram_user_id: int):
        self.telegram_user_id = telegram_user_id

    async def get_doner_sales_count(self, date: str = None) -> Dict:
        """
        Получить количество проданных донеров за день

        Args:
            date: Дата в формате "YYYYMMDD". Если None, используется сегодня

        Returns:
            Dict с данными:
            - category_count: количество из категории "Донер"
            - combo_count: количество "Комбо Донер"
            - pizza_count: количество "Донерная пицца"
            - total_count: общее количество
            - details: список товаров с количеством
        """
        try:
            if date is None:
                date = datetime.now().strftime("%Y%m%d")

            poster_client = PosterClient(self.telegram_user_id)

            # Получить продажи товаров за день
            result = await poster_client._request('GET', 'dash.getProductsSales', params={
                'dateFrom': date,
                'dateTo': date
            })

            products_sales = result.get('response', [])
            await poster_client.close()

            # Подсчёт по категориям и товарам
            category_count = 0.0
            combo_count = 0.0
            pizza_count = 0.0
            details = []

            for product in products_sales:
                product_name = product.get('product_name', '')
                category_id = product.get('category_id', '')
                count = float(product.get('count', 0))
                product_name_lower = product_name.lower()

                # Сначала проверяем специальные товары (ВАЖНО: до проверки категории!)

                # Донерная пицца:
                # 1. Название "Донерная" (точное совпадение или с пробелами)
                # 2. ИЛИ есть "донер" И "пицц" в любом порядке
                if (product_name_lower.strip() == 'донерная' or
                    ('донер' in product_name_lower and 'пицц' in product_name_lower)):
                    pizza_count += count
                    details.append({
                        'name': product_name,
                        'count': count,
                        'source': 'pizza'
                    })

                # Комбо Донер (ищем "комбо" И "донер")
                elif 'комбо' in product_name_lower and 'донер' in product_name_lower:
                    combo_count += count
                    details.append({
                        'name': product_name,
                        'count': count,
                        'source': 'combo'
                    })

                # Категория "Донер" - все остальное из категории 6
                elif category_id == str(self.DONER_CATEGORY_ID):
                    category_count += count
                    details.append({
                        'name': product_name,
                        'count': count,
                        'source': 'category'
                    })

            total_count = category_count + combo_count + pizza_count

            logger.info(
                f"📊 Продажи донеров за {date}: "
                f"категория={category_count}, комбо={combo_count}, "
                f"пицца={pizza_count}, всего={total_count}"
            )

            return {
                'category_count': category_count,
                'combo_count': combo_count,
                'pizza_count': pizza_count,
                'total_count': total_count,
                'details': details,
                'date': date
            }

        except Exception as e:
            logger.error(f"❌ Ошибка получения данных продаж донеров: {e}", exc_info=True)
            raise

    def calculate_salary(self, total_count: int) -> int:
        """
        Рассчитать зарплату донерщика по нормам

        Args:
            total_count: Общее количество проданных донеров

        Returns:
            Зарплата в тенге
        """
        # Найти подходящую норму
        for max_count, salary in sorted(DONER_SALARY_NORMS.items()):
            if total_count <= max_count:
                logger.info(f"💰 Зарплата донерщика: {total_count} шт → {salary}₸")
                return salary

        # Если больше максимума, берём последнюю норму
        max_salary = DONER_SALARY_NORMS[max(DONER_SALARY_NORMS.keys())]
        logger.warning(
            f"⚠️ Количество донеров ({total_count}) превышает максимум ({max(DONER_SALARY_NORMS.keys())}). "
            f"Используем максимальную зарплату: {max_salary}₸"
        )
        return max_salary

    async def create_salary_transaction(
        self,
        date: str = None,
        assistant_start_time: str = "10:00",
        doner_name: str = None,
        assistant_name: str = None
    ) -> Dict:
        """
        Создать транзакцию зарплаты донерщика и помощника

        Args:
            date: Дата для расчёта в формате "YYYYMMDD". Если None, используется сегодня
            assistant_start_time: Время выхода помощника ("10:00", "12:00", "14:00")
            doner_name: Имя донерщика (опционально, для комментария)
            assistant_name: Имя помощника (опционально, для комментария)

        Returns:
            Dict с результатом:
            - success: bool
            - doner_name: str
            - salary: int (зарплата донерщика с бонусом)
            - assistant_name: str
            - assistant_salary: int (зарплата помощника)
            - assistant_start_time: str
        """
        try:
            # Получить данные о продажах
            sales_data = await self.get_doner_sales_count(date)
            total_count = int(sales_data['total_count'])

            # Рассчитать базовую зарплату по таблице норм
            base_salary = self.calculate_salary(total_count)

            # Рассчитать бонус донерщику и зарплату помощника в зависимости от времени выхода
            if assistant_start_time == "10:00":
                bonus = 0
                assistant_salary = 9000
            elif assistant_start_time == "12:00":
                bonus = 750
                assistant_salary = 8000
            elif assistant_start_time == "14:00":
                bonus = 1500
                assistant_salary = 7000
            else:
                # По умолчанию 10:00
                bonus = 0
                assistant_salary = 9000

            # Итоговая зарплата донерщика = базовая + бонус
            salary = base_salary + bonus

            # Создать транзакцию
            poster_client = PosterClient(self.telegram_user_id)

            # Дата и время для транзакции
            if date:
                # Если указана дата, используем 21:30 этого дня
                transaction_date = datetime.strptime(date, "%Y%m%d").replace(hour=21, minute=30, second=0)
                transaction_date_str = transaction_date.strftime("%Y-%m-%d %H:%M:%S")
            else:
                # Если дата не указана, используем текущее время
                transaction_date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # 1. Создать транзакцию зарплаты донерщика
            # Счёт: "Оставил в кассе" (ID=4)
            # Категория: "Донерщик" (ID=19)
            transaction_id = await poster_client.create_transaction(
                transaction_type=0,  # expense
                category_id=19,  # Донерщик
                account_from_id=4,  # Оставил в кассе
                amount=salary,
                date=transaction_date_str,
                comment=doner_name or ""  # ИМЯ В КОММЕНТАРИИ
            )

            logger.info(
                f"✅ Транзакция зарплаты донерщика {doner_name or ''} создана: "
                f"ID={transaction_id}, сумма={salary}₸"
            )

            # 2. Создать транзакцию зарплаты помощника донерщика
            # Счёт: "Оставил в кассе" (ID=4)
            # Категория: "Донерщик" (ID=19)
            # Комментарий: "Помощник: {имя}" или просто "Помощник"
            assistant_comment = f"Помощник: {assistant_name}" if assistant_name else "Помощник"
            assistant_transaction_id = await poster_client.create_transaction(
                transaction_type=0,  # expense
                category_id=19,  # Донерщик
                account_from_id=4,  # Оставил в кассе
                amount=assistant_salary,
                date=transaction_date_str,
                comment=assistant_comment  # ИМЯ В КОММЕНТАРИИ
            )

            logger.info(
                f"✅ Транзакция зарплаты помощника {assistant_name or ''} создана: "
                f"ID={assistant_transaction_id}, сумма={assistant_salary}₸"
            )

            await poster_client.close()

            return {
                'success': True,
                'doner_name': doner_name or "Донерщик",
                'doner_count': total_count,
                'base_salary': base_salary,
                'bonus': bonus,
                'salary': salary,
                'assistant_name': assistant_name or "Помощник",
                'assistant_salary': assistant_salary,
                'assistant_start_time': assistant_start_time,
                'transaction_id': transaction_id,
                'assistant_transaction_id': assistant_transaction_id
            }

        except Exception as e:
            logger.error(f"❌ Ошибка создания транзакции зарплаты донерщика: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e)
            }


async def calculate_and_create_doner_salary(
    telegram_user_id: int,
    date: str = None,
    assistant_start_time: str = "10:00",
    doner_name: str = None,
    assistant_name: str = None
) -> Dict:
    """
    Рассчитать и создать транзакцию зарплаты донерщика и помощника

    Args:
        telegram_user_id: ID пользователя Telegram
        date: Дата для расчёта в формате "YYYYMMDD". Если None, используется сегодня
        assistant_start_time: Время выхода помощника ("10:00", "12:00", "14:00")
        doner_name: Имя донерщика (опционально, для комментария)
        assistant_name: Имя помощника (опционально, для комментария)

    Returns:
        Dict с результатом операции
    """
    calculator = DonerSalaryCalculator(telegram_user_id)
    return await calculator.create_salary_transaction(date, assistant_start_time, doner_name, assistant_name)
