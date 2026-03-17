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

    def _comment_exists(self, marker: str, existing_comments: set, strict: bool = False) -> bool:
        """
        Проверить, есть ли транзакция с данным комментарием.

        strict=False (default): substring matching — маркер 'Заготовка' найдётся
        в комментарии 'Заготовка Полина'.

        strict=True: exact match (case-insensitive) — 'Мадира Т' не матчит 'Мадира'.
        """
        if not marker:
            return False
        marker_lower = marker.lower().strip()
        for existing in existing_comments:
            existing_lower = existing.lower().strip()
            if strict:
                if marker_lower == existing_lower:
                    return True
            else:
                if marker_lower in existing_lower or existing_lower in marker_lower:
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
        Проверяет только флаг в БД (быстро). Poster API проверяется отдельно
        через verify_transactions_in_poster() при необходимости.
        """
        from database import get_database
        db = get_database()
        today = datetime.now(KZ_TZ).strftime("%Y-%m-%d")

        # Проверка по флагу в БД (row existence = attempted)
        if db.is_daily_transactions_created(self.telegram_user_id, today):
            return True

        return False

    async def verify_transactions_in_poster(self) -> bool:
        """
        Проверить наличие ежедневных транзакций непосредственно в Poster API.
        Используется как fallback если флаг в БД отсутствует (перезапуск, сбой БД).
        """
        from database import get_database
        db = get_database()

        try:
            accounts = db.get_accounts(self.telegram_user_id)
            if not accounts:
                return False

            # Загрузить конфигурацию
            db.seed_daily_transaction_configs(self.telegram_user_id)
            tx_configs = db.get_daily_transaction_configs(self.telegram_user_id)
            config_comments = [c.get('comment', '') for c in tx_configs if c.get('comment') and c.get('is_enabled')]

            if not config_comments:
                return False

            # Проверить первый аккаунт (Pizzburg — основной)
            account = accounts[0]
            poster_client = PosterClient(
                telegram_user_id=self.telegram_user_id,
                poster_token=account['poster_token'],
                poster_user_id=account['poster_user_id'],
                poster_base_url=account['poster_base_url']
            )

            try:
                existing = await self._get_account_existing_data(poster_client)
                existing_comments = existing.get('comments', set())

                # Если >= 3 маркеров из конфига найдены в Poster — транзакции есть
                found = sum(1 for c in config_comments if self._comment_exists(c, existing_comments))
                threshold = max(3, len(config_comments) // 2)

                if found >= threshold:
                    logger.info(f"✅ Poster API: найдено {found}/{len(config_comments)} маркеров — транзакции уже существуют")
                    return True
                else:
                    logger.info(f"🔍 Poster API: найдено только {found}/{len(config_comments)} маркеров — транзакции не найдены")
                    return False
            finally:
                await poster_client.close()

        except Exception as e:
            logger.error(f"❌ Ошибка проверки транзакций в Poster: {e}")
            return False

    async def create_daily_transactions(self):
        """
        Создать все ежедневные транзакции в 9:00.
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

            # 3. ATOMIC CLAIM: попытаться захватить слот (INSERT ON CONFLICT DO NOTHING)
            # Если другой процесс уже захватил — вернёт False и мы не создадим дубли
            claimed = db.try_claim_daily_transactions(self.telegram_user_id, today)
            if not claimed:
                logger.info(f"⏭️ Claim не удался — другой процесс уже захватил слот для {today}")
                return {
                    'success': True,
                    'count': 0,
                    'transactions': [],
                    'already_exists': True
                }
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

            # Загрузить конфигурацию из БД (если пуста — seed defaults)
            db.seed_daily_transaction_configs(self.telegram_user_id)
            tx_configs = db.get_daily_transaction_configs(self.telegram_user_id)

            # Группировать конфиги по account_name
            configs_by_account: Dict[str, list] = {}
            for cfg in tx_configs:
                if not cfg.get('is_enabled'):
                    continue
                acc_name = cfg.get('account_name', 'Pizzburg')
                if acc_name not in configs_by_account:
                    configs_by_account[acc_name] = []
                configs_by_account[acc_name].append(cfg)

            # Создать транзакции для каждого аккаунта
            for account in accounts:
                account_name = account['account_name']
                account_configs = configs_by_account.get(account_name, [])

                if not account_configs:
                    logger.info(f"⏭️ Нет включённых транзакций для аккаунта '{account_name}'")
                    continue

                # Создать PosterClient для этого аккаунта
                poster_client = PosterClient(
                    telegram_user_id=self.telegram_user_id,
                    poster_token=account['poster_token'],
                    poster_user_id=account['poster_user_id'],
                    poster_base_url=account['poster_base_url']
                )

                try:
                    # 4. Получить существующие транзакции для per-transaction дедупликации
                    account_existing = await self._get_account_existing_data(poster_client)

                    logger.info(f"📦 Создаю {len(account_configs)} ежедневных транзакций для '{account_name}'...")
                    transactions = await self._create_transactions_from_config(
                        poster_client, current_time, account_configs, account_existing
                    )
                    all_transactions.extend([f"[{account_name}] {tx}" for tx in transactions])

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

    async def _create_transactions_from_config(
        self, poster_client: PosterClient, current_time: str,
        configs: List[Dict], existing_data: dict = None
    ) -> List[str]:
        """Создать транзакции из конфигурации в БД.
        Пропускает транзакции, которые уже существуют (по комментарию или category_id)."""
        transactions_created = []
        if existing_data is None:
            existing_data = {'comments': set(), 'category_ids': set()}
        existing_comments = existing_data.get('comments', set())
        existing_category_ids = existing_data.get('category_ids', set())

        # Ранний выход: если большинство комментариев из конфига уже найдены в Poster
        # Используем strict=True чтобы не путать "Мадира" и "Мадира Т"
        config_comments = [c.get('comment', '') for c in configs if c.get('comment')]
        if config_comments:
            found = sum(1 for c in config_comments if self._comment_exists(c, existing_comments, strict=True))
            threshold = max(3, len(config_comments) // 2)
            if found >= threshold:
                logger.info(
                    f"⏭️ {found}/{len(config_comments)} маркеров уже найдено в Poster (exact match) — "
                    f"транзакции уже существуют, пропускаю создание"
                )
                return []

        # Кэш для автоопределения категорий (category_id=0)
        auto_category_cache: Dict[str, int] = {}

        for cfg in configs:
            comment = cfg.get('comment', '')
            category_id = cfg.get('category_id', 0)
            category_name = cfg.get('category_name', '')
            tx_type = cfg.get('transaction_type', 0)

            # Дедупликация: по комментарию (exact match) или category_id
            if comment and self._comment_exists(comment, existing_comments, strict=True):
                logger.info(f"⏭️ Пропускаю (уже есть): '{comment}'")
                continue
            if not comment and category_id > 0 and str(category_id) in existing_category_ids:
                logger.info(f"⏭️ Пропускаю (category {category_id} уже есть)")
                continue

            # Автоопределение category_id по category_name (для записей с id=0)
            actual_category_id = category_id
            if category_id == 0 and category_name and tx_type != 2:
                cache_key = category_name.lower()
                if cache_key in auto_category_cache:
                    actual_category_id = auto_category_cache[cache_key]
                else:
                    # Поиск по ключевым словам из category_name
                    keywords = [w.lower() for w in category_name.split() if len(w) > 2]
                    if keywords:
                        found_id = await self._find_category_id(poster_client, *keywords)
                        if found_id:
                            actual_category_id = found_id
                            auto_category_cache[cache_key] = found_id
                        else:
                            logger.warning(f"⚠️ Категория '{category_name}' не найдена, пропускаю")
                            continue

            # Создать транзакцию
            try:
                tx_kwargs = {
                    'transaction_type': tx_type,
                    'category_id': actual_category_id,
                    'account_from_id': cfg.get('account_from_id', 4),
                    'amount': cfg.get('amount', 1),
                    'date': current_time,
                    'comment': comment,
                }
                if tx_type == 2 and cfg.get('account_to_id'):
                    tx_kwargs['account_to_id'] = cfg['account_to_id']

                tx_id = await poster_client.create_transaction(**tx_kwargs)
                label = category_name or f"cat={actual_category_id}"
                if comment:
                    label = f"{label} ({comment})"
                transactions_created.append(f"{label}: {tx_id}")
            except Exception as e:
                logger.error(f"❌ Ошибка создания транзакции '{comment}': {e}")

        return transactions_created

    async def cleanup_duplicate_daily_transactions(self):
        """
        Проверить Poster на дубли ежедневных транзакций и удалить лишние.
        Запускается через ~2.5 часа после основного создания (13:00),
        чтобы поймать дубли от старых деплоев/инстансов.
        """
        try:
            from database import get_database
            db = get_database()
            today = datetime.now(KZ_TZ).strftime("%Y-%m-%d")

            # Только если мы сегодня создавали транзакции
            if not db.is_daily_transactions_created(self.telegram_user_id, today):
                return {'cleaned': 0, 'skipped': True}

            accounts = db.get_accounts(self.telegram_user_id)
            if not accounts:
                return {'cleaned': 0}

            # Загрузить текущий конфиг
            tx_configs = db.get_daily_transaction_configs(self.telegram_user_id)
            enabled_configs = [c for c in tx_configs if c.get('is_enabled')]

            # Группировать конфиги по account_name
            configs_by_account = {}
            for cfg in enabled_configs:
                acc_name = cfg.get('account_name', 'Pizzburg')
                if acc_name not in configs_by_account:
                    configs_by_account[acc_name] = []
                configs_by_account[acc_name].append(cfg)

            total_cleaned = 0

            for account in accounts:
                account_name = account['account_name']
                account_configs = configs_by_account.get(account_name, [])
                if not account_configs:
                    continue

                poster_client = PosterClient(
                    telegram_user_id=self.telegram_user_id,
                    poster_token=account['poster_token'],
                    poster_user_id=account['poster_user_id'],
                    poster_base_url=account['poster_base_url']
                )

                try:
                    today_fmt = datetime.now(KZ_TZ).strftime("%Y%m%d")
                    transactions = await poster_client.get_transactions(today_fmt, today_fmt)

                    if not transactions:
                        continue

                    # Построить маппинг: comment → list of transactions
                    # Только расходы и доходы (type 0, 1), не переводы
                    comment_groups = {}
                    for tx in transactions:
                        tx_type = str(tx.get('type', ''))
                        comment = (tx.get('comment') or '').strip()
                        if not comment or tx_type == '2':
                            continue
                        if comment not in comment_groups:
                            comment_groups[comment] = []
                        comment_groups[comment].append(tx)

                    # Проверяем дубли по ТОЧНОМУ совпадению комментария (case-insensitive)
                    # Не используем substring — "Мадира" и "Мадира Т" это разные транзакции
                    config_comments = [(cfg.get('comment', ''), cfg) for cfg in account_configs if cfg.get('comment')]

                    for config_comment, cfg in config_comments:
                        # Найти ВСЕ транзакции, которые матчат этот конфиг (exact match)
                        matching_txs = []
                        config_lower = config_comment.lower().strip()
                        for tx in transactions:
                            tx_type = str(tx.get('type', ''))
                            tx_comment = (tx.get('comment') or '').strip()
                            if not tx_comment or tx_type == '2':
                                continue
                            # Exact match (case-insensitive)
                            if tx_comment.lower().strip() == config_lower:
                                matching_txs.append(tx)

                        if len(matching_txs) <= 1:
                            continue

                        # Есть дубли! Оставить ту, которая совпадает по сумме с конфигом
                        config_amount = cfg.get('amount', 1) * 100  # в тийинах
                        logger.warning(
                            f"⚠️ Найдено {len(matching_txs)} дублей для '{config_comment}' "
                            f"(ожидаемая сумма: {config_amount} тийинов)"
                        )

                        # Разделить на "правильные" и "неправильные"
                        correct = []
                        incorrect = []
                        for tx in matching_txs:
                            tx_amount = abs(int(tx.get('amount_from', 0) or tx.get('amount', 0)))
                            tx_comment = (tx.get('comment') or '').strip()
                            # Правильная = точный комментарий И точная сумма
                            if tx_comment == config_comment and tx_amount == config_amount:
                                correct.append(tx)
                            else:
                                incorrect.append(tx)

                        # Если нет "правильных", оставляем все (не трогаем)
                        if not correct:
                            logger.warning(
                                f"  → Нет транзакций с точным совпадением, пропускаю очистку"
                            )
                            continue

                        # Удалить "неправильные" (от старого кода)
                        for tx in incorrect:
                            tx_id = tx.get('transaction_id')
                            tx_comment = (tx.get('comment') or '').strip()
                            tx_amount = abs(int(tx.get('amount_from', 0) or tx.get('amount', 0)))
                            logger.info(
                                f"  🗑️ Удаляю дубль: ID={tx_id}, comment='{tx_comment}', "
                                f"amount={tx_amount} (ожидалось {config_amount})"
                            )
                            removed = await poster_client.remove_finance_transaction(int(tx_id))
                            if removed:
                                total_cleaned += 1
                            else:
                                # Fallback: обновить комментарий чтобы пометить как дубль
                                logger.warning(f"  → Не удалось удалить {tx_id}, помечаю как дубль")
                                await poster_client.update_transaction(
                                    transaction_id=int(tx_id),
                                    amount=0,
                                    comment=f"[ДУБЛЬ] {tx_comment}"
                                )
                                total_cleaned += 1

                        # Если правильных больше 1, оставить одну, удалить остальные
                        if len(correct) > 1:
                            for tx in correct[1:]:
                                tx_id = tx.get('transaction_id')
                                logger.info(f"  🗑️ Удаляю лишний дубль: ID={tx_id}")
                                removed = await poster_client.remove_finance_transaction(int(tx_id))
                                if removed:
                                    total_cleaned += 1

                finally:
                    await poster_client.close()

            if total_cleaned > 0:
                logger.info(f"✅ Очистка дублей: удалено {total_cleaned} транзакций для пользователя {self.telegram_user_id}")
            else:
                logger.info(f"✅ Дублей не найдено для пользователя {self.telegram_user_id}")

            return {'cleaned': total_cleaned}

        except Exception as e:
            logger.error(f"❌ Ошибка очистки дублей: {e}", exc_info=True)
            return {'cleaned': 0, 'error': str(e)}

    async def cleanup_no_comment_duplicates(self):
        """
        Удалить дубли для транзакций БЕЗ комментариев (например КухРабочая).
        Проверяет по category_id: если за день больше 1 транзакции с одной категорией
        и пустым комментарием — удаляет лишние.
        """
        try:
            from database import get_database
            db = get_database()
            today = datetime.now(KZ_TZ).strftime("%Y-%m-%d")

            if not db.is_daily_transactions_created(self.telegram_user_id, today):
                return {'cleaned': 0}

            accounts = db.get_accounts(self.telegram_user_id)
            tx_configs = db.get_daily_transaction_configs(self.telegram_user_id)
            enabled_configs = [c for c in tx_configs if c.get('is_enabled')]

            # Конфиги без комментариев
            no_comment_configs = [c for c in enabled_configs if not c.get('comment')]
            if not no_comment_configs:
                return {'cleaned': 0}

            configs_by_account = {}
            for cfg in no_comment_configs:
                acc_name = cfg.get('account_name', 'Pizzburg')
                if acc_name not in configs_by_account:
                    configs_by_account[acc_name] = []
                configs_by_account[acc_name].append(cfg)

            total_cleaned = 0

            for account in accounts:
                account_name = account['account_name']
                account_configs = configs_by_account.get(account_name, [])
                if not account_configs:
                    continue

                poster_client = PosterClient(
                    telegram_user_id=self.telegram_user_id,
                    poster_token=account['poster_token'],
                    poster_user_id=account['poster_user_id'],
                    poster_base_url=account['poster_base_url']
                )

                try:
                    today_fmt = datetime.now(KZ_TZ).strftime("%Y%m%d")
                    transactions = await poster_client.get_transactions(today_fmt, today_fmt)

                    for cfg in account_configs:
                        cat_id = str(cfg.get('category_id', 0))
                        if cat_id == '0':
                            continue

                        # Найти транзакции с этой категорией и пустым комментарием
                        matching = [
                            tx for tx in transactions
                            if str(tx.get('category', '') or tx.get('finance_category_id', '')) == cat_id
                            and not (tx.get('comment') or '').strip()
                            and str(tx.get('type', '')) in ('0', '1')
                        ]

                        if len(matching) <= 1:
                            continue

                        logger.warning(f"⚠️ Найдено {len(matching)} дублей для category_id={cat_id} без комментария")

                        # Оставить одну, удалить остальные
                        for tx in matching[1:]:
                            tx_id = tx.get('transaction_id')
                            logger.info(f"  🗑️ Удаляю дубль без комментария: ID={tx_id}, cat={cat_id}")
                            removed = await poster_client.remove_finance_transaction(int(tx_id))
                            if removed:
                                total_cleaned += 1

                finally:
                    await poster_client.close()

            return {'cleaned': total_cleaned}

        except Exception as e:
            logger.error(f"❌ Ошибка очистки дублей без комментария: {e}", exc_info=True)
            return {'cleaned': 0}


# Конфигурация для пользователей
# Ключ: telegram_user_id, значение: включены ли авто-транзакции
# ВАЖНО: если оба пользователя привязаны к одному Poster аккаунту,
# включать нужно только ОДНОГО — иначе будут дубли транзакций
DAILY_TRANSACTIONS_ENABLED = {
    167084307: True,   # Основной аккаунт — создаёт ежедневные транзакции
    8010984368: False,  # Второй аккаунт — отключен (тот же Poster, дубли)
}


def is_daily_transactions_enabled(telegram_user_id: int) -> bool:
    """Проверить, включены ли авто-транзакции для пользователя"""
    return DAILY_TRANSACTIONS_ENABLED.get(telegram_user_id, False)
