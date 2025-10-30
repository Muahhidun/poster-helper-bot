"""Автоматические ежедневные транзакции"""
import logging
from typing import List, Dict
from datetime import datetime
from poster_client import PosterClient

logger = logging.getLogger(__name__)


class DailyTransactionScheduler:
    """Управление ежедневными автоматическими транзакциями"""

    def __init__(self, telegram_user_id: int):
        self.telegram_user_id = telegram_user_id

    async def create_daily_transactions(self):
        """
        Создать все ежедневные транзакции в 12:00
        Выбирает конфигурацию в зависимости от пользователя
        """
        try:
            poster_client = PosterClient(self.telegram_user_id)

            # Дата и время для всех транзакций
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            transactions_created = []

            # Выбрать конфигурацию в зависимости от пользователя
            if self.telegram_user_id == 167084307:
                # Первый аккаунт (основной)
                transactions_created = await self._create_transactions_account_1(poster_client, current_time)
            elif self.telegram_user_id == 8010984368:
                # Второй аккаунт
                transactions_created = await self._create_transactions_account_2(poster_client, current_time)
            else:
                logger.warning(f"Нет конфигурации для пользователя {self.telegram_user_id}")

            # Закрыть клиент
            await poster_client.close()

            logger.info(f"✅ Создано {len(transactions_created)} ежедневных транзакций для пользователя {self.telegram_user_id}")
            for tx in transactions_created:
                logger.info(f"  - {tx}")

            return {
                'success': True,
                'count': len(transactions_created),
                'transactions': transactions_created
            }

        except Exception as e:
            logger.error(f"❌ Ошибка создания ежедневных транзакций: {e}")
            return {
                'success': False,
                'error': str(e)
            }

    async def _create_transactions_account_1(self, poster_client: PosterClient, current_time: str) -> List[str]:
        """Транзакции для первого аккаунта (167084307)"""
        transactions_created = []

        # === СЧЕТ "Оставил в кассе" (ID=4) ===

        # ПРИМЕЧАНИЕ: Транзакции кассиров и донерщиков теперь создаются автоматически в 21:30
        # на основе продаж за день (см. cashier_salary.py и doner_salary.py)

        # 1× Повара (ID=17) - 1₸, комментарий "Заготовка"
        tx_id = await poster_client.create_transaction(
            transaction_type=0,
            category_id=17,  # Повара
            account_from_id=4,
            amount=1,
            date=current_time,
            comment="Заготовка"
        )
        transactions_created.append(f"Повара: {tx_id}")

        # 1× Повара (ID=17) - 1₸, комментарий "Мадира"
        tx_id = await poster_client.create_transaction(
            transaction_type=0,
            category_id=17,  # Повара
            account_from_id=4,
            amount=1,
            date=current_time,
            comment="Мадира"
        )
        transactions_created.append(f"Повара (Мадира): {tx_id}")

        # 1× Повара (ID=17) - 1₸, комментарий "Нургуль"
        tx_id = await poster_client.create_transaction(
            transaction_type=0,
            category_id=17,  # Повара
            account_from_id=4,
            amount=1,
            date=current_time,
            comment="Нургуль"
        )
        transactions_created.append(f"Повара (Нургуль): {tx_id}")

        # 1× Кухрабочая (ID=18) - 1₸
        tx_id = await poster_client.create_transaction(
            transaction_type=0,
            category_id=18,  # КухРабочая
            account_from_id=4,
            amount=1,
            date=current_time,
            comment=""
        )
        transactions_created.append(f"Кухрабочая: {tx_id}")

        # 1× Курьер (ID=15) - 1₸, комментарий "Курьеры"
        tx_id = await poster_client.create_transaction(
            transaction_type=0,
            category_id=15,  # Курьер
            account_from_id=4,
            amount=1,
            date=current_time,
            comment="Курьеры"
        )
        transactions_created.append(f"Курьер: {tx_id}")

        # 3× Логистика - Доставка продуктов (ID=24) с разными комментариями
        logistics_comments = ["Караганда", "Фарш", "Кюрдамир"]
        for comment in logistics_comments:
            tx_id = await poster_client.create_transaction(
                transaction_type=0,
                category_id=24,  # Логистика - Доставка продуктов
                account_from_id=4,
                amount=1,
                date=current_time,
                comment=comment
            )
            transactions_created.append(f"Логистика ({comment}): {tx_id}")

        # === СЧЕТ "Kaspi Pay" (ID=1) ===

        # 1× Маркетинг (ID=7) - 4100₸, комментарий "Реклама"
        tx_id = await poster_client.create_transaction(
            transaction_type=0,
            category_id=7,  # Маркетинг
            account_from_id=1,  # Kaspi Pay
            amount=4100,
            date=current_time,
            comment="Реклама"
        )
        transactions_created.append(f"Маркетинг: {tx_id}")

        # 1× Логистика - Доставка продуктов (ID=24) - 1₸, комментарий "Астана"
        tx_id = await poster_client.create_transaction(
            transaction_type=0,
            category_id=24,  # Логистика - Доставка продуктов
            account_from_id=1,  # Kaspi Pay
            amount=1,
            date=current_time,
            comment="Астана"
        )
        transactions_created.append(f"Логистика (Астана): {tx_id}")

        # 1× Банковские услуги и комиссии (ID=5) - 1₸, комментарий "Комиссия"
        tx_id = await poster_client.create_transaction(
            transaction_type=0,
            category_id=5,  # Банковские услуги и комиссии
            account_from_id=1,  # Kaspi Pay
            amount=1,
            date=current_time,
            comment="Комиссия"
        )
        transactions_created.append(f"Банковские услуги: {tx_id}")

        # === ПЕРЕВОДЫ ===

        # 1× Kaspi Pay → Wolt доставка - 1₸
        tx_id = await poster_client.create_transaction(
            transaction_type=2,  # transfer
            category_id=0,  # не используется для переводов
            account_from_id=1,  # Kaspi Pay
            account_to_id=8,  # Wolt доставка
            amount=1,
            date=current_time,
            comment=""
        )
        transactions_created.append(f"Перевод Kaspi → Wolt: {tx_id}")

        # 2× Kaspi Pay → Халык банк - 1₸
        tx_id = await poster_client.create_transaction(
            transaction_type=2,  # transfer
            category_id=0,  # не используется для переводов
            account_from_id=1,  # Kaspi Pay
            account_to_id=10,  # Халык банк
            amount=1,
            date=current_time,
            comment=""
        )
        transactions_created.append(f"Перевод Kaspi → Халык: {tx_id}")

        # 3× Инкассация (вечером) → Оставил в кассе (на закупы) - 1₸
        tx_id = await poster_client.create_transaction(
            transaction_type=2,  # transfer
            category_id=0,  # не используется для переводов
            account_from_id=2,  # Инкассация (вечером)
            account_to_id=4,  # Оставил в кассе (на закупы)
            amount=1,
            date=current_time,
            comment=""
        )
        transactions_created.append(f"Перевод Инкассация → Оставил в кассе: {tx_id}")

        # 4× Оставил в кассе (на закупы) → Деньги дома (отложенные) - 1₸, комментарий "Забрал - Имя"
        tx_id = await poster_client.create_transaction(
            transaction_type=2,  # transfer
            category_id=0,  # не используется для переводов
            account_from_id=4,  # Оставил в кассе (на закупы)
            account_to_id=5,  # Деньги дома (отложенные)
            amount=1,
            date=current_time,
            comment="Забрал - Имя"
        )
        transactions_created.append(f"Перевод Оставил в кассе → Деньги дома: {tx_id}")

        return transactions_created

    async def _create_transactions_account_2(self, poster_client: PosterClient, current_time: str) -> List[str]:
        """Транзакции для второго аккаунта (8010984368)"""
        transactions_created = []

        # === СЧЕТ "Оставил в кассе" (ID=5) ===

        # 1× Сушист (ID=17) - 1₸
        tx_id = await poster_client.create_transaction(
            transaction_type=0,  # expense
            category_id=17,  # Сушист
            account_from_id=5,  # Оставил в кассе (на закупы)
            amount=1,
            date=current_time,
            comment=""
        )
        transactions_created.append(f"Сушист: {tx_id}")

        # 1× Кассир (ID=16) - 1₸
        tx_id = await poster_client.create_transaction(
            transaction_type=0,
            category_id=16,  # Кассир
            account_from_id=5,
            amount=1,
            date=current_time,
            comment=""
        )
        transactions_created.append(f"Кассир: {tx_id}")

        # 1× Повар Сандей (ID=28) - 10000₸
        tx_id = await poster_client.create_transaction(
            transaction_type=0,
            category_id=28,  # Повар Сандей
            account_from_id=5,
            amount=10000,
            date=current_time,
            comment=""
        )
        transactions_created.append(f"Повар Сандей: {tx_id}")

        # === ПЕРЕВОДЫ ===

        # 1× Kaspi Pay → Wolt доставка - 1₸
        tx_id = await poster_client.create_transaction(
            transaction_type=2,  # transfer
            category_id=0,  # не используется для переводов
            account_from_id=1,  # Kaspi Pay
            account_to_id=7,  # Wolt доставка
            amount=1,
            date=current_time,
            comment=""
        )
        transactions_created.append(f"Перевод Kaspi → Wolt: {tx_id}")

        # 2× Инкассация (вечером) → Оставил в кассе (на закупы) - 1₸
        tx_id = await poster_client.create_transaction(
            transaction_type=2,  # transfer
            category_id=0,  # не используется для переводов
            account_from_id=2,  # Инкассация (вечером)
            account_to_id=5,  # Оставил в кассе (на закупы)
            amount=1,
            date=current_time,
            comment=""
        )
        transactions_created.append(f"Перевод Инкассация → Оставил в кассе: {tx_id}")

        return transactions_created


# Конфигурация для пользователей
# Ключ: telegram_user_id, значение: включены ли авто-транзакции
DAILY_TRANSACTIONS_ENABLED = {
    167084307: True,  # Основной аккаунт
    8010984368: True,  # Второй аккаунт
}


def is_daily_transactions_enabled(telegram_user_id: int) -> bool:
    """Проверить, включены ли авто-транзакции для пользователя"""
    return DAILY_TRANSACTIONS_ENABLED.get(telegram_user_id, False)
