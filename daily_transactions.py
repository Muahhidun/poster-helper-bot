"""Автоматические ежедневные транзакции"""
import logging
from typing import List, Dict
from datetime import datetime, timezone, timedelta
from poster_client import PosterClient

logger = logging.getLogger(__name__)

# Almaty timezone (UTC+5)
KZ_TZ = timezone(timedelta(hours=5))


class DailyTransactionScheduler:
    """Управление ежедневными автоматическими транзакциями"""

    def __init__(self, telegram_user_id: int):
        self.telegram_user_id = telegram_user_id

    async def _find_category_id(self, poster_client: PosterClient, *keywords: str) -> int | None:
        """Найти ID категории по ключевым словам в названии"""
        try:
            categories = await poster_client.get_categories()
            for cat in categories:
                cat_name = cat.get('finance_category_name', '').lower()
                if all(kw in cat_name for kw in keywords):
                    cat_id = int(cat.get('finance_category_id'))
                    logger.info(f"✅ Найдена категория '{cat.get('finance_category_name')}' ID={cat_id}")
                    return cat_id
        except Exception as e:
            logger.error(f"❌ Ошибка поиска категории: {e}")
        return None

    async def check_transactions_created_today(self) -> bool:
        """
        Проверить, были ли уже созданы ежедневные транзакции сегодня
        Возвращает True если транзакции найдены, False если нет
        """
        try:
            poster_client = PosterClient(self.telegram_user_id)

            # Получить сегодняшнюю дату по Алматы
            today = datetime.now(KZ_TZ).strftime("%Y-%m-%d")

            # Получить транзакции за сегодня
            result = await poster_client._request('GET', 'finance.getTransactions', params={
                'dateFrom': today,
                'dateTo': today
            })

            transactions = result.get('response', [])

            # Закрыть клиент
            await poster_client.close()

            # Проверить наличие характерных транзакций
            # Для первого аккаунта ищем "Мадира Т" или "Нургуль Т"
            # Для второго аккаунта ищем "Сушист"
            if self.telegram_user_id == 167084307:
                # Ищем транзакции с комментариями "Мадира Т" или "Нургуль Т"
                for tx in transactions:
                    comment = tx.get('comment', '')
                    if comment in ['Мадира Т', 'Нургуль Т', 'Заготовка', 'Мадина админ']:
                        logger.info(f"✅ Найдены ежедневные транзакции для пользователя {self.telegram_user_id}")
                        return True
            elif self.telegram_user_id == 8010984368:
                # Ищем транзакцию "Сушист"
                for tx in transactions:
                    category_id = tx.get('finance_category_id')
                    if category_id == '17':  # ID категории Сушист
                        logger.info(f"✅ Найдены ежедневные транзакции для пользователя {self.telegram_user_id}")
                        return True

            logger.info(f"❌ Ежедневные транзакции не найдены для пользователя {self.telegram_user_id}")
            return False

        except Exception as e:
            logger.error(f"❌ Ошибка проверки ежедневных транзакций: {e}")
            return False

    async def create_daily_transactions(self):
        """
        Создать все ежедневные транзакции в 12:00
        Создает транзакции для всех аккаунтов пользователя (Pizzburg и Pizzburg-cafe)
        """
        try:
            # Защита от дублей: проверить, созданы ли уже транзакции сегодня
            already_exists = await self.check_transactions_created_today()
            if already_exists:
                logger.info(f"⏭️ Ежедневные транзакции уже существуют для пользователя {self.telegram_user_id}, пропускаю создание")
                return {
                    'success': True,
                    'count': 0,
                    'transactions': [],
                    'already_exists': True
                }

            from database import get_database

            db = get_database()
            accounts = db.get_accounts(self.telegram_user_id)

            if not accounts:
                logger.warning(f"Нет аккаунтов для пользователя {self.telegram_user_id}")
                return {
                    'success': False,
                    'error': 'No accounts found'
                }

            # Дата и время для всех транзакций
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            all_transactions = []

            # Создать транзакции для каждого аккаунта
            for account in accounts:
                account_name = account['account_name']
                logger.info(f"📦 Создаю ежедневные транзакции для аккаунта '{account_name}'...")

                # Создать PosterClient для этого аккаунта
                poster_client = PosterClient(
                    telegram_user_id=self.telegram_user_id,
                    poster_token=account['poster_token'],
                    poster_user_id=account['poster_user_id'],
                    poster_base_url=account['poster_base_url']
                )

                try:
                    # Выбрать конфигурацию в зависимости от аккаунта
                    if account_name == 'Pizzburg':
                        transactions = await self._create_transactions_pizzburg(poster_client, current_time)
                    elif account_name == 'Pizzburg-cafe':
                        transactions = await self._create_transactions_pizzburg_cafe(poster_client, current_time)
                    else:
                        logger.warning(f"Нет конфигурации для аккаунта '{account_name}'")
                        transactions = []

                    all_transactions.extend([f"[{account_name}] {tx}" for tx in transactions])

                finally:
                    await poster_client.close()

            logger.info(f"✅ Создано {len(all_transactions)} ежедневных транзакций для пользователя {self.telegram_user_id}")
            for tx in all_transactions:
                logger.info(f"  - {tx}")

            return {
                'success': True,
                'count': len(all_transactions),
                'transactions': all_transactions
            }

        except Exception as e:
            logger.error(f"❌ Ошибка создания ежедневных транзакций: {e}")
            return {
                'success': False,
                'error': str(e)
            }

    async def _create_transactions_pizzburg(self, poster_client: PosterClient, current_time: str) -> List[str]:
        """Транзакции для аккаунта Pizzburg (основной)"""
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

        # 1× Повара (ID=17) - 1₸, комментарий "Мадира Т"
        tx_id = await poster_client.create_transaction(
            transaction_type=0,
            category_id=17,  # Повара
            account_from_id=4,
            amount=1,
            date=current_time,
            comment="Мадира Т"
        )
        transactions_created.append(f"Повара (Мадира Т): {tx_id}")

        # 1× Повара (ID=17) - 1₸, комментарий "Нургуль Т"
        tx_id = await poster_client.create_transaction(
            transaction_type=0,
            category_id=17,  # Повара
            account_from_id=4,
            amount=1,
            date=current_time,
            comment="Нургуль Т"
        )
        transactions_created.append(f"Повара (Нургуль Т): {tx_id}")

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

        # 1× Зарплаты - 1₸, комментарий "Мадина админ" (ID категории определяется автоматически)
        zarplaty_id = await self._find_category_id(poster_client, 'зарплат')
        if zarplaty_id:
            tx_id = await poster_client.create_transaction(
                transaction_type=0,
                category_id=zarplaty_id,
                account_from_id=4,
                amount=1,
                date=current_time,
                comment="Мадина админ"
            )
            transactions_created.append(f"Зарплаты (Мадина админ): {tx_id}")
        else:
            logger.warning("⚠️ Категория 'Зарплаты' не найдена в Pizzburg")

        # 3× Логистика - Доставка продуктов (ID=24) с разными комментариями
        logistics_configs = [
            {"comment": "Караганда", "amount": 1},
            {"comment": "Фарш", "amount": 700},
            {"comment": "Кюрдамир", "amount": 1000}
        ]
        for config in logistics_configs:
            tx_id = await poster_client.create_transaction(
                transaction_type=0,
                category_id=24,  # Логистика - Доставка продуктов
                account_from_id=4,
                amount=config["amount"],
                date=current_time,
                comment=config["comment"]
            )
            transactions_created.append(f"Логистика ({config['comment']}): {tx_id}")

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

        # Переводы Kaspi→Wolt, Kaspi→Халык, Инкассация→Оставил в кассе
        # убраны — теперь создаются при закрытии смены с реальными суммами

        # Оставил в кассе (на закупы) → Деньги дома (отложенные) - 1₸, комментарий "Забрал - Имя"
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

    async def _create_transactions_pizzburg_cafe(self, poster_client: PosterClient, current_time: str) -> List[str]:
        """Транзакции для аккаунта Pizzburg-cafe"""
        transactions_created = []

        # === СЧЕТ "Оставил в кассе (на закупы)" (ID=5) ===

        # 1. Кассир - 1₸
        tx_id = await poster_client.create_transaction(
            transaction_type=0,  # expense
            category_id=16,  # Кассир
            account_from_id=5,  # Оставил в кассе (на закупы)
            amount=1,
            date=current_time,
            comment=""
        )
        transactions_created.append(f"Кассир: {tx_id}")

        # 2. Сушист - 1₸
        tx_id = await poster_client.create_transaction(
            transaction_type=0,  # expense
            category_id=17,  # Сушист
            account_from_id=5,  # Оставил в кассе (на закупы)
            amount=1,
            date=current_time,
            comment=""
        )
        transactions_created.append(f"Сушист: {tx_id}")

        # 3. Повар Сандей - 1₸ (ID категории определяется автоматически из API)
        povar_sandey_id = await self._find_category_id(poster_client, 'повар', 'санд')
        if povar_sandey_id:
            tx_id = await poster_client.create_transaction(
                transaction_type=0,  # expense
                category_id=povar_sandey_id,
                account_from_id=5,  # Оставил в кассе (на закупы)
                amount=1,
                date=current_time,
                comment=""
            )
            transactions_created.append(f"Повар Сандей: {tx_id}")
        else:
            logger.warning("⚠️ Категория 'Повар Сандей' не найдена в Pizzburg-cafe")

        # Переводы Инкассация→Оставил в кассе и Kaspi→Wolt
        # убраны — теперь создаются при закрытии смены с реальными суммами

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
