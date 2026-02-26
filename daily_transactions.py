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
            # ВАЖНО: finance.getTransactions ожидает формат YYYYMMDD (не YYYY-MM-DD!)
            today = datetime.now(KZ_TZ).strftime("%Y%m%d")
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

    async def check_transactions_created_today(self) -> bool:
        """
        Проверить, были ли уже созданы ежедневные транзакции сегодня.
        Сначала проверяет флаг в БД (быстро), потом Poster API (надёжно).
        """
        from database import get_database
        db = get_database()
        today = datetime.now(KZ_TZ).strftime("%Y-%m-%d")

        # Быстрая проверка по флагу в БД
        if db.is_daily_transactions_created(self.telegram_user_id, today):
            return True

        return False

    async def create_daily_transactions(self):
        """
        Создать все ежедневные транзакции в 12:00.
        Только для аккаунта Pizzburg (зарплаты Кафе убраны — создаются при закрытии смены).

        Защита от дублей (3 уровня):
        1. Глобальный флаг в БД — если ЛЮБОЙ пользователь уже создал транзакции за сегодня, пропускаем
           (решает проблему: 2 пользователя с одним Poster аккаунтом)
        2. Per-user флаг в БД — если этот пользователь уже создал, пропускаем
           (решает проблему: повторный запуск при рестартах)
        3. Per-account проверка в Poster API — пропускает уже существующие транзакции
           (решает проблему: транзакции созданы вручную или другим способом)
        """
        try:
            from database import get_database
            db = get_database()
            today = datetime.now(KZ_TZ).strftime("%Y-%m-%d")

            # 1. ГЛОБАЛЬНАЯ проверка — если ЛЮБОЙ пользователь уже создал за сегодня
            if db.is_daily_transactions_created_for_date(today):
                logger.info(f"⏭️ Daily transactions уже созданы за {today} другим пользователем (глобальный флаг)")
                return {
                    'success': True,
                    'count': 0,
                    'transactions': [],
                    'already_exists': True
                }

            # 2. Per-user проверка — если этот пользователь уже создал
            if db.is_daily_transactions_created(self.telegram_user_id, today):
                logger.info(f"⏭️ Daily transactions уже созданы за {today} для {self.telegram_user_id} (флаг в БД)")
                return {
                    'success': True,
                    'count': 0,
                    'transactions': [],
                    'already_exists': True
                }

            # 3. CLAIM: установить флаг ДО создания (count=-1 = "в процессе")
            # Это предотвращает race condition когда 2 пользователя стартуют одновременно
            db.set_daily_transactions_created(self.telegram_user_id, today, -1)
            logger.info(f"🔒 Claim установлен для {self.telegram_user_id} за {today}")

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

                # Создать PosterClient для этого аккаунта
                poster_client = PosterClient(
                    telegram_user_id=self.telegram_user_id,
                    poster_token=account['poster_token'],
                    poster_user_id=account['poster_user_id'],
                    poster_base_url=account['poster_base_url']
                )

                try:
                    # 4. Получить существующие транзакции ЭТОГО аккаунта для per-transaction дедупликации
                    account_existing = await self._get_account_existing_data(poster_client)

                    # Выбрать конфигурацию в зависимости от аккаунта
                    if account_name == 'Pizzburg':
                        logger.info(f"📦 Создаю ежедневные транзакции для аккаунта '{account_name}'...")
                        transactions = await self._create_transactions_pizzburg(poster_client, current_time, account_existing)
                        all_transactions.extend([f"[{account_name}] {tx}" for tx in transactions])
                    elif account_name == 'Pizzburg-cafe':
                        # Зарплаты Кафе НЕ создаём — их создаёт админ при закрытии смены
                        logger.info(f"⏭️ Пропускаю '{account_name}' — зарплаты создаются при закрытии смены")
                    else:
                        logger.warning(f"Нет конфигурации для аккаунта '{account_name}'")

                finally:
                    await poster_client.close()

            # 5. Обновить флаг с реальным количеством (claim → done)
            db.set_daily_transactions_created(self.telegram_user_id, today, len(all_transactions))

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

        # Ранний выход: если большинство ожидаемых маркеров уже найдены в Poster,
        # значит транзакции уже созданы (возможно другим пользователем или вручную)
        expected_markers = ["Заготовка", "Мадира", "Нургуль", "Курьеры", "Караганда",
                           "Фарш", "Кюрдамир", "Реклама", "Астана", "Комиссия", "Забрал"]
        found_markers = sum(1 for m in expected_markers if self._comment_exists(m, existing_comments))
        if found_markers >= 7:
            logger.info(
                f"⏭️ Pizzburg: {found_markers}/{len(expected_markers)} маркеров уже найдено в Poster — "
                f"транзакции уже существуют, пропускаю создание"
            )
            return []

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
                zarplaty_id = await self._find_category_id(poster_client, 'зарпл')
            if zarplaty_id is None:
                # Системные категории Poster имеют английские имена
                zarplaty_id = await self._find_category_id(poster_client, 'labour_cost')
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


# Конфигурация для пользователей
# Ключ: telegram_user_id, значение: включены ли авто-транзакции
DAILY_TRANSACTIONS_ENABLED = {
    167084307: True,  # Основной аккаунт
    8010984368: True,  # Второй аккаунт
}


def is_daily_transactions_enabled(telegram_user_id: int) -> bool:
    """Проверить, включены ли авто-транзакции для пользователя"""
    return DAILY_TRANSACTIONS_ENABLED.get(telegram_user_id, False)
