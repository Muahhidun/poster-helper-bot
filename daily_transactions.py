"""Автоматические ежедневные транзакции"""
import logging
import pytz
from typing import List, Dict
from datetime import datetime, timedelta
from poster_client import PosterClient

logger = logging.getLogger(__name__)

# Almaty timezone — use pytz to avoid issues with server TZ config
KZ_TZ = pytz.timezone('Asia/Almaty')


class DailyTransactionScheduler:
    """Управление ежедневными автоматическими транзакциями"""

    def __init__(self, telegram_user_id: int):
        self.telegram_user_id = telegram_user_id

    async def _find_category_id(self, poster_client: PosterClient, *keywords: str) -> int | None:
        """Найти ID категории по ключевым словам в названии"""
        try:
            categories = await poster_client.get_categories()
            for cat in categories:
                cat_name = (cat.get('category_name') or cat.get('name') or '').lower()
                if all(kw in cat_name for kw in keywords):
                    cat_id = int(cat.get('category_id'))
                    display_name = cat.get('category_name') or cat.get('name') or '?'
                    logger.info(f"✅ Найдена категория '{display_name}' ID={cat_id}")
                    return cat_id
        except Exception as e:
            logger.error(f"❌ Ошибка поиска категории: {e}")
        return None

    def _comment_exists(self, marker: str, existing_comments: set) -> bool:
        """
        Проверить, есть ли транзакция с данным комментарием (substring matching).
        Например, маркер 'Заготовка' найдётся в комментарии 'Заготовка Полина'.
        """
        if not marker:
            return False
        for existing in existing_comments:
            if marker in existing or existing in marker:
                return True
        return False

    async def _get_account_existing_data(self, poster_client: PosterClient) -> dict:
        """
        Получить существующие транзакции для конкретного аккаунта Poster.
        Возвращает comments (set) и category_ids (set) для проверки дублей.
        """
        try:
            today = datetime.now(KZ_TZ).strftime("%Y-%m-%d")
            result = await poster_client._request('GET', 'finance.getTransactions', params={
                'dateFrom': today,
                'dateTo': today
            })
            transactions = result.get('response', [])

            comments = set()
            category_ids = set()
            for tx in transactions:
                comment = tx.get('comment', '').strip()
                if comment:
                    comments.add(comment)
                # Try both field names for robustness
                cat_id = tx.get('category_id') or tx.get('finance_category_id')
                if cat_id:
                    category_ids.add(str(cat_id))

            logger.info(f"🔍 Account data: {len(transactions)} tx, comments={len(comments)}, category_ids={category_ids}")
            return {'comments': comments, 'category_ids': category_ids}
        except Exception as e:
            logger.error(f"❌ Ошибка получения данных аккаунта: {e}")
            return {'comments': set(), 'category_ids': set()}

    def _check_all_markers_present(self, existing_comments: set) -> bool:
        """
        Проверить, все ли ключевые маркеры присутствуют в existing_comments.
        Используется для быстрой проверки без повторного API-запроса.
        """
        if not existing_comments:
            return False

        if self.telegram_user_id == 167084307:
            required = {'Заготовка', 'Мадира Т', 'Нургуль Т', 'Мадина админ'}
            missing = {m for m in required if not self._comment_exists(m, existing_comments)}
            if missing:
                logger.info(f"⚠️ Частично созданы транзакции для {self.telegram_user_id}. Отсутствуют: {missing}")
                return False
            logger.info(f"✅ Все ежедневные транзакции найдены для пользователя {self.telegram_user_id}")
            return True
        elif self.telegram_user_id == 8010984368:
            if '__cafe_sushist__' in existing_comments:
                logger.info(f"✅ Найдены ежедневные транзакции для пользователя {self.telegram_user_id}")
                return True

        logger.info(f"❌ Ежедневные транзакции не найдены для пользователя {self.telegram_user_id}")
        return False

    async def check_transactions_created_today(self) -> bool:
        """
        Проверить, были ли уже созданы ежедневные транзакции сегодня.
        Возвращает True если ВСЕ ключевые транзакции найдены, False если нет.
        Использует substring matching: маркер 'Заготовка' найдёт 'Заготовка Полина'.
        """
        existing = await self.get_existing_daily_comments()
        return self._check_all_markers_present(existing)

    async def get_existing_daily_comments(self) -> set:
        """
        Получить множество комментариев существующих транзакций за сегодня.
        Используется для per-transaction дубликат-проверки.
        """
        try:
            poster_client = PosterClient(self.telegram_user_id)

            today = datetime.now(KZ_TZ).strftime("%Y-%m-%d")

            result = await poster_client._request('GET', 'finance.getTransactions', params={
                'dateFrom': today,
                'dateTo': today
            })

            transactions = result.get('response', [])
            await poster_client.close()

            logger.info(f"🔍 [{self.telegram_user_id}] Найдено {len(transactions)} транзакций за {today}")
            # Детальное логирование только на уровне DEBUG (включить при необходимости)
            for tx in transactions:
                logger.debug(
                    f"  📋 TX#{tx.get('transaction_id', '?')} type={tx.get('type', '?')} "
                    f"cat={tx.get('category_id', tx.get('finance_category_id', '?'))} "
                    f"acc={tx.get('account_id', '?')} amount={tx.get('amount', '?')} "
                    f"comment='{tx.get('comment', '')}' date={tx.get('date', '?')}"
                )

            existing = set()
            for tx in transactions:
                comment = tx.get('comment', '').strip()
                if comment:
                    existing.add(comment)
                # Special marker for cafe category-based detection
                category_id = tx.get('category_id') or tx.get('finance_category_id')
                if str(category_id) == '17':
                    existing.add('__cafe_sushist__')

            logger.info(f"🔍 [{self.telegram_user_id}] Уникальные комментарии: {existing}")
            return existing

        except Exception as e:
            logger.error(f"❌ Ошибка получения транзакций за сегодня: {e}")
            return set()

    async def create_daily_transactions(self):
        """
        Создать все ежедневные транзакции в 12:00
        Создает транзакции для всех аккаунтов пользователя (Pizzburg и Pizzburg-cafe).
        Проверяет каждую транзакцию по отдельности — пропускает уже существующие,
        создаёт недостающие.
        """
        try:
            # Получить существующие транзакции за сегодня для per-transaction проверки
            existing_comments = await self.get_existing_daily_comments()

            # Если ВСЕ ключевые транзакции есть — пропустить
            # Проверяем прямо здесь чтобы не делать повторный API-запрос
            already_exists = self._check_all_markers_present(existing_comments)
            if already_exists:
                logger.info(f"⏭️ Все ежедневные транзакции уже существуют для пользователя {self.telegram_user_id}, пропускаю создание")
                return {
                    'success': True,
                    'count': 0,
                    'transactions': [],
                    'already_exists': True
                }

            if existing_comments:
                logger.info(f"📋 Найдены частичные транзакции для {self.telegram_user_id}, создаю недостающие. Существующие: {existing_comments}")

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
                    # Получить существующие транзакции ЭТОГО аккаунта (не основного)
                    account_existing = await self._get_account_existing_data(poster_client)

                    # Выбрать конфигурацию в зависимости от аккаунта
                    if account_name == 'Pizzburg':
                        transactions = await self._create_transactions_pizzburg(poster_client, current_time, account_existing)
                    elif account_name == 'Pizzburg-cafe':
                        transactions = await self._create_transactions_pizzburg_cafe(poster_client, current_time, account_existing)
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

    async def _create_transactions_pizzburg(self, poster_client: PosterClient, current_time: str, existing_data: dict = None) -> List[str]:
        """Транзакции для аккаунта Pizzburg (основной).
        Пропускает транзакции, которые уже существуют (по комментарию или category_id)."""
        transactions_created = []
        if existing_data is None:
            existing_data = {'comments': set(), 'category_ids': set()}
        existing_comments = existing_data.get('comments', set())
        existing_category_ids = existing_data.get('category_ids', set())

        def _should_skip(comment: str = None, category_id: int = None) -> bool:
            """Проверить дубликат по комментарию (substring) или category_id.
            Для транзакций с пустым комментарием используем category_id."""
            if comment and self._comment_exists(comment, existing_comments):
                logger.info(f"⏭️ Пропускаю (уже есть): '{comment}'")
                return True
            if category_id is not None and str(category_id) in existing_category_ids:
                logger.info(f"⏭️ Пропускаю (category {category_id} уже есть)")
                return True
            return False

        # === СЧЕТ "Оставил в кассе" (ID=4) ===

        # ПРИМЕЧАНИЕ: Транзакции кассиров и донерщиков теперь создаются автоматически в 21:30
        # на основе продаж за день (см. cashier_salary.py и doner_salary.py)

        # 1× Повара (ID=17) - 1₸, комментарий "Заготовка"
        if not _should_skip("Заготовка"):
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
        if not _should_skip("Мадира Т"):
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
        if not _should_skip("Нургуль Т"):
            tx_id = await poster_client.create_transaction(
                transaction_type=0,
                category_id=17,  # Повара
                account_from_id=4,
                amount=1,
                date=current_time,
                comment="Нургуль Т"
            )
            transactions_created.append(f"Повара (Нургуль Т): {tx_id}")

        # 1× Кухрабочая (ID=18) - 1₸ (пустой комментарий → проверяем по category_id)
        if not _should_skip(category_id=18):
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
        if not _should_skip("Курьеры"):
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
        if not _should_skip("Мадина админ"):
            zarplaty_id = await self._find_category_id(poster_client, 'зарплат')
            if zarplaty_id is None:
                # Fallback: try broader search
                zarplaty_id = await self._find_category_id(poster_client, 'зарпл')
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
                try:
                    categories = await poster_client.get_categories()
                    cat_names = [f"{c.get('category_name') or c.get('name')} (ID={c.get('category_id')})" for c in categories]
                    logger.warning(f"⚠️ Категория 'Зарплаты' не найдена в Pizzburg. Доступные: {cat_names}")
                except Exception:
                    logger.warning("⚠️ Категория 'Зарплаты' не найдена в Pizzburg")

        # 3× Логистика - Доставка продуктов (ID=24) с разными комментариями
        logistics_configs = [
            {"comment": "Караганда", "amount": 1},
            {"comment": "Фарш", "amount": 700},
            {"comment": "Кюрдамир", "amount": 1000}
        ]
        for config in logistics_configs:
            if not _should_skip(config["comment"]):
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
        if not _should_skip("Реклама"):
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
        if not _should_skip("Астана"):
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
        if not _should_skip("Комиссия"):
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
        if not _should_skip("Забрал - Имя"):
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

    async def _create_transactions_pizzburg_cafe(self, poster_client: PosterClient, current_time: str, existing_data: dict = None) -> List[str]:
        """Транзакции для аккаунта Pizzburg-cafe.
        Пропускает транзакции, которые уже существуют (по category_id, т.к. комментарии пустые)."""
        transactions_created = []
        if existing_data is None:
            existing_data = {'comments': set(), 'category_ids': set()}
        existing_category_ids = existing_data.get('category_ids', set())

        def _should_skip_cat(category_id: int) -> bool:
            """Проверить, есть ли уже транзакция с таким category_id."""
            if str(category_id) in existing_category_ids:
                logger.info(f"⏭️ Пропускаю cafe (category {category_id} уже есть)")
                return True
            return False

        # === СЧЕТ "Оставил в кассе (на закупы)" (ID=5) ===

        # 1. Кассир - 1₸
        if not _should_skip_cat(16):
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
        if not _should_skip_cat(17):
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
        if povar_sandey_id is None:
            # Fallback: just 'повар'
            povar_sandey_id = await self._find_category_id(poster_client, 'повар')
        if povar_sandey_id:
            if not _should_skip_cat(povar_sandey_id):
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
            try:
                categories = await poster_client.get_categories()
                cat_names = [f"{c.get('category_name') or c.get('name')} (ID={c.get('category_id')})" for c in categories]
                logger.warning(f"⚠️ Категория 'Повар Сандей' не найдена в Pizzburg-cafe. Доступные: {cat_names}")
            except Exception:
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
