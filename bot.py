"""Main Telegram Bot module for Poster Helper"""
import logging
import asyncio
from pathlib import Path
from datetime import datetime
from typing import Dict
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)

# Local imports
from config import (
    TELEGRAM_BOT_TOKEN, ALLOWED_USER_IDS, ADMIN_USER_IDS, TIMEZONE,
    DEFAULT_ACCOUNT_FROM_ID, CURRENCY, validate_config, DATA_DIR, WEBAPP_URL,
    USE_WEBHOOK, WEBHOOK_URL, WEBHOOK_PATH, LOG_LEVEL, MIN_MATCH_CONFIDENCE
)
from database import get_database
from poster_client import get_poster_client
from parser_service import get_parser_service
from simple_parser import get_simple_parser
from matchers import get_category_matcher, get_account_matcher, get_supplier_matcher, get_ingredient_matcher, get_product_matcher
from daily_transactions import DailyTransactionScheduler, is_daily_transactions_enabled
from alias_generator import AliasGenerator
from sync_ingredients import sync_ingredients
from sync_products import sync_products
import salary_flow_handlers
from shipment_templates import (
    templates_command,
    edit_template_command,
    delete_template_command,
    try_parse_quick_template,
    create_shipment_from_template,
    save_draft_as_template,
    handle_template_name_input,
    handle_edit_template_callback,
    handle_delete_template_callback,
    handle_confirm_delete_template_callback,
    handle_edit_template_prices_callback,
    handle_template_price_update
)
import re

# APScheduler для автоматических задач
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=getattr(logging, LOG_LEVEL, logging.INFO)
)
logger = logging.getLogger(__name__)


# === Helper Functions ===

def get_main_menu_keyboard():
    """Главное меню - ReplyKeyboard (сетка 2x3)"""
    keyboard = [
        [KeyboardButton("📥 Расходы"), KeyboardButton("📦 Поставки"), KeyboardButton("💰 Зарплаты")],
        [KeyboardButton("🔄 Сверка счетов"), KeyboardButton("⚙️ Ещё")]
    ]
    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True,
        one_time_keyboard=False
    )


def get_more_menu_keyboard():
    """Подменю 'Ещё' - ReplyKeyboard"""
    keyboard = [
        [KeyboardButton("🏪 Закрыть кассу")],
        [KeyboardButton("📊 Отчёт недели"), KeyboardButton("📈 Отчёт месяца")],
        [KeyboardButton("📝 Транзакции"), KeyboardButton("← Назад")]
    ]
    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True,
        one_time_keyboard=False
    )


def fix_user_poster_urls():
    """
    Автоматическое исправление poster_base_url для всех пользователей при старте бота.
    Обновляет пользователей с неправильным URL на правильный из конфига.
    """
    try:
        from config import POSTER_BASE_URL
        from database import DB_TYPE
        db = get_database()

        # Получаем всех пользователей
        conn = db._get_connection()

        # Используем правильный cursor для каждого типа БД
        if DB_TYPE == "sqlite":
            cursor = conn.cursor()
        else:
            # PostgreSQL - используем RealDictCursor
            from psycopg2.extras import RealDictCursor
            cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute("SELECT telegram_user_id, poster_base_url FROM users")
        users = cursor.fetchall()

        conn.close()

        if not users:
            logger.info("📋 Нет пользователей для проверки URL")
            return

        logger.info(f"🔍 Проверка poster_base_url для {len(users)} пользователей...")
        logger.info(f"   Правильный URL: {POSTER_BASE_URL}")

        fixed_count = 0
        for user in users:
            # Для PostgreSQL RealDictCursor возвращает dict, для SQLite - Row
            if DB_TYPE == "sqlite":
                telegram_user_id = user[0]
                current_url = user[1]
            else:
                telegram_user_id = user['telegram_user_id']
                current_url = user['poster_base_url']

            # Проверяем нужно ли обновление
            if current_url != POSTER_BASE_URL:
                logger.info(f"   🔧 Исправляю пользователя {telegram_user_id}: {current_url} → {POSTER_BASE_URL}")

                success = db.update_user(
                    telegram_user_id=telegram_user_id,
                    poster_base_url=POSTER_BASE_URL
                )

                if success:
                    fixed_count += 1

        if fixed_count > 0:
            logger.info(f"✅ Обновлено poster_base_url для {fixed_count}/{len(users)} пользователей")
        else:
            logger.info(f"✅ Все пользователи имеют правильный poster_base_url")

    except Exception as e:
        logger.error(f"❌ Ошибка при исправлении poster_base_url: {e}", exc_info=True)


def sync_ingredients_if_needed():
    """
    Синхронизация ингредиентов из Poster API если CSV файл отсутствует.
    Нужно для Railway, где файловая система эфемерная.
    """
    try:
        ingredients_csv = DATA_DIR / "poster_ingredients.csv"

        logger.info(f"🔍 Проверка ингредиентов...")
        logger.info(f"   DATA_DIR: {DATA_DIR}")
        logger.info(f"   CSV path: {ingredients_csv}")
        logger.info(f"   File exists: {ingredients_csv.exists()}")

        if ingredients_csv.exists():
            # Посчитать строки
            with open(ingredients_csv, 'r') as f:
                line_count = sum(1 for _ in f) - 1  # -1 for header
            logger.info(f"✅ Ингредиенты уже загружены ({line_count} штук)")
            return

        logger.info("🔄 Синхронизация ингредиентов из Poster API...")

        # Запускаем async функцию sync_ingredients
        asyncio.run(sync_ingredients())

        # Проверяем что файл создан
        if ingredients_csv.exists():
            with open(ingredients_csv, 'r') as f:
                line_count = sum(1 for _ in f) - 1
            logger.info(f"✅ Ингредиенты успешно синхронизированы ({line_count} штук)")
        else:
            logger.error(f"❌ CSV файл не был создан после синхронизации!")

    except Exception as e:
        logger.error(f"❌ Ошибка при синхронизации ингредиентов: {e}", exc_info=True)
        logger.warning("⚠️ Бот продолжит работу без ингредиентов (alias matching не будет работать)")


def sync_products_if_needed():
    """
    Синхронизация products из Poster API если CSV файл отсутствует.
    Нужно для Railway, где файловая система эфемерная.
    """
    try:
        products_csv = DATA_DIR / "poster_products.csv"

        logger.info(f"🔍 Проверка products...")
        logger.info(f"   CSV path: {products_csv}")
        logger.info(f"   File exists: {products_csv.exists()}")

        if products_csv.exists():
            # Посчитать строки
            with open(products_csv, 'r') as f:
                line_count = sum(1 for _ in f) - 1  # -1 for header
            logger.info(f"✅ Products уже загружены ({line_count} штук)")
            return

        logger.info("🔄 Синхронизация products из Poster API...")

        # Запускаем async функцию sync_products
        asyncio.run(sync_products())

        # Проверяем что файл создан
        if products_csv.exists():
            with open(products_csv, 'r') as f:
                line_count = sum(1 for _ in f) - 1
            logger.info(f"✅ Products успешно синхронизированы ({line_count} штук)")
        else:
            logger.error(f"❌ CSV файл не был создан после синхронизации!")

    except Exception as e:
        logger.error(f"❌ Ошибка при синхронизации products: {e}", exc_info=True)
        logger.warning("⚠️ Бот продолжит работу без products (product alias matching не будет работать)")


def migrate_csv_aliases_to_db():
    """
    Автоматическая миграция алиасов из CSV в PostgreSQL при первом запуске.
    Проверяет каждого пользователя и импортирует алиасы если их нет в БД.
    """
    try:
        import csv
        from config import DATA_DIR
        from database import DB_TYPE

        db = get_database()
        users_dir = DATA_DIR / "users"

        if not users_dir.exists():
            return

        logger.info("=" * 70)
        logger.info("🔄 ПРОВЕРКА МИГРАЦИИ АЛИАСОВ ИЗ CSV В БД...")
        logger.info("=" * 70)

        # Получаем всех пользователей из БД
        conn = db._get_connection()

        if DB_TYPE == "sqlite":
            cursor = conn.cursor()
        else:
            from psycopg2.extras import RealDictCursor
            cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute("SELECT telegram_user_id FROM users")
        db_users = cursor.fetchall()
        conn.close()

        logger.info(f"📊 Найдено пользователей в БД: {len(db_users)}")

        total_imported = 0

        for user_row in db_users:
            telegram_user_id = user_row[0] if DB_TYPE == "sqlite" else user_row['telegram_user_id']

            logger.info(f"🔍 Проверка пользователя {telegram_user_id}...")

            # Проверяем, есть ли уже алиасы в БД для этого пользователя
            existing_aliases = db.get_ingredient_aliases(telegram_user_id)
            logger.info(f"   → Найдено алиасов в БД: {len(existing_aliases)}")

            # Если алиасов достаточно (>100) - пропускаем импорт
            if len(existing_aliases) > 100:
                logger.info(f"   ✓ User {telegram_user_id}: {len(existing_aliases)} aliases already in DB - SKIP")
                continue

            # Алиасов нет или мало - пробуем импортировать из CSV
            csv_path = users_dir / str(telegram_user_id) / "alias_item_mapping.csv"
            logger.info(f"   → CSV путь: {csv_path}, exists={csv_path.exists()}")

            if not csv_path.exists():
                logger.info(f"   → CSV not found for user {telegram_user_id} - skipping")
                continue

            # Читаем CSV
            aliases_to_import = []
            with open(csv_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get('source', '').strip().lower() != 'ingredient':
                        continue

                    aliases_to_import.append({
                        'alias_text': row['alias_text'].strip(),
                        'poster_item_id': int(row['poster_item_id']),
                        'poster_item_name': row['poster_item_name'].strip(),
                        'source': row.get('source', 'ingredient').strip(),
                        'notes': row.get('notes', '').strip()
                    })

            if aliases_to_import:
                count = db.bulk_add_aliases(telegram_user_id, aliases_to_import)
                logger.info(f"   ✓ User {telegram_user_id}: Imported {count} aliases from CSV")
                total_imported += count

        if total_imported > 0:
            logger.info(f"✅ Миграция завершена: {total_imported} алиасов импортировано в БД")
        else:
            logger.info("   ✓ All aliases already in database")

    except Exception as e:
        logger.error(f"❌ Ошибка при миграции алиасов: {e}", exc_info=True)


def extract_packing_size(item_name: str) -> int:
    """
    Extract packing size from canonical item name in Poster.

    Examples:
        "Булочка кунжут 11,4 (30шт)" -> 30
        "Тортилья сырная (12шт)" -> 12
        "Сырные палочки 1кг" -> 1 (no packing)

    Returns:
        Packing size or 1 if no packing info found
    """
    # Look for patterns like (30шт), (12шт), etc.
    match = re.search(r'\((\d+)шт\)', item_name)
    if match:
        return int(match.group(1))
    return 1


def adjust_for_packing(item_name: str, qty: float, price: float, original_name: str) -> tuple:
    """
    Adjust quantity and price if item is sold in packages.

    If canonical name has packing info (e.g., "(30шт)") and original qty looks like
    number of packages (small integer like 10), then:
    - qty = qty * packing_size
    - price = price / packing_size

    Args:
        item_name: Canonical name from Poster (e.g., "Булочка кунжут 11,4 (30шт)")
        qty: Quantity from invoice
        price: Price from invoice
        original_name: Original name from invoice

    Returns:
        (adjusted_qty, adjusted_price, packing_size)
    """
    packing_size = extract_packing_size(item_name)

    # If no packing info, return as is
    if packing_size == 1:
        return (qty, price, 1)

    # Check if qty looks like number of packages (< 100 and is integer)
    # This heuristic helps determine if invoice lists packages or individual items
    if qty < 100 and qty == int(qty):
        # Looks like packages - convert to items
        adjusted_qty = qty * packing_size
        adjusted_price = price / packing_size
        logger.info(f"Adjusted packing for '{item_name}': {qty} упак × {packing_size}шт = {adjusted_qty}шт, {price}₸/упак → {adjusted_price:.2f}₸/шт")
        return (adjusted_qty, adjusted_price, packing_size)

    # Otherwise, assume it's already in items
    return (qty, price, 1)


# === Authorization Decorator ===

def authorized_only(func):
    """Decorator to check if user has active subscription"""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        from database import get_database

        # Check if update has message
        if not update.message:
            logger.error(f"authorized_only: update.message is None in {func.__name__}")
            return

        user_id = update.effective_user.id
        db = get_database()

        # Check if user exists in database
        user_data = db.get_user(user_id)

        if not user_data:
            # User not registered - ask them to use /start
            logger.warning(f"Unregistered user attempt by user_id={user_id} in {func.__name__}")
            try:
                await update.message.reply_text(
                    f"👋 Привет!\n\n"
                    f"Вы еще не зарегистрированы.\n"
                    f"Отправьте команду /start для регистрации и получения 14-дневного триала!"
                )
            except Exception as e:
                logger.error(f"Failed to send unregistered message: {e}")
            return

        # Admins and allowed users bypass subscription check
        if user_id in ADMIN_USER_IDS or user_id in ALLOWED_USER_IDS:
            logger.info(f"Allowed user {user_id} bypassing subscription check in {func.__name__}")
            return await func(update, context)

        # Check if subscription is active (only for users not in allowed lists)
        if not db.is_subscription_active(user_id):
            # Subscription expired
            logger.warning(f"Expired subscription attempt by user_id={user_id} in {func.__name__}")
            try:
                await update.message.reply_text(
                    f"⛔ Ваша подписка истекла.\n\n"
                    f"Для продолжения работы необходимо продлить подписку.\n"
                    f"Используйте /subscription для подробностей."
                )
            except Exception as e:
                logger.error(f"Failed to send expired subscription message: {e}")
            return

        return await func(update, context)

    return wrapper


def admin_only(func):
    """Decorator to check if user is admin"""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id

        if user_id not in ADMIN_USER_IDS:
            logger.warning(f"Non-admin attempt to use admin command by user_id={user_id}")
            await update.message.reply_text(
                "⛔ Эта команда доступна только администраторам."
            )
            return

        return await func(update, context)

    return wrapper


# === Admin Notifications ===

async def notify_admin(context: ContextTypes.DEFAULT_TYPE, message: str):
    """Send notification to admin users"""
    if not ADMIN_USER_IDS:
        return

    for admin_id in ADMIN_USER_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=f"🔔 Уведомление администратора\n\n{message}",
                parse_mode=None
            )
            logger.info(f"Admin notification sent to {admin_id}")
        except Exception as e:
            logger.error(f"Failed to send admin notification to {admin_id}: {e}")


# === Command Handlers ===

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command with onboarding for new users"""
    from database import get_database
    from telegram import MenuButtonWebApp, WebAppInfo

    user = update.effective_user
    telegram_user_id = user.id
    chat_id = update.effective_chat.id
    db = get_database()

    # Установить MenuButton WebApp для этого чата
    try:
        await context.bot.set_chat_menu_button(
            chat_id=chat_id,
            menu_button=MenuButtonWebApp(
                text="📱 App",
                web_app=WebAppInfo(url=WEBAPP_URL)
            )
        )
        logger.info(f"✅ MenuButton WebApp установлен для чата {chat_id}")
    except Exception as e:
        logger.warning(f"Не удалось установить MenuButton: {e}")

    # Check if user exists
    user_data = db.get_user(telegram_user_id)

    if user_data:
        # Existing user - show welcome back message
        await update.message.reply_text(
            f"👋 С возвращением, {user.first_name}!\n\n"
            f"Я помогу создавать транзакции и поставки в Poster.\n\n"
            f"📝 Просто отправьте:\n"
            f"   • Голосовое сообщение для транзакций\n"
            f"   • Фото накладной для поставок\n\n"
            f"Используйте кнопки меню внизу экрана 👇",
            reply_markup=get_main_menu_keyboard()
        )
    else:
        # New user - start onboarding
        # Notify admin about new user
        await notify_admin(
            context,
            f"👤 Новый пользователь начал регистрацию:\n\n"
            f"Имя: {user.first_name} {user.last_name or ''}\n"
            f"Username: @{user.username or 'нет'}\n"
            f"Telegram ID: {telegram_user_id}"
        )

        await update.message.reply_text(
            f"👋 Привет, {user.first_name}!\n\n"
            f"🤖 Я бот для автоматизации работы с Poster POS.\n\n"
            f"✨ Что я умею:\n"
            f"   💸 Создавать транзакции из голосовых сообщений\n"
            f"   📦 Создавать поставки из фото накладных\n"
            f"   🎯 Автоматически запоминать ваши алиасы\n\n"
            f"⚡️ Триал: 14 дней бесплатно\n\n"
            f"Для начала подключим ваш Poster аккаунт →",
            reply_markup=ReplyKeyboardRemove()
        )

        await update.message.reply_text(
            f"📍 Шаг 1/2: API Токен\n\n"
            f"Как получить токен:\n\n"
            f"1️⃣ Откройте Poster в браузере\n"
            f"   https://joinposter.com\n\n"
            f"2️⃣ Войдите в свой аккаунт\n\n"
            f"3️⃣ Перейдите:\n"
            f"   Доступ → Интеграция → Личная интеграция\n"
            f"   (Access → Integration → Personal Integration)\n\n"
            f"4️⃣ Найдите поле \"API токен\" или \"Access Token\"\n\n"
            f"5️⃣ Скопируйте весь токен полностью\n"
            f"   (обычно это строка вида: 881862:abc123def456...)\n\n"
            f"📨 Отправьте мне скопированный токен следующим сообщением\n\n"
            f"ℹ️ Токен нужен для безопасного доступа к вашему Poster через API",
            reply_markup=ReplyKeyboardRemove()
        )

        # Set state: waiting for token
        context.user_data['onboarding_step'] = 'waiting_token'


@authorized_only
async def myid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /myid command - show user's telegram ID"""
    user_id = update.effective_user.id
    username = update.effective_user.username or "нет username"
    first_name = update.effective_user.first_name or ""

    await update.message.reply_text(
        f"👤 **Ваши данные:**\n\n"
        f"🆔 Telegram ID: `{user_id}`\n"
        f"👤 Имя: {first_name}\n"
        f"📝 Username: @{username}\n\n"
        f"Скопируйте ID выше и отправьте администратору для добавления в систему."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command"""
    await update.message.reply_text(
        "📚 **Как использовать бот:**\n\n"
        "🎤 **Отправьте голосовое или текст:**\n\n"
        "💸 **Расходы:**\n"
        '  "Донерщик 7500 Максат"\n'
        '  "Аренда 50000 со счёта Каспи за октябрь"\n'
        '  "Логистика 3000 комментарий доставка"\n\n'
        "🔄 **Переводы:**\n"
        '  "Перевод 50000 с Касипай в Кассу"\n\n'
        "📦 **Поставки:**\n"
        '  "Поставщик Метро. Айсберг 2.2 кг по 1600"\n\n'
        "⚡ **Быстрые поставки (шаблоны):**\n"
        "  Для часто повторяющихся поставок:\n"
        '  "Лаваш 400" - создаст поставку по шаблону\n'
        "  /templates - Просмотр шаблонов\n"
        "  /edit_template - Изменить цены в шаблоне\n"
        "  /delete_template - Удалить шаблон\n\n"
        "📁 **Основные категории:**\n"
        "  Зарплата: донерщик, повара, кассиры, курьер\n"
        "  Расходы: логистика, аренда, коммуналка\n"
        "  Другое: маркетинг, упаковки, мыломойка\n\n"
        "💰 **Счета:** каспи, касса, закуп, wolt, форте\n\n"
        "Бот покажет черновик для проверки перед созданием!\n\n"
        "⚙️ **Команды:**\n"
        "  /settings - Настройки аккаунта\n"
        "  /subscription - Информация о подписке\n"
        "  /sync - Обновить справочники\n"
        "  /cancel - Отменить текущее действие",
        parse_mode="Markdown"
    )


@authorized_only
async def sync_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /sync command - reload references"""
    await update.message.reply_text("🔄 Обновляю справочники...")

    try:
        telegram_user_id = update.effective_user.id

        # Reload matchers
        category_matcher = get_category_matcher(telegram_user_id)
        account_matcher = get_account_matcher(telegram_user_id)

        category_matcher.load_aliases()
        account_matcher.load_accounts()

        await update.message.reply_text(
            f"✅ Справочники обновлены:\n"
            f"   Алиасы категорий: {len(category_matcher.aliases)}\n"
            f"   Счета: {len(account_matcher.accounts)}"
        )

    except Exception as e:
        logger.error(f"Sync failed: {e}")
        await update.message.reply_text(f"❌ Ошибка обновления: {e}")


@authorized_only
async def force_sync_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Принудительная синхронизация всех данных из Poster"""
    telegram_user_id = update.effective_user.id

    # Check if user has poster accounts
    db = get_database()
    accounts = db.get_accounts(telegram_user_id)

    if not accounts:
        await update.message.reply_text(
            "❌ У вас нет подключенных Poster аккаунтов.\n\n"
            "Используйте /start для регистрации."
        )
        return

    await update.message.reply_text(
        f"🔄 Запускаю синхронизацию из {len(accounts)} аккаунта(ов)...\n\n"
        + "\n".join([f"  • {acc['account_name']}" for acc in accounts])
    )

    try:
        # Запустить синхронизацию для этого пользователя
        await auto_sync_poster_data(context, telegram_user_id=telegram_user_id)

        await update.message.reply_text(
            "✅ Синхронизация завершена!\n\n"
            "Все данные обновлены из Poster API."
        )

    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка синхронизации:\n{str(e)}")
        logger.error(f"Force sync failed: {e}", exc_info=True)


@authorized_only
async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /cancel command"""
    context.user_data.clear()
    await update.message.reply_text("✖️ Действие отменено.")


@admin_only
async def test_daily_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /test_daily command - ручной запуск ежедневных транзакций (только для админа)"""
    telegram_user_id = update.effective_user.id

    if not is_daily_transactions_enabled(telegram_user_id):
        await update.message.reply_text(
            "❌ Автоматические транзакции не включены для вашего аккаунта."
        )
        return

    await update.message.reply_text("⏳ Создаю ежедневные транзакции...")

    try:
        # Запустить создание транзакций
        await run_daily_transactions_for_user(telegram_user_id)

        await update.message.reply_text(
            "✅ Ежедневные транзакции созданы!\n\n"
            "Проверьте Poster для подтверждения."
        )

    except Exception as e:
        logger.error(f"Test daily transactions failed: {e}", exc_info=True)
        await update.message.reply_text(
            f"❌ Ошибка создания транзакций:\n{str(e)[:300]}"
        )


@admin_only
async def cleanup_daily_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /cleanup_daily command - ручная очистка дублей ежедневных транзакций"""
    telegram_user_id = update.effective_user.id

    if not is_daily_transactions_enabled(telegram_user_id):
        await update.message.reply_text(
            "❌ Автоматические транзакции не включены для вашего аккаунта."
        )
        return

    await update.message.reply_text("🧹 Проверяю дубли ежедневных транзакций...")

    try:
        await run_daily_transactions_cleanup(telegram_user_id)

        await update.message.reply_text(
            "✅ Проверка дублей завершена!\n\n"
            "Подробности в логах."
        )

    except Exception as e:
        logger.error(f"Cleanup daily command failed: {e}", exc_info=True)
        await update.message.reply_text(
            f"❌ Ошибка очистки:\n{str(e)[:300]}"
        )


@admin_only
async def check_ids_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /check_ids command - показать ID счетов и категорий для всех аккаунтов"""
    telegram_user_id = update.effective_user.id

    await update.message.reply_text("🔍 Получаю ID счетов и категорий...")

    try:
        from database import get_database
        from poster_client import PosterClient
        import json

        db = get_database()
        accounts = db.get_accounts(telegram_user_id)

        if not accounts:
            await update.message.reply_text("❌ Аккаунты не найдены!")
            return

        for account in accounts:
            account_name = account['account_name']

            # Создать клиент для этого аккаунта
            client = PosterClient(
                telegram_user_id=telegram_user_id,
                poster_token=account['poster_token'],
                poster_user_id=account['poster_user_id'],
                poster_base_url=account['poster_base_url']
            )

            try:
                # Получить счета
                accounts_list = await client.get_accounts()

                # Получить категории
                categories_list = await client.get_categories()

                # Форматировать сообщение с правильными ключами (БЕЗ Markdown)
                message = f"📊 {account_name}\n\n"
                message += "Счета:\n"
                for acc in accounts_list:
                    acc_id = acc.get('account_id')
                    acc_name = acc.get('name', 'Unknown')
                    message += f"  • ID={acc_id} - {acc_name}\n"

                message += "\nВсе категории (по ID):\n"
                message += "Легенда: operations 1=доход, 2=расход, 3=оба\n\n"

                # Сортируем по category_id
                sorted_categories = sorted(categories_list, key=lambda x: int(x.get('category_id', 0)))

                for cat in sorted_categories:
                    cat_id = cat.get('category_id')
                    cat_name = cat.get('name', 'Unknown')
                    parent_id = cat.get('parent_id', '0')
                    operations = cat.get('operations', '?')

                    # Определяем тип операции
                    op_label = {'1': '💰доход', '2': '💸расход', '3': '💱оба'}.get(operations, f'?{operations}')

                    # Показываем с отступом если это подкатегория
                    if parent_id != '0':
                        message += f"  ├─ ID={cat_id} - {cat_name} [{op_label}, parent={parent_id}]\n"
                    else:
                        message += f"📂 ID={cat_id} - {cat_name} [{op_label}]\n"

                # Отправляем БЕЗ parse_mode чтобы избежать ошибок Markdown
                await update.message.reply_text(message)

            finally:
                await client.close()

    except Exception as e:
        logger.error(f"Check IDs failed: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Ошибка: {str(e)[:300]}")


@admin_only
async def test_report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /test_report command - ручная генерация еженедельного отчёта (только для админа)"""
    telegram_user_id = update.effective_user.id

    await update.message.reply_text("⏳ Генерирую еженедельный отчёт...")

    try:
        from weekly_report import WeeklyReportGenerator

        generator = WeeklyReportGenerator(telegram_user_id)
        result = await generator.generate_weekly_report()

        if result['success']:
            await update.message.reply_text(
                result['report_text'],
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text(
                f"❌ Ошибка генерации отчёта:\n{result.get('error', 'Неизвестная ошибка')}"
            )

    except Exception as e:
        logger.error(f"Test report failed: {e}", exc_info=True)
        await update.message.reply_text(
            f"❌ Ошибка генерации отчёта:\n{str(e)[:300]}"
        )


@admin_only
async def test_monthly_report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /test_monthly command - ручная генерация ежемесячного отчёта (только для админа)"""
    telegram_user_id = update.effective_user.id

    await update.message.reply_text("⏳ Генерирую ежемесячный отчёт...")

    try:
        from monthly_report import MonthlyReportGenerator

        generator = MonthlyReportGenerator(telegram_user_id)
        result = await generator.generate_monthly_report()

        if result['success']:
            await update.message.reply_text(
                result['report_text'],
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text(
                f"❌ Ошибка генерации месячного отчёта:\n{result.get('error', 'Неизвестная ошибка')}"
            )

    except Exception as e:
        logger.error(f"Test monthly report failed: {e}", exc_info=True)
        await update.message.reply_text(
            f"❌ Ошибка генерации месячного отчёта:\n{str(e)[:300]}"
        )


@admin_only
async def check_doner_sales_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /check_doner_sales [YYYYMMDD] - проверка продаж донеров за дату"""
    telegram_user_id = update.effective_user.id

    # Получить дату из аргументов или использовать вчерашний день
    from datetime import datetime, timedelta

    if context.args and len(context.args) > 0:
        date_str = context.args[0]
    else:
        # По умолчанию - вчера
        yesterday = datetime.now() - timedelta(days=1)
        date_str = yesterday.strftime("%Y%m%d")

    await update.message.reply_text(f"⏳ Получаю данные о продажах донеров за {date_str}...")

    try:
        from doner_salary import DonerSalaryCalculator

        calculator = DonerSalaryCalculator(telegram_user_id)
        sales = await calculator.get_doner_sales_count(date_str)

        # Форматируем красивый отчёт
        message = "📊 <b>ПРОДАЖИ ДОНЕРОВ</b>\n"
        message += f"📅 Дата: {date_str}\n"
        message += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        message += f"📦 Категория \"Донер\" (ID=6): <b>{sales['category_count']:.0f}</b> шт\n"
        message += f"🎁 Комбо Донер: <b>{sales['combo_count']:.0f}</b> шт\n"
        message += f"🍕 Донерная пицца: <b>{sales['pizza_count']:.0f}</b> шт\n"
        message += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        message += f"📊 <b>ВСЕГО для зарплаты: {sales['total_count']:.0f} шт</b>\n\n"

        # Рассчитать зарплату
        salary = calculator.calculate_salary(int(sales['total_count']))
        message += f"💰 Зарплата донерщика: <b>{salary:,}₸</b>\n\n"

        # Детали по товарам
        if sales['details']:
            message += "📝 <b>Детали по товарам:</b>\n\n"

            # Группировка
            category_items = [x for x in sales['details'] if x['source'] == 'category']
            combo_items = [x for x in sales['details'] if x['source'] == 'combo']
            pizza_items = [x for x in sales['details'] if x['source'] == 'pizza']

            if category_items:
                message += "<i>Категория \"Донер\":</i>\n"
                for item in sorted(category_items, key=lambda x: x['count'], reverse=True):
                    message += f"  • {item['name']}: {item['count']:.0f} шт\n"
                message += "\n"

            if combo_items:
                message += "<i>Комбо:</i>\n"
                for item in combo_items:
                    message += f"  • {item['name']}: {item['count']:.0f} шт\n"
                message += "\n"

            if pizza_items:
                message += "<i>Донерная пицца:</i>\n"
                for item in pizza_items:
                    message += f"  • {item['name']}: {item['count']:.0f} шт\n"

        await update.message.reply_text(message, parse_mode='HTML')

    except Exception as e:
        logger.error(f"Check doner sales failed: {e}", exc_info=True)
        await update.message.reply_text(
            f"❌ Ошибка получения данных:\n{str(e)[:300]}"
        )


@admin_only
async def price_check_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /price_check command - ручная проверка трендов цен"""
    telegram_user_id = update.effective_user.id

    await update.message.reply_text("⏳ Анализирую изменения цен за последние 6 месяцев...")

    try:
        from price_monitoring import PriceMonitor, format_price_alert_message

        monitor = PriceMonitor(telegram_user_id)

        # Step 1: ABC analysis
        abc_groups, abc_results = await monitor.calculate_abc_analysis(period_months=3)
        category_a_ids = abc_groups['A']

        if not category_a_ids:
            await update.message.reply_text(
                "ℹ️ Недостаточно данных для анализа.\n\n"
                "Для работы системы мониторинга цен необходимо:\n"
                "1. Создать хотя бы несколько поставок\n"
                "2. Подождать накопления истории цен за несколько месяцев\n\n"
                "Система автоматически начнёт анализ, когда данных будет достаточно."
            )
            return

        await update.message.reply_text(
            f"📊 ABC-анализ завершён\n"
            f"Категория A (ключевые): {len(abc_groups['A'])} ингредиентов\n"
            f"Категория B: {len(abc_groups['B'])} ингредиентов\n"
            f"Категория C: {len(abc_groups['C'])} ингредиентов\n\n"
            f"Проверяю тренды цен..."
        )

        # Step 2: Analyze price trends (6 months, 30% threshold)
        alerts = await monitor.analyze_price_trends(
            ingredient_ids=category_a_ids,
            months=6,
            threshold=30.0
        )

        if not alerts:
            await update.message.reply_text(
                "✅ Проверка завершена!\n\n"
                f"Проверено: {len(category_a_ids)} ключевых ингредиентов (категория A)\n"
                "Значительных изменений цен (≥30%) не обнаружено.\n\n"
                "🔔 Автоматическая проверка выполняется каждый понедельник в 9:00"
            )
            return

        # Step 3: Format and send notification
        message = format_price_alert_message(alerts, abc_results, telegram_user_id)

        await update.message.reply_text(
            message,
            parse_mode='HTML'
        )

    except Exception as e:
        logger.error(f"Price check failed: {e}", exc_info=True)
        await update.message.reply_text(
            f"❌ Ошибка проверки цен:\n{str(e)[:300]}"
        )


@admin_only
async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /menu command - показать главное меню (ReplyKeyboard)"""
    await update.message.reply_text(
        "Выберите действие 👇",
        reply_markup=get_main_menu_keyboard()
    )


@authorized_only
async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /settings command - show user settings"""
    telegram_user_id = update.effective_user.id

    from database import get_database
    db = get_database()
    user_data = db.get_user(telegram_user_id)

    if not user_data:
        await update.message.reply_text(
            "❌ Пользователь не найден в базе.\n\n"
            "Пожалуйста, пройдите регистрацию командой /start"
        )
        return

    # Mask token for security (show only first 8 and last 4 chars)
    token = user_data['poster_token']
    masked_token = f"{token[:8]}...{token[-4:]}" if len(token) > 12 else "***"

    settings_message = (
        "⚙️ Настройки аккаунта\n\n"
        f"🔑 API Token: {masked_token}\n"
        f"👤 User ID: {user_data['poster_user_id']}\n"
        f"🌐 Poster URL: {user_data['poster_base_url']}\n"
        f"📅 Создан: {user_data['created_at'][:10]}\n"
        f"📊 Статус: {user_data['subscription_status']}\n\n"
        "Для изменения настроек свяжитесь с поддержкой."
    )

    await update.message.reply_text(settings_message)


@authorized_only
async def subscription_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /subscription command - show subscription details"""
    telegram_user_id = update.effective_user.id

    from database import get_database
    from datetime import datetime
    db = get_database()
    user_data = db.get_user(telegram_user_id)

    if not user_data:
        await update.message.reply_text(
            "❌ Пользователь не найден в базе.\n\n"
            "Пожалуйста, пройдите регистрацию командой /start"
        )
        return

    subscription_status = user_data['subscription_status']
    expires_at = user_data['subscription_expires_at']

    # Calculate days remaining
    if expires_at:
        try:
            expires_date = datetime.fromisoformat(expires_at)
            days_remaining = (expires_date - datetime.now()).days
        except:
            days_remaining = 0
    else:
        days_remaining = 0

    # Build status message
    if subscription_status == 'trial':
        status_emoji = "🆓"
        status_text = "Триал"
    elif subscription_status == 'active':
        status_emoji = "✅"
        status_text = "Активная"
    elif subscription_status == 'expired':
        status_emoji = "⛔"
        status_text = "Истёк"
    else:
        status_emoji = "❓"
        status_text = subscription_status.capitalize()

    subscription_message = (
        "💳 Подписка\n\n"
        f"{status_emoji} Статус: {status_text}\n"
    )

    if days_remaining > 0:
        subscription_message += f"⏰ Осталось дней: {days_remaining}\n"
        subscription_message += f"📅 Истекает: {expires_at[:10]}\n"
    elif subscription_status != 'active':
        subscription_message += "❌ Подписка истекла\n"

    subscription_message += "\n"

    if subscription_status == 'expired' or days_remaining <= 0:
        subscription_message += "⚠️ Продлите подписку для продолжения работы.\n"
    elif days_remaining <= 3:
        subscription_message += "⚠️ Подписка скоро истечёт. Не забудьте продлить!\n"

    await update.message.reply_text(subscription_message)


@authorized_only
async def daily_transfers_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /daily_transfers command - create daily recurring transfers"""
    try:
        telegram_user_id = update.effective_user.id
        await update.message.reply_text("⏳ Создаю ежедневные переводы...")

        poster = get_poster_client(telegram_user_id)
        account_matcher = get_account_matcher(telegram_user_id)
        date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Define daily transfers (all accounts from poster_accounts.csv)
        # ID mapping: 1=Kaspi, 2=Инкассация, 3=Касса, 4=Закуп, 5=Дома, 8=Wolt, 10=Халык
        transfers = [
            {
                'from': 'касипай',  # ID 1: Kaspi Pay
                'to': 'wolt',       # ID 8: Wolt доставка
                'amount': 1,
                'comment': 'Ежедневный перевод'
            },
            {
                'from': 'касипай',  # ID 1: Kaspi Pay
                'to': 'халык',      # ID 10: Халык банк
                'amount': 1,
                'comment': 'Ежедневный перевод'
            },
            {
                'from': 'инкассация',        # ID 2: Инкассация (вечером)
                'to': 'оставил в кассе',     # ID 4: Оставил в кассе (на закупы)
                'amount': 1,
                'comment': 'Ежедневный перевод'
            },
            {
                'from': 'оставил в кассе',   # ID 4: Оставил в кассе (на закупы)
                'to': 'деньги дома',         # ID 5: Деньги дома (отложенные)
                'amount': 1,
                'comment': 'Ежедневный перевод'
            }
        ]

        results = []
        failed = []

        for transfer in transfers:
            try:
                # Match accounts
                from_id = account_matcher.match(transfer['from'])
                to_id = account_matcher.match(transfer['to'])

                if not from_id or not to_id:
                    failed.append(f"❌ {transfer['from']} → {transfer['to']}: счета не найдены")
                    continue

                from_name = account_matcher.get_account_name(from_id)
                to_name = account_matcher.get_account_name(to_id)

                # Create transfer
                transaction_id = await poster.create_transaction(
                    transaction_type=2,  # transfer
                    category_id=None,
                    account_from_id=from_id,
                    account_to_id=to_id,
                    amount=transfer['amount'],
                    date=date,
                    comment=transfer['comment']
                )

                results.append(f"✅ {from_name} → {to_name}: {transfer['amount']} {CURRENCY}")

            except Exception as e:
                failed.append(f"❌ {transfer['from']} → {transfer['to']}: {str(e)[:50]}")
                logger.error(f"Daily transfer failed: {e}", exc_info=True)

        # Build response
        response = "📊 Результаты ежедневных переводов:\n\n"

        if results:
            response += "\n".join(results)

        if failed:
            response += "\n\n" + "\n".join(failed)

        await update.message.reply_text(response)

    except Exception as e:
        logger.error(f"Daily transfers command failed: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Ошибка создания переводов: {e}")


@admin_only
async def cafe_token_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /cafe_token command - manage cafe access tokens for isolated shift closing"""
    telegram_user_id = update.effective_user.id
    db = get_database()

    args = context.args if context.args else []

    if not args or args[0] == 'list':
        # List existing tokens
        tokens = db.list_cafe_tokens(telegram_user_id)
        if not tokens:
            await update.message.reply_text(
                "Нет токенов доступа для Cafe.\n\n"
                "Создать: /cafe_token create [label]\n"
                "Пример: /cafe_token create Кассир"
            )
            return

        lines = ["Токены доступа Cafe:\n"]
        for t in tokens:
            lines.append(
                f"ID {t['id']}: {t.get('account_name', '?')} — {t.get('label', 'без метки')}\n"
                f"Создан: {t.get('created_at', '?')}"
            )
        lines.append(f"\nУдалить: /cafe_token delete <id>")
        await update.message.reply_text("\n".join(lines))
        return

    if args[0] == 'create':
        label = ' '.join(args[1:]) if len(args) > 1 else 'Кассир'

        # Find non-primary (Cafe) account
        accounts = db.get_accounts(telegram_user_id)
        cafe_account = next((a for a in accounts if not a.get('is_primary')), None)

        if not cafe_account:
            await update.message.reply_text("Нет вторичного аккаунта (Cafe). Сначала добавьте через /add_cafe")
            return

        token = db.create_cafe_token(telegram_user_id, cafe_account['id'], label)

        # Get webhook URL for the link
        import os
        base_url = os.environ.get('WEBHOOK_URL', 'https://your-app.railway.app')
        url = f"{base_url}/cafe/{token}/shift-closing"

        await update.message.reply_text(
            f"Токен создан для {cafe_account['account_name']} ({label})\n\n"
            f"Ссылка для сотрудника:\n{url}\n\n"
            f"Сотрудник может открыть эту ссылку в браузере телефона."
        )
        return

    if args[0] == 'delete' and len(args) > 1:
        try:
            token_id = int(args[1])
            success = db.delete_cafe_token(token_id, telegram_user_id)
            if success:
                await update.message.reply_text(f"Токен ID {token_id} удалён.")
            else:
                await update.message.reply_text(f"Токен ID {token_id} не найден.")
        except ValueError:
            await update.message.reply_text("Укажите числовой ID токена.")
        return

    await update.message.reply_text(
        "Управление токенами Cafe:\n\n"
        "/cafe_token list — список токенов\n"
        "/cafe_token create [метка] — создать токен\n"
        "/cafe_token delete <id> — удалить токен"
    )


@admin_only
async def cashier_token_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /cashier_token command - manage cashier access tokens for main dept shift closing"""
    telegram_user_id = update.effective_user.id
    db = get_database()

    args = context.args if context.args else []

    if not args or args[0] == 'list':
        tokens = db.list_cashier_tokens(telegram_user_id)
        if not tokens:
            await update.message.reply_text(
                "Нет токенов доступа для Кассира.\n\n"
                "Создать: /cashier_token create [label]\n"
                "Пример: /cashier_token create Кассир"
            )
            return

        lines = ["Токены доступа Кассира:\n"]
        for t in tokens:
            lines.append(
                f"ID {t['id']}: {t.get('account_name', '?')} — {t.get('label', 'без метки')}\n"
                f"Создан: {t.get('created_at', '?')}"
            )
        lines.append(f"\nУдалить: /cashier_token delete <id>")
        await update.message.reply_text("\n".join(lines))
        return

    if args[0] == 'create':
        label = ' '.join(args[1:]) if len(args) > 1 else 'Кассир'

        # Find primary account (main dept)
        accounts = db.get_accounts(telegram_user_id)
        primary_account = next((a for a in accounts if a.get('is_primary')), None)

        if not primary_account:
            await update.message.reply_text("Нет основного аккаунта. Сначала добавьте через /start")
            return

        token = db.create_cashier_token(telegram_user_id, primary_account['id'], label)

        import os
        base_url = os.environ.get('WEBHOOK_URL', 'https://your-app.railway.app')
        url = f"{base_url}/cashier/{token}/shift-closing"

        await update.message.reply_text(
            f"Токен создан для {primary_account['account_name']} ({label})\n\n"
            f"Ссылка для кассира:\n{url}\n\n"
            f"Кассир может открыть эту ссылку в браузере телефона."
        )
        return

    if args[0] == 'delete' and len(args) > 1:
        try:
            token_id = int(args[1])
            success = db.delete_cashier_token(token_id, telegram_user_id)
            if success:
                await update.message.reply_text(f"Токен ID {token_id} удалён.")
            else:
                await update.message.reply_text(f"Токен ID {token_id} не найден.")
        except ValueError:
            await update.message.reply_text("Укажите числовой ID токена.")
        return

    await update.message.reply_text(
        "Управление токенами Кассира:\n\n"
        "/cashier_token list — список токенов\n"
        "/cashier_token create [метка] — создать токен\n"
        "/cashier_token delete <id> — удалить токен"
    )


@admin_only
async def staff_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /staff command - manage web user accounts (login/password auth)"""
    telegram_user_id = update.effective_user.id
    db = get_database()

    args = context.args if context.args else []

    if not args or args[0] == 'list':
        users = db.list_web_users(telegram_user_id)
        if not users:
            await update.message.reply_text(
                "Нет аккаунтов.\n\n"
                "Создать: /staff create <роль> <логин> <пароль>\n"
                "Роли: owner, admin, cashier\n\n"
                "Пример: /staff create cashier meruyert 1234"
            )
            return

        role_labels = {'owner': 'Владелец', 'admin': 'Админ', 'cashier': 'Кассир'}
        lines = ["👥 Аккаунты:\n"]
        for u in users:
            active = "✅" if u.get('is_active', True) in (True, 1) else "❌"
            role = role_labels.get(u['role'], u['role'])
            label = u.get('label') or u['username']
            last_login = u.get('last_login', '—') or '—'
            lines.append(f"{active} ID {u['id']}: {label} ({role}) — логин: {u['username']}\n   Последний вход: {last_login}")

        lines.append(f"\n/staff delete <id> — удалить")
        lines.append(f"/staff reset <id> <пароль> — сменить пароль")
        await update.message.reply_text("\n".join(lines))
        return

    if args[0] == 'create' and len(args) >= 4:
        role = args[1].lower()
        username = args[2]
        password = args[3]
        label = ' '.join(args[4:]) if len(args) > 4 else None

        if role not in ('owner', 'admin', 'cashier'):
            await update.message.reply_text("Роль должна быть: owner, admin или cashier")
            return

        if len(password) < 4:
            await update.message.reply_text("Пароль должен быть минимум 4 символа")
            return

        # Determine poster_account_id based on role
        poster_account_id = None
        accounts = db.get_accounts(telegram_user_id)

        if role == 'admin':
            # Admin → non-primary account (Cafe)
            cafe_account = next((a for a in accounts if not a.get('is_primary')), None)
            if not cafe_account:
                await update.message.reply_text("Нет аккаунта Кафе. Сначала добавьте через /start")
                return
            poster_account_id = cafe_account['id']
        elif role == 'cashier':
            # Cashier → primary account (Main dept)
            primary_account = next((a for a in accounts if a.get('is_primary')), None)
            if not primary_account:
                await update.message.reply_text("Нет основного аккаунта. Сначала добавьте через /start")
                return
            poster_account_id = primary_account['id']

        user_id = db.create_web_user(
            telegram_user_id=telegram_user_id,
            username=username,
            password=password,
            role=role,
            label=label or username,
            poster_account_id=poster_account_id
        )

        if user_id:
            role_labels = {'owner': 'Владелец', 'admin': 'Админ', 'cashier': 'Кассир'}
            import os
            base_url = os.environ.get('WEBHOOK_URL', 'https://your-app.railway.app')
            # Delete user's command message (contains password in plaintext)
            try:
                await update.message.delete()
            except Exception:
                pass
            pwd_msg = await update.message.chat.send_message(
                f"✅ Аккаунт создан (ID {user_id})\n\n"
                f"Роль: {role_labels.get(role, role)}\n"
                f"Логин: {username}\n"
                f"Пароль: {password}\n\n"
                f"Ссылка для входа: {base_url}/login\n\n"
                f"⚠️ Это сообщение будет удалено через 60 секунд. Запишите пароль!"
            )
            # Schedule auto-delete after 60 seconds
            async def _delete_pwd_msg():
                import asyncio
                await asyncio.sleep(60)
                try:
                    await pwd_msg.delete()
                except Exception:
                    pass
            import asyncio
            asyncio.create_task(_delete_pwd_msg())
        else:
            await update.message.reply_text("❌ Ошибка создания. Возможно, логин уже занят.")
        return

    if args[0] == 'delete' and len(args) > 1:
        try:
            user_id = int(args[1])
            success = db.delete_web_user(user_id, telegram_user_id)
            if success:
                await update.message.reply_text(f"✅ Аккаунт ID {user_id} удалён.")
            else:
                await update.message.reply_text(f"Аккаунт ID {user_id} не найден.")
        except ValueError:
            await update.message.reply_text("Укажите числовой ID.")
        return

    if args[0] == 'reset' and len(args) >= 3:
        try:
            user_id = int(args[1])
            new_password = args[2]
            if len(new_password) < 4:
                await update.message.reply_text("Пароль должен быть минимум 4 символа")
                return
            success = db.reset_web_user_password(user_id, telegram_user_id, new_password)
            if success:
                # Delete user's command message (contains password in plaintext)
                try:
                    await update.message.delete()
                except Exception:
                    pass
                pwd_msg = await update.message.chat.send_message(
                    f"✅ Пароль для ID {user_id} обновлён.\n\n"
                    f"⚠️ Это сообщение будет удалено через 60 секунд."
                )
                async def _delete_reset_msg():
                    import asyncio
                    await asyncio.sleep(60)
                    try:
                        await pwd_msg.delete()
                    except Exception:
                        pass
                import asyncio
                asyncio.create_task(_delete_reset_msg())
            else:
                await update.message.reply_text(f"Аккаунт ID {user_id} не найден.")
        except ValueError:
            await update.message.reply_text("Укажите числовой ID.")
        return

    await update.message.reply_text(
        "Управление аккаунтами:\n\n"
        "/staff list — список аккаунтов\n"
        "/staff create <роль> <логин> <пароль> [имя]\n"
        "  Роли: owner, admin, cashier\n"
        "  Пример: /staff create cashier meruyert 1234 Меруерт\n"
        "/staff delete <id> — удалить аккаунт\n"
        "/staff reset <id> <пароль> — сменить пароль"
    )


@authorized_only
async def accounts_check_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle /accounts_check command - сверка счетов двух отделов

    Показывает суммарные балансы из PizzBurg и Pizzburg-cafe
    для сравнения с фактическими остатками.
    """
    telegram_user_id = update.effective_user.id

    await update.message.reply_text("📊 Загружаю балансы счетов из обоих отделов...")

    try:
        from accounts_check import get_accounts_summary
        summary = await get_accounts_summary(telegram_user_id)
        await update.message.reply_text(summary)

    except Exception as e:
        logger.error(f"Accounts check failed: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Ошибка сверки счетов: {str(e)[:300]}")


@authorized_only
async def check_discrepancy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle /check command - проверка расхождения по конкретному счету

    Usage: /check Kaspi Pay 1500000
    """
    telegram_user_id = update.effective_user.id

    # Parse arguments
    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "📊 Использование: /check <название_счета> <фактический_остаток>\n\n"
            "Примеры:\n"
            "  /check Kaspi Pay 1500000\n"
            "  /check Халык банк 2350000\n\n"
            "Сначала выполните /accounts_check чтобы увидеть все счета."
        )
        return

    try:
        # Last argument is the amount, everything before is account name
        actual_balance = float(context.args[-1].replace(',', '').replace(' ', ''))
        account_name = ' '.join(context.args[:-1])

        from accounts_check import calculate_discrepancy
        discrepancy, message = await calculate_discrepancy(
            telegram_user_id, account_name, actual_balance
        )

        await update.message.reply_text(message)

    except ValueError:
        await update.message.reply_text(
            "❌ Неверный формат суммы. Используйте число без пробелов.\n"
            "Пример: /check Kaspi Pay 1500000"
        )
    except Exception as e:
        logger.error(f"Check discrepancy failed: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Ошибка: {str(e)[:300]}")


# === Voice Handler ===

# === Photo Handler ===

@authorized_only
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle photo message (receipt OCR for order deletion OR invoice recognition)"""
    try:
        telegram_user_id = update.effective_user.id
        logger.info(f"📸 Photo message received from user {telegram_user_id}")

        await update.message.reply_text("📸 Распознаю фото...")

        # Get the largest photo
        photo = update.message.photo[-1]
        photo_file = await photo.get_file()

        # Download photo
        photo_path = Path(f"storage/photo_{update.message.message_id}.jpg")
        await photo_file.download_to_drive(photo_path)

        # Check if user is in expense input mode
        expense_input = context.user_data.get('expense_input')
        if expense_input and expense_input.get('mode') == 'waiting_photo':
            await handle_expense_photo(update, context, str(photo_path))
            photo_path.unlink()
            return

        # No active photo mode — ignore
        photo_path.unlink()
        await update.message.reply_text("Фото не обработано. Используйте веб-интерфейс для поставок и расходов.")

    except Exception as e:
        logger.error(f"Photo processing failed: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Ошибка обработки фото: {str(e)[:200]}")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text message"""
    # Log chat info for debugging
    chat_type = update.message.chat.type
    user_id = update.effective_user.id
    logger.info(f"Text message from user {user_id} in chat type: {chat_type}")

    # Check if user pressed menu buttons (ReplyKeyboard)
    text = update.message.text

    # Главное меню
    if text == "📥 Расходы":
        # Начинаем режим ввода расходов
        context.user_data['expense_input'] = {
            'mode': 'waiting_photo',  # Ждём фото листа кассира
            'items': [],
            'source': 'наличка'
        }
        keyboard = [
            [KeyboardButton("✅ Готово"), KeyboardButton("❌ Отмена")]
        ]
        await update.message.reply_text(
            "📥 **Режим ввода расходов**\n\n"
            "Отправьте:\n"
            "• 📷 Фото листа кассира (наличка)\n"
            "• 📄 XLSX выписку Kaspi\n\n"
            "Бот распознает и создаст список расходов с разбивкой на транзакции и поставки.",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
            parse_mode='Markdown'
        )
        return

    elif text == "📦 Поставки":
        # Начинаем режим ввода поставок
        await handle_supply_mode_start(update, context)
        return

    elif text == "💰 Зарплаты":
        # Показать выбор количества кассиров
        keyboard = [
            [
                InlineKeyboardButton("👥 2 кассира", callback_data="cashiers_2"),
                InlineKeyboardButton("👥👥 3 кассира", callback_data="cashiers_3")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "💰 **Расчёт зарплат**\n\n"
            "Сколько кассиров на смене сегодня?",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        return

    elif text == "📱 Приложение":
        # Отправить ссылку на WebApp
        keyboard = [[InlineKeyboardButton("📱 Открыть приложение", web_app=WebAppInfo(url=WEBAPP_URL))]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "Нажмите кнопку, чтобы открыть приложение:",
            reply_markup=reply_markup
        )
        return

    elif text == "⚙️ Ещё":
        # Показать подменю
        await update.message.reply_text(
            "Дополнительные функции:",
            reply_markup=get_more_menu_keyboard()
        )
        return

    elif text == "← Назад":
        # Вернуться в главное меню
        await update.message.reply_text(
            "Главное меню:",
            reply_markup=get_main_menu_keyboard()
        )
        return

    # Подменю "Ещё"
    elif text == "🏪 Закрыть кассу":
        # Показать выбор отдела
        keyboard = [
            [
                InlineKeyboardButton("🍕 PizzBurg", callback_data="close_cash_dept:pittsburgh"),
                InlineKeyboardButton("☕ PizzBurg Cafe", callback_data="close_cash_dept:pittsburgh_cafe")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "🏪 **Закрытие кассы**\n\n"
            "Выберите отдел:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        return

    elif text == "📝 Транзакции":
        # Создать дневные транзакции
        await update.message.reply_text("⏳ Создаю дневные транзакции...")
        await run_daily_transactions_for_user(update.effective_user.id)
        await update.message.reply_text("✅ Дневные транзакции созданы!")
        return

    elif text == "📊 Отчёт недели":
        # Сгенерировать еженедельный отчёт
        await update.message.reply_text("⏳ Генерирую еженедельный отчёт...")
        await run_weekly_report_for_user(update.effective_user.id, context.application)
        return

    elif text == "📈 Отчёт месяца":
        # Сгенерировать месячный отчёт
        await update.message.reply_text("⏳ Генерирую месячный отчёт...")
        await run_monthly_report_for_user(update.effective_user.id, context.application)
        return

    elif text == "🔄 Сверка счетов":
        # Начинаем интерактивную сверку счетов
        await update.message.reply_text("📊 Загружаю данные из Poster...")
        try:
            from accounts_check import get_poster_balances, ACCOUNTS_TO_CHECK

            # Получаем балансы из Poster (без показа пользователю)
            poster_balances = await get_poster_balances(update.effective_user.id)

            # Сохраняем в context для последующих шагов
            context.user_data['accounts_check'] = {
                'step': 0,  # 0=Закуп, 1=Kaspi, 2=Халык
                'poster_balances': poster_balances,
                'actual_balances': {}
            }

            # Запрашиваем первый счет
            await update.message.reply_text(
                "💵 Введите фактический остаток:\n\n"
                "**Оставил в кассе (на закупы)**\n\n"
                "Просто напишите число, например: 127500",
                reply_markup=ReplyKeyboardMarkup(
                    [[KeyboardButton("❌ Отмена")]],
                    resize_keyboard=True
                )
            )
        except Exception as e:
            logger.error(f"Accounts check failed: {e}", exc_info=True)
            await update.message.reply_text(f"❌ Ошибка: {str(e)[:200]}")
        return

    # Check if user is in onboarding flow (BEFORE authorization check)
    onboarding_step = context.user_data.get('onboarding_step')
    if onboarding_step:
        await handle_onboarding(update, context, onboarding_step)
        return

    # Check authorization for registered users only
    from database import get_database
    db = get_database()
    user_data = db.get_user(user_id)

    if not user_data:
        # User not registered
        await update.message.reply_text(
            f"👋 Привет!\n\n"
            f"Вы еще не зарегистрированы.\n"
            f"Отправьте команду /start для регистрации и получения 14-дневного триала!"
        )
        return

    # Admins and allowed users bypass subscription check
    if user_id not in ADMIN_USER_IDS and user_id not in ALLOWED_USER_IDS:
        # Check if subscription is active (only for users not in allowed lists)
        if not db.is_subscription_active(user_id):
            await update.message.reply_text(
                f"⛔ Ваша подписка истекла.\n\n"
                f"Для продолжения работы необходимо продлить подписку.\n"
                f"Используйте /subscription для подробностей."
            )
            return

    # Check if in salary flow (расчет зарплат)
    if 'salary_flow' in context.user_data:
        step = context.user_data['salary_flow'].get('step')

        if step == 'waiting_cashier_names':
            await salary_flow_handlers.handle_cashier_names(update, context, text)
            return
        elif step == 'waiting_staff_names':
            await salary_flow_handlers.handle_staff_names(update, context, text)
            return

    # Check if in accounts check flow (сверка счетов)
    if 'accounts_check' in context.user_data:
        await handle_accounts_check_input(update, context, text)
        return

    # Check if in expense input flow (ввод расходов)
    if 'expense_input' in context.user_data:
        await handle_expense_input_text(update, context, text)
        return

    # Check if in supply input flow (ввод поставок)
    if 'supply_input' in context.user_data:
        await handle_supply_input_text(update, context, text)
        return

    # Check if in cash closing flow
    if 'cash_closing_data' in context.user_data:
        await handle_cash_input_step(update, context)
        return

    # Check if waiting for manual ingredient input
    if context.user_data.get('waiting_for_manual_ingredient'):
        await handle_manual_ingredient_input(update, context)
        return

    # Check if editing ingredient for item in draft
    if 'editing_ingredient_for_item' in context.user_data:
        await handle_item_ingredient_search_input(update, context)
        return

    # Check if waiting for quantity change
    if 'waiting_for_quantity_change' in context.user_data:
        await handle_quantity_change_input(update, context)
        return

    # Check if waiting for price change
    if 'waiting_for_price_change' in context.user_data:
        await handle_price_change_input(update, context)
        return

    # Check if waiting for template name input
    if context.user_data.get('waiting_for_template_name'):
        handled = await handle_template_name_input(update, context, update.message.text.strip())
        if handled:
            return

    # Check if waiting for template price update
    if context.user_data.get('waiting_for_template_prices'):
        handled = await handle_template_price_update(update, context, update.message.text.strip())
        if handled:
            return

    text = update.message.text

    # Ignore system button texts that might be sent when session expired
    ignored_texts = ["✅ Готово", "❌ Отмена", "← Назад"]
    if text in ignored_texts:
        await update.message.reply_text(
            "Сессия истекла. Выберите действие из меню.",
            reply_markup=get_main_menu_keyboard()
        )
        return

    # Try to parse quick template syntax (e.g., "лаваш 400")
    template_match = try_parse_quick_template(text)
    if template_match:
        template_name, quantity = template_match
        success = await create_shipment_from_template(update, context, template_name, quantity)
        if success:
            return
        # If template not found, continue to regular processing

    await process_transaction_text(update, context, text)


async def handle_quantity_change_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle user input for quantity change"""
    item_index = context.user_data.pop('waiting_for_quantity_change')
    text = update.message.text.strip()

    # Parse quantity
    try:
        # Replace comma with dot for decimal
        text = text.replace(',', '.')
        quantity = float(text)

        if quantity <= 0:
            await update.message.reply_text("❌ Количество должно быть больше нуля. Попробуйте снова:")
            context.user_data['waiting_for_quantity_change'] = item_index
            return

    except ValueError:
        await update.message.reply_text("❌ Неверный формат. Введите число (например: 5 или 2.5):")
        context.user_data['waiting_for_quantity_change'] = item_index
        return

    # Update draft
    message_id = context.user_data.get('current_message_id')
    drafts = context.user_data.get('drafts', {})
    draft = drafts.get(message_id)

    if not draft or item_index >= len(draft['items']):
        await update.message.reply_text("❌ Ошибка: товар не найден.")
        return

    item = draft['items'][item_index]
    old_sum = item['sum']

    # Update quantity and recalculate sum
    item['num'] = quantity
    item['sum'] = int(quantity * item['price'])

    # Update total
    draft['total_amount'] = draft['total_amount'] - old_sum + item['sum']

    # Save draft
    drafts[message_id] = draft
    context.user_data['drafts'] = drafts

    await update.message.reply_text(
        f"✅ Количество изменено:\n"
        f"{item['name']}: {quantity} x {item['price']:,} = {item['sum']:,} {CURRENCY}"
    )

    # Show updated draft with edit buttons
    message = await show_supply_draft(update, context, draft)

    # Update current_message_id to the new message
    if message:
        context.user_data['current_message_id'] = message.message_id
        context.user_data['drafts'][message.message_id] = draft


async def handle_price_change_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle user input for price change"""
    item_index = context.user_data.pop('waiting_for_price_change')
    text = update.message.text.strip()

    # Parse price
    try:
        # Remove spaces and commas, replace comma with dot
        text = text.replace(' ', '').replace(',', '.')
        price = int(float(text))

        if price <= 0:
            await update.message.reply_text("❌ Цена должна быть больше нуля. Попробуйте снова:")
            context.user_data['waiting_for_price_change'] = item_index
            return

    except ValueError:
        await update.message.reply_text("❌ Неверный формат. Введите число (например: 5000):")
        context.user_data['waiting_for_price_change'] = item_index
        return

    # Update draft
    message_id = context.user_data.get('current_message_id')
    drafts = context.user_data.get('drafts', {})
    draft = drafts.get(message_id)

    if not draft or item_index >= len(draft['items']):
        await update.message.reply_text("❌ Ошибка: товар не найден.")
        return

    item = draft['items'][item_index]
    old_sum = item['sum']

    # Update price and recalculate sum
    item['price'] = price
    item['sum'] = int(item['num'] * price)

    # Update total
    draft['total_amount'] = draft['total_amount'] - old_sum + item['sum']

    # Save draft
    drafts[message_id] = draft
    context.user_data['drafts'] = drafts

    await update.message.reply_text(
        f"✅ Цена изменена:\n"
        f"{item['name']}: {item['num']} x {price:,} = {item['sum']:,} {CURRENCY}"
    )

    # Show updated draft with edit buttons
    message = await show_supply_draft(update, context, draft)

    # Update current_message_id to the new message
    if message:
        context.user_data['current_message_id'] = message.message_id
        context.user_data['drafts'][message.message_id] = draft


async def process_transaction_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    """Process text as transaction"""
    try:
        telegram_user_id = update.effective_user.id
        await update.message.reply_text("🔍 Анализирую данные...")

        # Try Claude parser first, fallback to simple parser
        parsed = None

        try:
            parser = get_parser_service()
            parsed = await parser.parse_transaction(text)
        except Exception as e:
            logger.warning(f"Claude parser failed: {e}, trying simple parser")

        # Fallback to simple parser
        if not parsed:
            simple_parser = get_simple_parser()
            parsed = simple_parser.parse_transaction(text)

        if not parsed:
            await update.message.reply_text(
                "❌ Не удалось распознать транзакцию.\n\n"
                "Попробуйте формат:\n"
                "Расход: \"Донерщик 7500 Максат\"\n"
                "Перевод: \"Перевод 50000 с Касипай в Кассу комментарий Жандос\"\n"
                "Поставка: \"Поставщик Метро. Айсберг 2.2 кг по 1600, Помидоры 10.4 по 850\""
            )
            return

        # Check if it's a transfer
        if parsed.get('type') == 'transfer':
            await process_transfer(update, context, parsed)
            return

        # Check if it's a supply
        if parsed.get('type') == 'supply':
            await process_supply(update, context, parsed)
            return

        # Check if it's multiple expenses
        if parsed.get('type') == 'multiple_expenses':
            await process_multiple_expenses(update, context, parsed)
            return

        # Match category
        category_matcher = get_category_matcher(telegram_user_id)
        category_match = category_matcher.match(parsed['category'])

        if not category_match:
            await update.message.reply_text(
                f"❌ Категория '{parsed['category']}' не найдена.\n\n"
                f"Доступные: донерщик, повара, кассиры, курьер, кухрабочая, официанты"
            )
            return

        category_id, category_name = category_match

        # Match account (default to "закуп" if not specified)
        account_matcher = get_account_matcher(telegram_user_id)
        account_from_text = parsed.get('account_from', 'закуп')
        account_from_id = account_matcher.match(account_from_text)

        if not account_from_id:
            account_from_id = DEFAULT_ACCOUNT_FROM_ID

        account_from_name = account_matcher.get_account_name(account_from_id)

        # Build draft
        amount = int(parsed['amount'])
        comment = parsed.get('comment', '').strip()

        draft = {
            'type': 0,  # expense
            'category_id': category_id,
            'category_name': category_name,
            'account_from_id': account_from_id,
            'account_from_name': account_from_name,
            'amount': amount,
            'comment': comment,
            'date': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

        # Show draft with buttons
        message = await show_draft(update, context, draft)

        # Store draft with message_id as key
        if message:
            if 'drafts' not in context.user_data:
                context.user_data['drafts'] = {}
            context.user_data['drafts'][message.message_id] = draft
            logger.info(f"✅ Draft saved: message_id={message.message_id}, available drafts={list(context.user_data['drafts'].keys())}")

    except Exception as e:
        logger.error(f"Transaction processing failed: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Ошибка обработки: {e}")


async def process_transfer(update: Update, context: ContextTypes.DEFAULT_TYPE, parsed: Dict):
    """Process transfer between accounts"""
    try:
        telegram_user_id = update.effective_user.id

        # Match accounts
        account_matcher = get_account_matcher(telegram_user_id)

        account_from_text = parsed.get('account_from', 'касипай')
        account_from_id = account_matcher.match(account_from_text)
        if not account_from_id:
            account_from_id = 1  # Default: Kaspi Pay

        account_to_text = parsed.get('account_to', 'касса')
        account_to_id = account_matcher.match(account_to_text)
        if not account_to_id:
            account_to_id = 3  # Default: Денежный ящик

        account_from_name = account_matcher.get_account_name(account_from_id)
        account_to_name = account_matcher.get_account_name(account_to_id)

        # Build transfer draft
        amount = int(parsed['amount'])
        comment = parsed.get('comment', '').strip()

        draft = {
            'type': 2,  # transfer
            'account_from_id': account_from_id,
            'account_from_name': account_from_name,
            'account_to_id': account_to_id,
            'account_to_name': account_to_name,
            'amount': amount,
            'comment': comment,
            'date': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'category_id': None,  # transfers don't need category
            'category_name': None
        }

        # Show transfer draft
        message = await show_transfer_draft(update, context, draft)

        # Store draft with message_id as key
        if message:
            if 'drafts' not in context.user_data:
                context.user_data['drafts'] = {}
            context.user_data['drafts'][message.message_id] = draft
            logger.info(f"✅ Draft saved: message_id={message.message_id}, available drafts={list(context.user_data['drafts'].keys())}")

    except Exception as e:
        logger.error(f"Transfer processing failed: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Ошибка обработки перевода: {e}")


async def process_supply(update: Update, context: ContextTypes.DEFAULT_TYPE, parsed: Dict):
    """Process supply (поставка) from parsed data"""
    try:
        telegram_user_id = update.effective_user.id

        # Match supplier
        supplier_matcher = get_supplier_matcher(telegram_user_id)
        supplier_text = parsed.get('supplier', '')
        supplier_id = None

        if supplier_text:
            supplier_id = supplier_matcher.match(supplier_text)

        # If supplier not found or not specified, ask user to select
        if not supplier_id:
            # Store parsed data for later use
            context.user_data['pending_supply'] = parsed
            await show_supplier_selection(update, context, supplier_text=supplier_text)
            return

        supplier_name = supplier_matcher.get_supplier_name(supplier_id)

        # Match account
        account_matcher = get_account_matcher(telegram_user_id)
        account_text = parsed.get('account') or 'оставил в кассе'
        account_id = account_matcher.match(account_text)

        if not account_id:
            account_id = DEFAULT_ACCOUNT_FROM_ID

        account_name = account_matcher.get_account_name(account_id)

        # Match ingredients and products
        ingredient_matcher = get_ingredient_matcher(telegram_user_id)
        product_matcher = get_product_matcher(telegram_user_id)
        items = parsed.get('items', [])
        matched_items = []
        unmatched_items = []  # Items that need manual selection
        total_amount = 0

        for item in items:
            # Log original item name from OCR
            logger.info(f"🔍 Matching item: \"{item['name']}\"")

            # Try ingredient match first (with priority: Pizzburg → Pizzburg-cafe)
            ingredient_match = ingredient_matcher.match_with_priority(item['name'])
            if ingredient_match:
                logger.info(f"   Ingredient match: {ingredient_match[1]} (ID: {ingredient_match[0]}, account: {ingredient_match[4]}, score: {ingredient_match[3]:.1f})")
            else:
                logger.info(f"   Ingredient match: None")

            # Try product match if ingredient not found or score too low
            product_match = None
            if not ingredient_match or ingredient_match[3] < 75:
                product_match = product_matcher.match_with_priority(item['name'])
                if product_match:
                    logger.info(f"   Product match: {product_match[1]} (ID: {product_match[0]}, account: {product_match[4]}, score: {product_match[3]:.1f})")
                else:
                    logger.info(f"   Product match: None")

            # Use best match
            best_match = None

            # Определить является ли товар напитком
            item_is_beverage = any(keyword in item['name'].lower() for keyword in [
                'кола', 'cola', 'кока', 'coca',
                'спрайт', 'sprite',
                'фанта', 'fanta',
                'пико', 'piko', 'pulpy',
                'фьюз', 'fuze',
                'бонаква', 'bonaqua',
                'швепс', 'schweppes',
                'нести', 'nestea',
                'квас', 'сок', 'juice',
                'лимонад', 'чай', 'tea',
                'вода', 'water', 'напиток',
                'пэт', 'pet',  # упаковка
            ])

            is_product_match = False
            if ingredient_match and product_match:
                # Оба найдены - выбор зависит от типа товара
                if item_is_beverage:
                    # Напиток: приоритет товарам при равном или близком score
                    logger.info(f"   🥤 Beverage detected: prioritizing product over ingredient")
                    # Используем product если score >= ingredient_score - 5 (допускаем небольшую погрешность)
                    if product_match[3] >= ingredient_match[3] - 5:
                        best_match = product_match
                        is_product_match = True
                    else:
                        best_match = ingredient_match
                else:
                    # Не напиток: приоритет ингредиентам
                    if ingredient_match[3] >= product_match[3]:
                        best_match = ingredient_match
                    else:
                        best_match = product_match
                        is_product_match = True
            elif ingredient_match:
                best_match = ingredient_match
            elif product_match:
                best_match = product_match
                is_product_match = True

            # Check if match is good enough (score >= MIN_MATCH_CONFIDENCE or exact match)
            if not best_match or best_match[3] < MIN_MATCH_CONFIDENCE:
                # Need manual selection
                unmatched_items.append(item)
                continue

            item_id, item_name, unit, match_score, account_name = best_match
            qty = item['qty']
            price = item.get('price')

            # Skip items without price
            if price is None:
                logger.warning(f"Skipping item '{item['name']}' - no price specified")
                continue

            # Adjust for packing if needed (e.g., 10 упак → 300 шт)
            adjusted_qty, adjusted_price, packing_size = adjust_for_packing(
                item_name, qty, price, item['name']
            )

            item_sum = int(adjusted_qty * adjusted_price)

            # Determine item_type for Poster API
            if is_product_match:
                item_type = 'product'
            else:
                # Look up ingredient type from matcher data
                ing_info = ingredient_matcher.ingredients.get(item_id, {})
                item_type = ing_info.get('type', 'ingredient')

            matched_items.append({
                'id': item_id,
                'name': item_name,
                'num': adjusted_qty,
                'price': adjusted_price,
                'sum': item_sum,
                'match_score': match_score,
                'original_name': item['name'],
                'packing_size': packing_size,
                'account_name': account_name,  # Добавляем информацию об аккаунте
                'item_type': item_type  # 'ingredient', 'semi_product', or 'product'
            })

            total_amount += item_sum

        # If there are unmatched items, ask user to select manually
        if unmatched_items:
            # Store context for later
            context.user_data['supply_context'] = {
                'supplier_id': supplier_id,
                'supplier_name': supplier_name,
                'account_id': account_id,
                'account_name': account_name,
                'matched_items': matched_items,
                'unmatched_items': unmatched_items,
                'total_amount': total_amount,
                'current_unmatched_index': 0
            }

            # Show selection UI for first unmatched item
            await show_ingredient_selection(update, context)
            return

        # Group items by account (Pizzburg or Pizzburg-cafe)
        items_by_account = {}
        for item in matched_items:
            acc_name = item.get('account_name', 'Unknown')
            if acc_name not in items_by_account:
                items_by_account[acc_name] = []
            items_by_account[acc_name].append(item)

        # Notify about skipped items without prices
        skipped_count = len(items) - len(matched_items) - len(unmatched_items)
        if skipped_count > 0:
            await update.message.reply_text(
                f"⚠️ Пропущено {skipped_count} позиций без указания цены.\n"
                f"Добавьте цены в накладную или введите их вручную."
            )

        # Create separate drafts for each account
        if len(items_by_account) > 1:
            # Multi-account: show summary first
            summary_lines = ["📦 Создано несколько черновиков для разных аккаунтов:\n"]
            for acc_name, acc_items in items_by_account.items():
                acc_total = sum(it['sum'] for it in acc_items)
                summary_lines.append(f"• {acc_name}: {len(acc_items)} товаров, {acc_total:,} {CURRENCY}")

            await update.message.reply_text("\n".join(summary_lines))

        # Build and show draft for each account
        for acc_name, acc_items in items_by_account.items():
            acc_total = sum(it['sum'] for it in acc_items)

            draft = {
                'type': 'supply',
                'supplier_id': supplier_id,
                'supplier_name': supplier_name,
                'account_id': account_id,
                'account_name': account_name,
                'storage_id': 1,  # Default: Продукты
                'storage_name': 'Продукты',
                'items': acc_items,
                'total_amount': acc_total,
                'date': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                'poster_account_name': acc_name  # Для отправки в правильный аккаунт Poster
            }

            # Show supply draft with account name in title
            message = await show_supply_draft(update, context, draft, account_label=acc_name if len(items_by_account) > 1 else None)

            # Store draft with message_id as key
            if message:
                if 'drafts' not in context.user_data:
                    context.user_data['drafts'] = {}
                context.user_data['drafts'][message.message_id] = draft
                context.user_data['current_message_id'] = message.message_id
                logger.info(f"✅ Draft saved for {acc_name}: message_id={message.message_id}, available drafts={list(context.user_data['drafts'].keys())}")

        # Если есть пропущенные товары с кандидатами - показать UI выбора
        skipped_with_candidates = parsed.get('skipped_items_with_candidates', [])
        skipped_no_candidates = parsed.get('skipped_items', [])

        if skipped_with_candidates or skipped_no_candidates:
            logger.warning(f"Skipped items found but manual selection UI is not available (removed)")

    except Exception as e:
        logger.error(f"Supply processing failed: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Ошибка обработки поставки: {e}")


def get_confidence_indicator(score):
    """Возвращает эмодзи индикатор на основе confidence score"""
    if score >= 85:
        return "✅"
    elif score >= 60:
        return "⚠️"
    else:
        return "❌"


async def show_supply_draft(update: Update, context: ContextTypes.DEFAULT_TYPE, draft: Dict, account_label: str = None):
    """Show supply draft with confirmation buttons"""
    items_lines = []
    for idx, item in enumerate(draft['items']):
        # Get confidence score and indicator
        confidence = item.get('match_score', 100)  # default 100 если нет
        indicator = get_confidence_indicator(confidence)

        line = f"  {idx+1}. {item['name']}: {item['num']} x {item['price']:,} = {item['sum']:,} {CURRENCY} {indicator} {confidence:.0f}%"
        # Add original name from invoice if available
        if item.get('original_name'):
            line += f"\n   _из накладной: {item['original_name']}_"
        items_lines.append(line)

    items_text = "\n".join(items_lines)

    # Count low confidence items
    low_confidence_count = sum(1 for item in draft['items'] if item.get('match_score', 100) < 85)
    low_confidence_hint = ""
    if low_confidence_count > 0:
        low_confidence_hint = f"\n💡 ⚠️ {low_confidence_count} поз. с низкой уверенностью - проверьте\n"

    # Add account label if multi-account
    account_label_text = f" [{account_label}]" if account_label else ""

    message_text = (
        f"📦 Черновик поставки{account_label_text}:\n\n"
        f"Поставщик: {draft['supplier_name']}\n"
        f"Счёт: {draft['account_name']}\n"
        f"Склад: {draft['storage_name']}\n\n"
        f"Товары:\n{items_text}\n"
        f"{low_confidence_hint}\n"
        f"Итого: {draft['total_amount']:,} {CURRENCY}\n"
        f"Дата: {draft['date']}\n\n"
        f"💡 Нажмите на товар чтобы изменить"
    )

    # Create keyboard with item edit buttons
    keyboard = []

    # Add buttons for each item (2 per row)
    item_buttons = []
    for idx, item in enumerate(draft['items']):
        button_text = f"{idx+1}. {item['name'][:20]}"  # Truncate long names
        item_buttons.append(InlineKeyboardButton(button_text, callback_data=f"edit_item:{idx}"))

        if len(item_buttons) == 2 or idx == len(draft['items']) - 1:
            keyboard.append(item_buttons)
            item_buttons = []

    # Add main action buttons
    keyboard.extend([
        [
            InlineKeyboardButton("✅ Подтвердить", callback_data="confirm"),
        ],
        [
            InlineKeyboardButton("🏪 Изменить поставщика", callback_data="change_supplier"),
            InlineKeyboardButton("💰 Изменить счёт", callback_data="change_account")
        ]
    ])

    # Add "Save as template" button only if not already from template
    if not draft.get('from_template'):
        keyboard.append([
            InlineKeyboardButton("💾 Сохранить как шаблон", callback_data="save_as_template")
        ])

    keyboard.append([
        InlineKeyboardButton("❌ Отмена", callback_data="cancel")
    ])
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Get chat for sending messages (works for both message and callback_query)
    if update.callback_query:
        chat = update.callback_query.message.chat
        return await context.bot.send_message(chat.id, message_text, reply_markup=reply_markup)
    else:
        return await update.message.reply_text(message_text, reply_markup=reply_markup)


async def show_supply_draft_edit(query, context: ContextTypes.DEFAULT_TYPE, draft: Dict):
    """Show supply draft with edit buttons (for editing existing message)"""
    items_lines = []
    for idx, item in enumerate(draft['items']):
        # Get confidence score and indicator
        confidence = item.get('match_score', 100)  # default 100 если нет
        indicator = get_confidence_indicator(confidence)

        line = f"  {idx+1}. {item['name']}: {item['num']} x {item['price']:,} = {item['sum']:,} {CURRENCY} {indicator} {confidence:.0f}%"
        # Add original name from invoice if available
        if item.get('original_name'):
            line += f"\n   _из накладной: {item['original_name']}_"
        items_lines.append(line)

    items_text = "\n".join(items_lines)

    # Count low confidence items
    low_confidence_count = sum(1 for item in draft['items'] if item.get('match_score', 100) < 85)
    low_confidence_hint = ""
    if low_confidence_count > 0:
        low_confidence_hint = f"\n💡 ⚠️ {low_confidence_count} поз. с низкой уверенностью - проверьте\n"

    message_text = (
        "📦 Черновик поставки:\n\n"
        f"Поставщик: {draft['supplier_name']}\n"
        f"Счёт: {draft['account_name']}\n"
        f"Склад: {draft['storage_name']}\n\n"
        f"Товары:\n{items_text}\n"
        f"{low_confidence_hint}\n"
        f"Итого: {draft['total_amount']:,} {CURRENCY}\n"
        f"Дата: {draft['date']}\n\n"
        f"💡 Нажмите на товар чтобы изменить"
    )

    # Create keyboard with item edit buttons
    keyboard = []

    # Add buttons for each item (2 per row)
    item_buttons = []
    for idx, item in enumerate(draft['items']):
        button_text = f"{idx+1}. {item['name'][:20]}"  # Truncate long names
        item_buttons.append(InlineKeyboardButton(button_text, callback_data=f"edit_item:{idx}"))

        if len(item_buttons) == 2 or idx == len(draft['items']) - 1:
            keyboard.append(item_buttons)
            item_buttons = []

    # Add main action buttons
    keyboard.extend([
        [
            InlineKeyboardButton("✅ Подтвердить", callback_data="confirm"),
        ],
        [
            InlineKeyboardButton("🏪 Изменить поставщика", callback_data="change_supplier"),
            InlineKeyboardButton("💰 Изменить счёт", callback_data="change_account")
        ]
    ])

    # Add "Save as template" button only if not already from template
    if not draft.get('from_template'):
        keyboard.append([
            InlineKeyboardButton("💾 Сохранить как шаблон", callback_data="save_as_template")
        ])

    keyboard.append([
        InlineKeyboardButton("❌ Отмена", callback_data="cancel")
    ])
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(message_text, reply_markup=reply_markup)


async def show_transfer_draft(update: Update, context: ContextTypes.DEFAULT_TYPE, draft: Dict):
    """Show transfer draft with confirmation buttons"""
    message_text = (
        "🔄 Черновик перевода:\n\n"
        f"Откуда: {draft['account_from_name']}\n"
        f"Куда: {draft['account_to_name']}\n"
        f"Сумма: {draft['amount']:,} {CURRENCY}\n"
        f"Комментарий: {draft['comment'] or '—'}\n"
        f"Дата: {draft['date']}\n"
    )

    keyboard = [
        [
            InlineKeyboardButton("✅ Подтвердить", callback_data="confirm"),
        ],
        [
            InlineKeyboardButton("📤 Изменить откуда", callback_data="change_account_from"),
            InlineKeyboardButton("📥 Изменить куда", callback_data="change_account_to")
        ],
        [
            InlineKeyboardButton("❌ Отмена", callback_data="cancel")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    return await update.message.reply_text(message_text, reply_markup=reply_markup)


async def process_multiple_expenses(update: Update, context: ContextTypes.DEFAULT_TYPE, parsed: Dict):
    """Process multiple expense transactions from a single voice message"""
    try:
        telegram_user_id = update.effective_user.id

        # Match account
        account_matcher = get_account_matcher(telegram_user_id)
        account_text = parsed.get('account', 'оставил в кассе')
        account_id = account_matcher.match(account_text)

        if not account_id:
            account_id = DEFAULT_ACCOUNT_FROM_ID

        account_name = account_matcher.get_account_name(account_id)

        # Match categories for each transaction
        category_matcher = get_category_matcher(telegram_user_id)
        transactions = parsed.get('transactions', [])
        matched_transactions = []
        total_amount = 0

        for txn in transactions:
            category_text = txn.get('category', '')
            category_match = category_matcher.match(category_text)

            if not category_match:
                await update.message.reply_text(
                    f"❌ Не удалось найти категорию '{category_text}'.\n"
                    f"Доступные категории можно посмотреть командой /categories"
                )
                return

            category_id, category_name = category_match
            amount = txn.get('amount', 0)
            comment = txn.get('comment', '')

            matched_transactions.append({
                'category_id': category_id,
                'category_name': category_name,
                'amount': amount,
                'comment': comment
            })

            total_amount += amount

        # Create draft
        draft = {
            'type': 'multiple_expenses',
            'account_from_id': account_id,
            'account_from_name': account_name,
            'transactions': matched_transactions,
            'total_amount': total_amount,
            'date': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

        # Show draft with confirmation
        message = await show_multiple_expenses_draft(update, context, draft)

        # Save draft with message_id as key
        if message:
            if 'drafts' not in context.user_data:
                context.user_data['drafts'] = {}
            context.user_data['drafts'][message.message_id] = draft
            logger.info(f"✅ Multiple expenses draft saved: message_id={message.message_id}")

    except Exception as e:
        logger.exception(f"Error processing multiple expenses: {e}")
        await update.message.reply_text(f"❌ Ошибка обработки транзакций: {e}")


async def show_multiple_expenses_draft(update: Update, context: ContextTypes.DEFAULT_TYPE, draft: Dict):
    """Show multiple expenses draft with confirmation buttons"""
    transactions_text = "\n".join([
        f"  • {txn['category_name']}: {txn['amount']:,} {CURRENCY} ({txn['comment'] or '—'})"
        for txn in draft['transactions']
    ])

    message_text = (
        "💸 Черновик множественных транзакций:\n\n"
        f"Счёт: {draft['account_from_name']}\n"
        f"Количество транзакций: {len(draft['transactions'])}\n\n"
        f"Транзакции:\n{transactions_text}\n\n"
        f"Итого: {draft['total_amount']:,} {CURRENCY}\n"
        f"Дата: {draft['date']}\n"
    )

    keyboard = [
        [
            InlineKeyboardButton("✅ Подтвердить всё", callback_data="confirm"),
        ],
        [
            InlineKeyboardButton("💰 Изменить счёт", callback_data="change_account")
        ],
        [
            InlineKeyboardButton("❌ Отмена", callback_data="cancel")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    return await update.message.reply_text(message_text, reply_markup=reply_markup)


async def show_draft(update: Update, context: ContextTypes.DEFAULT_TYPE, draft: Dict):
    """Show transaction draft with confirmation buttons"""
    message_text = (
        "💸 Черновик транзакции:\n\n"
        f"Категория: {draft['category_name']}\n"
        f"Сумма: {draft['amount']:,} {CURRENCY}\n"
        f"Счёт: {draft['account_from_name']}\n"
        f"Комментарий: {draft['comment'] or '—'}\n"
        f"Дата: {draft['date']}\n"
    )

    keyboard = [
        [
            InlineKeyboardButton("✅ Подтвердить", callback_data="confirm"),
            InlineKeyboardButton("💰 Изменить счёт", callback_data="change_account")
        ],
        [
            InlineKeyboardButton("❌ Отмена", callback_data="cancel")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    return await update.message.reply_text(message_text, reply_markup=reply_markup)


# === Supplier Selection ===

async def show_supplier_selection(update: Update, context: ContextTypes.DEFAULT_TYPE, supplier_text: str = ""):
    """Show supplier selection UI when supplier not found or not specified"""
    telegram_user_id = update.effective_user.id
    supplier_matcher = get_supplier_matcher(telegram_user_id)

    message = "🏪 Выберите поставщика:\n\n"
    if supplier_text:
        message = f"❌ Поставщик '{supplier_text}' не найден.\n\n🏪 Выберите поставщика:\n\n"

    # Get all suppliers sorted by name
    suppliers = [(sid, sinfo['name']) for sid, sinfo in supplier_matcher.suppliers.items()]
    suppliers.sort(key=lambda x: x[1])

    # Create keyboard with supplier buttons (2 per row)
    keyboard = []
    row = []
    for supplier_id, supplier_name in suppliers:
        row.append(InlineKeyboardButton(supplier_name, callback_data=f"select_supplier:{supplier_id}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []

    # Add last row if not empty
    if row:
        keyboard.append(row)

    # Add cancel button
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel_supplier_selection")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(message, reply_markup=reply_markup)


async def handle_supplier_selection(update: Update, context: ContextTypes.DEFAULT_TYPE, supplier_id: int):
    """Handle supplier selection and continue with supply processing"""
    query = update.callback_query
    telegram_user_id = update.effective_user.id

    # Get pending supply data
    parsed = context.user_data.get('pending_supply')
    if not parsed:
        await query.edit_message_text("❌ Данные поставки потеряны.")
        return

    # Update supplier in parsed data
    supplier_matcher = get_supplier_matcher(telegram_user_id)
    supplier_name = supplier_matcher.get_supplier_name(supplier_id)

    await query.edit_message_text(f"✅ Выбран поставщик: {supplier_name}\n\n⏳ Обрабатываю поставку...")

    # Set supplier in parsed data
    parsed['supplier'] = supplier_name
    parsed['supplier_id'] = supplier_id

    # Clear pending supply
    del context.user_data['pending_supply']

    # Create a fake update with the original message for process_supply
    # We need to call process_supply with the selected supplier
    # Instead, let's directly continue the logic from process_supply

    # Match account
    account_matcher = get_account_matcher(telegram_user_id)
    account_text = parsed.get('account') or 'оставил в кассе'
    account_id = account_matcher.match(account_text)

    if not account_id:
        account_id = DEFAULT_ACCOUNT_FROM_ID

    account_name = account_matcher.get_account_name(account_id)

    # Match ingredients and products
    ingredient_matcher = get_ingredient_matcher(telegram_user_id)
    product_matcher = get_product_matcher(telegram_user_id)
    items = parsed.get('items', [])
    matched_items = []
    unmatched_items = []
    total_amount = 0

    for item in items:
        # Try ingredient match first
        ingredient_match = ingredient_matcher.match(item['name'])

        # Try product match if ingredient not found or score too low
        product_match = None
        if not ingredient_match or ingredient_match[3] < 75:
            product_match = product_matcher.match(item['name'])

        # Use best match
        best_match = None
        is_product_match = False
        if ingredient_match and product_match:
            if ingredient_match[3] >= product_match[3]:
                best_match = ingredient_match
            else:
                best_match = product_match
                is_product_match = True
        elif ingredient_match:
            best_match = ingredient_match
        elif product_match:
            best_match = product_match
            is_product_match = True

        if not best_match or best_match[3] < 75:
            unmatched_items.append(item)
            continue

        item_id, item_name, unit, match_score, account_name_match = best_match
        qty = item['qty']
        price = item.get('price')

        # Skip items without price
        if price is None:
            logger.warning(f"Skipping item '{item['name']}' - no price specified")
            continue

        # Adjust for packing if needed (e.g., 10 упак → 300 шт)
        adjusted_qty, adjusted_price, packing_size = adjust_for_packing(
            item_name, qty, price, item['name']
        )

        item_sum = int(adjusted_qty * adjusted_price)

        # Determine item_type for Poster API
        if is_product_match:
            item_type = 'product'
        else:
            ing_info = ingredient_matcher.ingredients.get(item_id, {})
            item_type = ing_info.get('type', 'ingredient')

        matched_items.append({
            'id': item_id,
            'name': item_name,
            'num': adjusted_qty,
            'price': adjusted_price,
            'sum': item_sum,
            'match_score': match_score,
            'original_name': item['name'],
            'packing_size': packing_size,
            'account_name': account_name_match,
            'item_type': item_type
        })

        total_amount += item_sum

    # If there are unmatched items, ask user to select manually
    if unmatched_items:
        context.user_data['supply_context'] = {
            'supplier_id': supplier_id,
            'supplier_name': supplier_name,
            'account_id': account_id,
            'account_name': account_name,
            'matched_items': matched_items,
            'unmatched_items': unmatched_items,
            'total_amount': total_amount,
            'current_unmatched_index': 0
        }

        # Need to create a fake update for show_ingredient_selection
        # Use query.message as the message
        fake_update = Update(
            update_id=update.update_id,
            message=query.message
        )
        await show_ingredient_selection(fake_update, context)
        return

    # Build supply draft
    draft = {
        'type': 'supply',
        'supplier_id': supplier_id,
        'supplier_name': supplier_name,
        'account_id': account_id,
        'account_name': account_name,
        'storage_id': 1,
        'storage_name': 'Продукты',
        'items': matched_items,
        'total_amount': total_amount,
        'date': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    # Show supply draft - need fake update
    fake_update = Update(
        update_id=update.update_id,
        message=query.message
    )
    message = await show_supply_draft(fake_update, context, draft)

    if message:
        if 'drafts' not in context.user_data:
            context.user_data['drafts'] = {}
        context.user_data['drafts'][message.message_id] = draft
        context.user_data['current_message_id'] = message.message_id


# === Ingredient Selection ===

async def show_ingredient_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show ingredient selection UI for unmatched items"""
    telegram_user_id = update.effective_user.id
    supply_ctx = context.user_data.get('supply_context')

    # Get chat for sending messages (works for both message and callback_query)
    if update.callback_query:
        chat = update.callback_query.message.chat
    else:
        chat = update.message.chat

    if not supply_ctx:
        await context.bot.send_message(chat.id, "❌ Контекст поставки потерян.")
        return

    unmatched_items = supply_ctx['unmatched_items']
    current_index = supply_ctx['current_unmatched_index']

    if current_index >= len(unmatched_items):
        # All items processed, show draft
        await finalize_supply_draft(update, context)
        return

    current_item = unmatched_items[current_index]
    item_name = current_item['name']

    # Get top matches
    ingredient_matcher = get_ingredient_matcher(telegram_user_id)
    top_matches = ingredient_matcher.get_top_matches(item_name, limit=3, score_cutoff=40)

    if not top_matches:
        # No matches at all, skip this item
        message = (
            f"❌ Не найдено похожих ингредиентов для: \"{item_name}\"\n"
            f"Количество: {current_item['qty']}, Цена: {current_item['price']}\n\n"
            f"Эта позиция будет пропущена."
        )
        await context.bot.send_message(chat.id, message)

        # Move to next item
        supply_ctx['current_unmatched_index'] += 1
        context.user_data['supply_context'] = supply_ctx
        await show_ingredient_selection(update, context)
        return

    # Build keyboard with top matches (one button per row)
    keyboard = []

    for idx, (ing_id, ing_name, unit, score) in enumerate(top_matches):
        button_text = f"[{idx+1}] {ing_name} ({int(score)}%)"
        button = InlineKeyboardButton(
            button_text,
            callback_data=f"select_ingredient_{ing_id}"
        )
        keyboard.append([button])

    # Add "Manual search" and "Skip" buttons
    keyboard.append([
        InlineKeyboardButton("✏️ Другое (ввести вручную)", callback_data="manual_ingredient_search")
    ])
    keyboard.append([
        InlineKeyboardButton("⏭️ Пропустить", callback_data="skip_ingredient")
    ])

    reply_markup = InlineKeyboardMarkup(keyboard)

    progress = f"({current_index + 1}/{len(unmatched_items)})"
    item_sum = int(current_item['qty'] * current_item['price'])
    message = (
        f"❓ Не уверен в распознавании товара #{current_index + 1}:\n\n"
        f"📝 Оригинал: \"{item_name}\"\n"
        f"📦 Количество: {current_item['qty']} x {current_item['price']:,} = {item_sum:,} {CURRENCY}\n\n"
        f"Выберите правильный вариант:"
    )

    await context.bot.send_message(chat.id, message, reply_markup=reply_markup)


async def finalize_supply_draft(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Create supply draft after all ingredient selections"""
    supply_ctx = context.user_data.get('supply_context')

    # Get chat for sending messages (works for both message and callback_query)
    if update.callback_query:
        chat = update.callback_query.message.chat
    else:
        chat = update.message.chat

    if not supply_ctx:
        await context.bot.send_message(chat.id, "❌ Контекст поставки потерян.")
        return

    matched_items = supply_ctx['matched_items']
    total_amount = supply_ctx['total_amount']

    if not matched_items:
        await context.bot.send_message(
            chat.id,
            "❌ Все позиции были пропущены. Поставка отменена."
        )
        context.user_data.pop('supply_context', None)
        return

    # Build supply draft
    draft = {
        'type': 'supply',
        'supplier_id': supply_ctx['supplier_id'],
        'supplier_name': supply_ctx['supplier_name'],
        'account_id': supply_ctx['account_id'],
        'account_name': supply_ctx['account_name'],
        'storage_id': 1,
        'storage_name': 'Продукты',
        'items': matched_items,
        'total_amount': total_amount,
        'date': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    # Show supply draft
    message = await show_supply_draft(update, context, draft)

    # Store draft
    if message:
        drafts = context.user_data.get('drafts', {})
        drafts[message.message_id] = draft
        context.user_data['drafts'] = drafts
        context.user_data['current_message_id'] = message.message_id

    # Clear supply context
    context.user_data.pop('supply_context', None)


async def handle_ingredient_selection(update: Update, context: ContextTypes.DEFAULT_TYPE, ingredient_id: int):
    """Handle when user selects an ingredient from suggestions"""
    query = update.callback_query
    telegram_user_id = update.effective_user.id
    supply_ctx = context.user_data.get('supply_context')

    if not supply_ctx:
        await query.edit_message_text("❌ Контекст поставки потерян.")
        return

    unmatched_items = supply_ctx['unmatched_items']
    current_index = supply_ctx['current_unmatched_index']
    current_item = unmatched_items[current_index]

    # Get ingredient info
    ingredient_matcher = get_ingredient_matcher(telegram_user_id)
    ingredient_info = ingredient_matcher.get_ingredient_info(ingredient_id)

    if not ingredient_info:
        await query.edit_message_text("❌ Ошибка: ингредиент не найден.")
        return

    # Add to matched items
    qty = current_item['qty']
    price = current_item['price']
    item_sum = int(qty * price)

    matched_item = {
        'id': ingredient_id,
        'name': ingredient_info['name'],
        'num': qty,
        'price': price,
        'sum': item_sum,
        'match_score': 100,  # User confirmed
        'original_name': current_item['name']
    }

    supply_ctx['matched_items'].append(matched_item)
    supply_ctx['total_amount'] += item_sum

    # Auto-learning: Save alias
    alias_created = False
    original_name = current_item['name'].strip()

    # Check conditions for creating alias
    if original_name and len(original_name) >= 3:
        # Normalize both names for comparison
        from matchers import normalize_text_for_matching
        original_normalized = normalize_text_for_matching(original_name)
        new_normalized = normalize_text_for_matching(ingredient_info['name'])

        # Only create alias if names are different
        if original_normalized != new_normalized:
            try:
                success = ingredient_matcher.add_alias(
                    alias_text=original_name,
                    ingredient_id=ingredient_id,
                    notes="Auto-learned from user selection"
                )
                if success:
                    alias_created = True
                    logger.info(f"📚 Auto-created alias: '{original_name}' -> {ingredient_id} ({ingredient_info['name']})")
            except Exception as e:
                logger.error(f"Failed to auto-create alias: {e}")

    # Show confirmation message
    message = f"✅ Выбрано: {ingredient_info['name']}"
    if alias_created:
        message += f"\n📚 Алиас сохранён: \"{original_name}\" → \"{ingredient_info['name']}\""

    await query.edit_message_text(message)

    # Move to next unmatched item
    supply_ctx['current_unmatched_index'] += 1
    context.user_data['supply_context'] = supply_ctx

    logger.info(f"Moving to next item: {current_index + 1}/{len(unmatched_items)}")

    # Show next item or finalize
    await show_ingredient_selection(update, context)


async def handle_ingredient_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle when user skips an ingredient"""
    query = update.callback_query
    supply_ctx = context.user_data.get('supply_context')

    if not supply_ctx:
        await query.edit_message_text("❌ Контекст поставки потерян.")
        return

    unmatched_items = supply_ctx['unmatched_items']
    current_index = supply_ctx['current_unmatched_index']
    current_item = unmatched_items[current_index]

    await query.edit_message_text(
        f"⏭️ Пропущено: \"{current_item['name']}\" "
        f"({current_item['qty']} × {current_item['price']})"
    )

    # Move to next unmatched item
    supply_ctx['current_unmatched_index'] += 1
    context.user_data['supply_context'] = supply_ctx

    # Show next item or finalize
    await show_ingredient_selection(update, context)


async def start_manual_ingredient_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start manual ingredient search - ask user to type ingredient name"""
    query = update.callback_query
    supply_ctx = context.user_data.get('supply_context')

    if not supply_ctx:
        await query.edit_message_text("❌ Контекст поставки потерян.")
        return

    # Set flag to wait for manual input
    context.user_data['waiting_for_manual_ingredient'] = True

    await query.edit_message_text(
        "✏️ Введите название ингредиента для поиска:\n\n"
        "Например: Полпа, Соус барбекю, Огурцы и т.д.\n\n"
        "Бот найдёт похожие ингредиенты в базе Poster."
    )


async def handle_manual_ingredient_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle manual ingredient name input from user"""
    if not context.user_data.get('waiting_for_manual_ingredient'):
        return

    telegram_user_id = update.effective_user.id
    user_input = update.message.text.strip()
    supply_ctx = context.user_data.get('supply_context')

    if not supply_ctx:
        await update.message.reply_text("❌ Контекст поставки потерян.")
        return

    # Clear waiting flag
    context.user_data['waiting_for_manual_ingredient'] = False

    # Search for ingredient
    ingredient_matcher = get_ingredient_matcher(telegram_user_id)
    top_matches = ingredient_matcher.get_top_matches(user_input, limit=10, score_cutoff=50)

    if not top_matches:
        await update.message.reply_text(
            f"❌ Не найдено ингредиентов для: \"{user_input}\"\n\n"
            f"Попробуйте другое название или пропустите этот товар."
        )
        # Show ingredient selection again
        await show_ingredient_selection(update, context)
        return

    # Get current item info
    unmatched_items = supply_ctx['unmatched_items']
    current_index = supply_ctx['current_unmatched_index']
    current_item = unmatched_items[current_index]

    # Build keyboard with matches (one button per row for better readability)
    keyboard = []

    for idx, (ing_id, ing_name, unit, score) in enumerate(top_matches):
        button_text = f"[{idx+1}] {ing_name} ({int(score)}%)"
        button = InlineKeyboardButton(
            button_text,
            callback_data=f"select_ingredient_{ing_id}"
        )
        keyboard.append([button])

    # Add back button
    keyboard.append([
        InlineKeyboardButton("« Назад к предложенным", callback_data="back_to_suggestions")
    ])

    reply_markup = InlineKeyboardMarkup(keyboard)

    item_sum = int(current_item['qty'] * current_item['price'])
    message = (
        f"🔍 Найдено {len(top_matches)} совпадений для \"{user_input}\":\n\n"
        f"📝 Оригинал: \"{current_item['name']}\"\n"
        f"📦 Количество: {current_item['qty']} x {current_item['price']:,} = {item_sum:,} {CURRENCY}\n\n"
        f"Выберите подходящий:"
    )

    await update.message.reply_text(message, reply_markup=reply_markup)


# === Menu Callback Handlers ===

async def handle_calculate_salaries_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка нажатия кнопки 'Рассчитать зарплаты'"""
    query = update.callback_query
    telegram_user_id = update.effective_user.id

    # Спросить количество кассиров
    keyboard = [
        [
            InlineKeyboardButton("👥 2 кассира", callback_data="cashiers_2"),
            InlineKeyboardButton("👥👥 3 кассира", callback_data="cashiers_3")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        "💰 **Расчёт зарплат**\n\n"
        "Сколько кассиров на смене сегодня?",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )


async def handle_cashiers_count_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, cashier_count: int):
    """Обработка выбора количества кассиров - спрашиваем время выхода помощника"""
    query = update.callback_query

    # Сохраняем количество кассиров в контекст
    context.user_data['cashier_count'] = cashier_count

    # Спрашиваем время выхода помощника донерщика
    keyboard = [
        [
            InlineKeyboardButton("⏰ С 10:00", callback_data="assistant_time_10"),
            InlineKeyboardButton("⏰ С 12:00", callback_data="assistant_time_12"),
            InlineKeyboardButton("⏰ С 14:00", callback_data="assistant_time_14")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        f"💰 **Расчёт зарплат**\n\n"
        f"Кассиров: {cashier_count} чел\n\n"
        f"Когда вышел помощник донерщика?",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )


async def handle_assistant_time_and_calculate(update: Update, context: ContextTypes.DEFAULT_TYPE, assistant_start_time: str):
    """Обработка выбора времени помощника и расчёт зарплат"""
    query = update.callback_query
    telegram_user_id = update.effective_user.id

    # Получаем количество кассиров из контекста
    cashier_count = context.user_data.get('cashier_count', 2)

    await query.edit_message_text(
        f"⏳ Рассчитываю зарплаты для {cashier_count} кассиров и донерщика...",
        parse_mode='Markdown'
    )

    try:
        from cashier_salary import calculate_and_create_cashier_salary
        from doner_salary import calculate_and_create_doner_salary

        # Рассчитать зарплату кассиров
        cashier_result = await calculate_and_create_cashier_salary(telegram_user_id, cashier_count)

        # Рассчитать зарплату донерщика с учётом времени выхода помощника
        doner_result = await calculate_and_create_doner_salary(telegram_user_id, None, assistant_start_time)

        # Сформировать отчёт
        message_lines = ["✅ **Зарплаты рассчитаны!**\n"]

        if cashier_result['success']:
            salaries = cashier_result['salaries']
            salary_per_cashier = salaries[0]['salary'] if salaries else 0
            transaction_ids = [s['transaction_id'] for s in salaries]
            message_lines.append(f"👥 **Кассиры ({cashier_count} чел):**")
            for s in salaries:
                message_lines.append(f"   {s['name']}: {s['salary']:,}₸".replace(',', ' '))
            message_lines.append(f"   ID транзакций: {', '.join(str(id) for id in transaction_ids)}")
        else:
            message_lines.append(f"❌ Ошибка расчёта кассиров: {cashier_result.get('error')}")

        message_lines.append("")

        if doner_result['success']:
            message_lines.append(f"🌮 **Донерщик:**")
            message_lines.append(f"   Донеров продано: {doner_result['doner_count']} шт")
            message_lines.append(f"   Базовая зарплата: {doner_result['base_salary']:,}₸".replace(',', ' '))
            if doner_result['bonus'] > 0:
                message_lines.append(f"   Бонус за помощника: +{doner_result['bonus']:,}₸".replace(',', ' '))
            message_lines.append(f"   Итого зарплата: {doner_result['salary']:,}₸".replace(',', ' '))
            message_lines.append(f"   ID транзакции: {doner_result['transaction_id']}")
            message_lines.append("")
            message_lines.append(f"👷 **Помощник донерщика:**")
            message_lines.append(f"   Вышел: {assistant_start_time}")
            message_lines.append(f"   Зарплата: {doner_result['assistant_salary']:,}₸".replace(',', ' '))
            message_lines.append(f"   ID транзакции: {doner_result['assistant_transaction_id']}")
        else:
            message_lines.append(f"❌ Ошибка расчёта донерщика: {doner_result.get('error')}")

        await query.edit_message_text(
            "\n".join(message_lines),
            parse_mode='Markdown'
        )

        # Очищаем контекст
        context.user_data.pop('cashier_count', None)

    except Exception as e:
        logger.error(f"Salary calculation failed: {e}", exc_info=True)
        await query.edit_message_text(
            f"❌ Ошибка расчёта зарплат:\n{str(e)[:300]}"
        )
        context.user_data.pop('cashier_count', None)


async def handle_create_daily_transactions_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка нажатия кнопки 'Создать дневные транзакции'"""
    query = update.callback_query
    telegram_user_id = update.effective_user.id

    from daily_transactions import is_daily_transactions_enabled

    if not is_daily_transactions_enabled(telegram_user_id):
        await query.edit_message_text(
            "❌ Автоматические транзакции не включены для вашего аккаунта."
        )
        return

    await query.edit_message_text("⏳ Создаю ежедневные транзакции...")

    try:
        await run_daily_transactions_for_user(telegram_user_id)

        await query.edit_message_text(
            "✅ Ежедневные транзакции созданы!\n\n"
            "Проверьте Poster для подтверждения."
        )

    except Exception as e:
        logger.error(f"Daily transactions failed: {e}", exc_info=True)
        await query.edit_message_text(
            f"❌ Ошибка создания транзакций:\n{str(e)[:300]}"
        )


async def handle_generate_weekly_report_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка нажатия кнопки 'Еженедельный отчёт'"""
    query = update.callback_query
    telegram_user_id = update.effective_user.id

    await query.edit_message_text("⏳ Генерирую еженедельный отчёт...")

    try:
        from weekly_report import WeeklyReportGenerator

        generator = WeeklyReportGenerator(telegram_user_id)
        result = await generator.generate_weekly_report()

        if result['success']:
            await query.edit_message_text(
                result['report_text'],
                parse_mode='Markdown'
            )
        else:
            await query.edit_message_text(
                f"❌ Ошибка генерации отчёта:\n{result.get('error', 'Неизвестная ошибка')}"
            )

    except Exception as e:
        logger.error(f"Weekly report failed: {e}", exc_info=True)
        await query.edit_message_text(
            f"❌ Ошибка генерации отчёта:\n{str(e)[:300]}"
        )


async def handle_generate_monthly_report_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка нажатия кнопки 'Месячный отчёт'"""
    query = update.callback_query
    telegram_user_id = update.effective_user.id

    await query.edit_message_text("⏳ Генерирую ежемесячный отчёт...")

    try:
        from monthly_report import MonthlyReportGenerator

        generator = MonthlyReportGenerator(telegram_user_id)
        result = await generator.generate_monthly_report()

        if result['success']:
            await query.edit_message_text(
                result['report_text'],
                parse_mode='Markdown'
            )
        else:
            await query.edit_message_text(
                f"❌ Ошибка генерации месячного отчёта:\n{result.get('error', 'Неизвестная ошибка')}"
            )

    except Exception as e:
        logger.error(f"Monthly report failed: {e}", exc_info=True)
        await query.edit_message_text(
            f"❌ Ошибка генерации месячного отчёта:\n{str(e)[:300]}"
        )


async def handle_close_cash_register_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать выбор заведения для закрытия кассы"""
    query = update.callback_query

    keyboard = [
        [
            InlineKeyboardButton("🍕 PizzBurg", callback_data="close_cash_dept:pittsburgh"),
            InlineKeyboardButton("☕ PizzBurg Cafe", callback_data="close_cash_dept:pittsburgh_cafe")
        ],
        [
            InlineKeyboardButton("« Назад в меню", callback_data="back_to_menu")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        "🏪 **ЗАКРЫТЬ КАССОВУЮ СМЕНУ**\n\n"
        "Выберите заведение:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )


async def handle_cash_closing_start(update: Update, context: ContextTypes.DEFAULT_TYPE, dept: str):
    """Начать процесс закрытия кассы для выбранного заведения"""
    query = update.callback_query
    telegram_user_id = update.effective_user.id

    # Определяем telegram_user_id для выбранного заведения
    from database import get_database
    db = get_database()

    if dept == "pittsburgh":
        dept_name = "🍕 PizzBurg"
        dept_user_id = 167084307  # Pittsburgh
    elif dept == "pittsburgh_cafe":
        dept_name = "☕ PizzBurg Cafe"
        dept_user_id = 1486244636  # Pittsburgh Cafe
    else:
        await query.edit_message_text("❌ Неизвестное заведение")
        return

    await query.edit_message_text(f"🔄 Загружаю данные из Poster для {dept_name}...")

    try:
        from cash_shift_closing import CashShiftClosing

        # Получить данные из Poster
        closing = CashShiftClosing(dept_user_id)
        poster_data = await closing.get_poster_data()
        await closing.close()

        if not poster_data.get('success'):
            await query.edit_message_text(
                f"❌ Ошибка получения данных из Poster:\n{poster_data.get('error', 'Неизвестная ошибка')}"
            )
            return

        # Сохраняем данные в context для последующих шагов
        context.user_data['cash_closing_data'] = {
            'dept': dept,
            'dept_name': dept_name,
            'dept_user_id': dept_user_id,
            'poster_data': poster_data,
            'step': 'shift_start',  # ПЕРВЫЙ шаг - остаток на начало смены
            'inputs': {}  # Собираем введённые данные
        }

        # Показать данные из Poster и запросить остаток на начало смены
        message = (
            f"📊 **Данные из Poster** ({dept_name}):\n\n"
            f"💰 Торговля за день: {poster_data['trade_total']/100:,.0f}₸\n"
            f"🎁 Бонусы/онлайн: {poster_data['bonus']/100:,.0f}₸\n"
            f"💳 Безнал в Poster: {poster_data['poster_cashless']/100:,.0f}₸\n"
            f"💵 Наличка в Poster: {poster_data['poster_cash']/100:,.0f}₸\n"
            f"📦 Заказов обработано: {poster_data['transactions_count']}\n\n"
            f"➡️ **Введите остаток на начало смены** (из чека Poster, в тенге):\n"
            f"Например: `40477` или `0`"
        )

        keyboard = [[InlineKeyboardButton("❌ Отменить", callback_data="cash_closing_cancel")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(message, reply_markup=reply_markup, parse_mode='Markdown')

    except Exception as e:
        logger.error(f"Ошибка начала закрытия кассы: {e}", exc_info=True)
        await query.edit_message_text(
            f"❌ Ошибка:\n{str(e)[:300]}"
        )


async def handle_accounts_check_input(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    """
    Обработка ввода данных в интерактивной сверке счетов.

    Шаги:
    0 - Ждём ввод суммы Закуп
    1 - Ждём ввод суммы Kaspi Pay
    2 - Ждём ввод 2 сумм для Халык (торговля + остаток)
    """
    from accounts_check import ACCOUNTS_TO_CHECK, calculate_all_discrepancies, format_discrepancy_report

    data = context.user_data.get('accounts_check')
    if not data:
        return

    # Обработка отмены
    if text == "❌ Отмена":
        del context.user_data['accounts_check']
        await update.message.reply_text(
            "Сверка отменена.",
            reply_markup=get_main_menu_keyboard()
        )
        return

    step = data['step']

    # Шаг 0 и 1: ввод одной суммы
    if step in [0, 1]:
        try:
            amount = float(text.replace(',', '.').replace(' ', ''))
            if amount < 0:
                await update.message.reply_text("❌ Сумма не может быть отрицательной. Попробуйте ещё раз:")
                return

            # Сохраняем сумму
            account_name = ACCOUNTS_TO_CHECK[step]
            data['actual_balances'][account_name] = amount

            # Переходим к следующему шагу
            data['step'] = step + 1

            if step == 0:
                # Запрашиваем Kaspi Pay
                await update.message.reply_text(
                    "💳 Введите фактический остаток:\n\n"
                    "**Kaspi Pay**\n\n"
                    "Просто напишите число, например: 45000",
                    reply_markup=ReplyKeyboardMarkup(
                        [[KeyboardButton("❌ Отмена")]],
                        resize_keyboard=True
                    ),
                    parse_mode='Markdown'
                )
            else:
                # Запрашиваем Халык (2 суммы)
                await update.message.reply_text(
                    "🏦 Введите фактический остаток:\n\n"
                    "**Халык банк**\n\n"
                    "Введите 2 числа через пробел или с новой строки:\n"
                    "• Торговля за сегодня\n"
                    "• Остаток на счёте\n\n"
                    "Например: 85000 120000",
                    reply_markup=ReplyKeyboardMarkup(
                        [[KeyboardButton("❌ Отмена")]],
                        resize_keyboard=True
                    ),
                    parse_mode='Markdown'
                )

        except ValueError:
            await update.message.reply_text("❌ Неверный формат. Введите число, например: 127500")
            return

    # Шаг 2: ввод 2 сумм для Халык
    elif step == 2:
        try:
            # Парсим 2 числа (через пробел, запятую, или с новой строки)
            import re
            numbers = re.findall(r'[\d.,]+', text)

            if len(numbers) < 2:
                await update.message.reply_text(
                    "❌ Нужно 2 числа.\n\n"
                    "Введите торговлю за сегодня и остаток на счёте.\n"
                    "Например: 85000 120000"
                )
                return

            # Берём первые 2 числа
            amount1 = float(numbers[0].replace(',', '.'))
            amount2 = float(numbers[1].replace(',', '.'))

            if amount1 < 0 or amount2 < 0:
                await update.message.reply_text("❌ Суммы не могут быть отрицательными. Попробуйте ещё раз:")
                return

            # Сохраняем сумму Халык (сумма двух)
            halyk_total = amount1 + amount2
            data['actual_balances']['Халык банк'] = halyk_total

            # Рассчитываем расхождения и показываем отчёт
            results = calculate_all_discrepancies(
                data['poster_balances'],
                data['actual_balances']
            )

            report = format_discrepancy_report(results)

            # Очищаем состояние
            del context.user_data['accounts_check']

            await update.message.reply_text(
                report,
                reply_markup=get_main_menu_keyboard()
            )

        except ValueError:
            await update.message.reply_text(
                "❌ Неверный формат.\n\n"
                "Введите 2 числа, например: 85000 120000"
            )
            return


async def handle_expense_photo(update: Update, context: ContextTypes.DEFAULT_TYPE, photo_path: str):
    """Обработка фото в режиме ввода расходов"""
    from expense_input import (
        parse_cashier_sheet_from_image,
        ExpenseSession,
        ExpenseType,
        format_expense_list
    )

    try:
        await update.message.reply_text("🔍 Распознаю лист расходов...")

        # OCR + GPT парсинг
        items = await parse_cashier_sheet_from_image(photo_path)

        if not items:
            await update.message.reply_text(
                "❌ Не удалось распознать расходы.\n\n"
                "Убедитесь что фото чёткое и текст читаемый."
            )
            return

        # Определяем источник из распознанных позиций (берём из первой)
        detected_source = items[0].source if items else "наличка"
        source_db = "kaspi" if detected_source == "kaspi" else "cash"
        source_account = "Kaspi Gold" if detected_source == "kaspi" else "Оставил в кассе (на закупы)"

        # Сохраняем черновики в БД для веб-интерфейса
        from database import get_database
        db = get_database()
        db.save_expense_drafts(
            telegram_user_id=update.effective_user.id,
            items=items,
            source=source_db,
            source_account=source_account
        )

        # Сохраняем в сессию
        expense_data = context.user_data.get('expense_input', {})
        expense_data['items'] = items
        expense_data['mode'] = 'review'

        # Создаём сессию
        session = ExpenseSession(
            items=items,
            source_account=source_account
        )
        expense_data['session'] = session

        # Форматируем список
        text = format_expense_list(session)

        # Создаём inline кнопки для каждой позиции
        keyboard = []
        for i, item in enumerate(items):
            type_label = "📦→💰" if item.expense_type == ExpenseType.SUPPLY else "💰→📦"
            keyboard.append([
                InlineKeyboardButton(
                    f"{i+1}. {type_label} {item.description[:20]}",
                    callback_data=f"exp_toggle:{item.id}"
                )
            ])

        # Кнопки действий
        keyboard.append([
            InlineKeyboardButton("✅ Создать транзакции", callback_data="exp_create"),
            InlineKeyboardButton("❌ Отмена", callback_data="exp_cancel")
        ])

        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

    except Exception as e:
        logger.error(f"Ошибка обработки фото расходов: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Ошибка: {str(e)[:200]}")


async def handle_expense_input_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    """Обработка текста в режиме ввода расходов"""
    expense_data = context.user_data.get('expense_input')
    if not expense_data:
        return

    if text == "❌ Отмена":
        del context.user_data['expense_input']
        await update.message.reply_text(
            "Ввод расходов отменён.",
            reply_markup=get_main_menu_keyboard()
        )
        return

    if text == "✅ Готово":
        # Если есть items - показать финальный список
        if expense_data.get('items'):
            await update.message.reply_text(
                "Нажмите '✅ Создать транзакции' под списком расходов.",
                reply_markup=get_main_menu_keyboard()
            )
        else:
            await update.message.reply_text(
                "❌ Нет данных для обработки.\n"
                "Сначала отправьте фото листа кассира.",
                reply_markup=get_main_menu_keyboard()
            )
        return


async def handle_expense_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка inline кнопок для расходов"""
    from expense_input import ExpenseType, format_expense_list

    query = update.callback_query
    data = query.data

    expense_data = context.user_data.get('expense_input')
    if not expense_data:
        await query.edit_message_text("❌ Сессия истекла. Начните заново.")
        return

    session = expense_data.get('session')
    if not session:
        await query.edit_message_text("❌ Нет данных для обработки.")
        return

    # Переключить тип расхода
    if data.startswith("exp_toggle:"):
        item_id = data.split(":")[1]
        session.toggle_type(item_id)

        # Обновляем сообщение
        text = format_expense_list(session)

        keyboard = []
        for i, item in enumerate(session.items):
            type_label = "📦→💰" if item.expense_type == ExpenseType.SUPPLY else "💰→📦"
            keyboard.append([
                InlineKeyboardButton(
                    f"{i+1}. {type_label} {item.description[:20]}",
                    callback_data=f"exp_toggle:{item.id}"
                )
            ])

        keyboard.append([
            InlineKeyboardButton("✅ Создать транзакции", callback_data="exp_create"),
            InlineKeyboardButton("❌ Отмена", callback_data="exp_cancel")
        ])

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )

    # Создать транзакции
    elif data == "exp_create":
        await query.edit_message_text("⏳ Создаю транзакции в Poster...")

        try:
            from expense_input import create_transactions_in_poster
            from database import get_database
            from poster_client import PosterClient

            telegram_user_id = update.effective_user.id
            db = get_database()
            accounts = db.get_accounts(telegram_user_id)

            if not accounts:
                await query.edit_message_text("❌ Нет подключенных аккаунтов Poster")
                return

            account = accounts[0]
            client = PosterClient(
                telegram_user_id=telegram_user_id,
                poster_token=account['poster_token'],
                poster_user_id=account['poster_user_id'],
                poster_base_url=account['poster_base_url']
            )

            try:
                # Получаем счета и категории
                poster_accounts = await client.get_accounts()
                categories = await client.get_categories()

                # Находим счёт "Оставил в кассе"
                account_id = None
                for acc in poster_accounts:
                    if "закуп" in acc.get('name', '').lower() or "оставил" in acc.get('name', '').lower():
                        account_id = int(acc['account_id'])
                        break

                if not account_id and poster_accounts:
                    account_id = int(poster_accounts[0]['account_id'])

                # Маппинг категорий
                category_map = {}
                for cat in categories:
                    category_map[cat.get('category_name', '')] = int(cat.get('category_id', 1))

                # Дефолтная категория
                if "Прочее" not in category_map:
                    category_map["Прочее"] = list(category_map.values())[0] if category_map else 1

                # Создаём транзакции
                transactions = session.get_transactions()
                success = 0
                errors = []

                for item in transactions:
                    try:
                        cat_id = category_map.get(item.category, category_map.get("Прочее", 1))

                        await client.create_transaction(
                            transaction_type=0,
                            category_id=cat_id,
                            account_from_id=account_id,
                            amount=int(item.amount),
                            comment=item.description
                        )
                        success += 1
                    except Exception as e:
                        errors.append(f"{item.description}: {e}")

                # Формируем результат
                supplies = session.get_supplies()
                result_text = f"✅ Создано транзакций: {success}\n"

                if errors:
                    result_text += f"❌ Ошибок: {len(errors)}\n"

                if supplies:
                    result_text += f"\n📦 Осталось поставок: {len(supplies)}\n"
                    for s in supplies:
                        result_text += f"  • {s.amount:,.0f}₸ — {s.description}\n"

                # Очищаем сессию
                del context.user_data['expense_input']

                await query.edit_message_text(result_text)

                # Отправляем главное меню
                await context.bot.send_message(
                    chat_id=update.effective_user.id,
                    text="Главное меню:",
                    reply_markup=get_main_menu_keyboard()
                )

            finally:
                await client.close()

        except Exception as e:
            logger.error(f"Ошибка создания транзакций: {e}", exc_info=True)
            await query.edit_message_text(f"❌ Ошибка: {str(e)[:200]}")

    # Создать транзакции из Kaspi выписки
    elif data == "exp_create_kaspi":
        await query.edit_message_text("⏳ Создаю транзакции в Poster (Kaspi Pay)...")

        try:
            from database import get_database
            from poster_client import PosterClient

            telegram_user_id = update.effective_user.id
            db = get_database()
            accounts = db.get_accounts(telegram_user_id)

            if not accounts:
                await query.edit_message_text("❌ Нет подключенных аккаунтов Poster")
                return

            account = accounts[0]
            client = PosterClient(
                telegram_user_id=telegram_user_id,
                poster_token=account['poster_token'],
                poster_user_id=account['poster_user_id'],
                poster_base_url=account['poster_base_url']
            )

            try:
                # Получаем счета и категории
                poster_accounts = await client.get_accounts()
                categories = await client.get_categories()

                # Находим счёт "Kaspi Pay"
                account_id = None
                for acc in poster_accounts:
                    if "kaspi" in acc.get('name', '').lower():
                        account_id = int(acc['account_id'])
                        break

                if not account_id and poster_accounts:
                    # Fallback - первый счёт
                    account_id = int(poster_accounts[0]['account_id'])
                    logger.warning("Kaspi Pay не найден, использую первый счёт")

                # Маппинг категорий
                category_map = {}
                for cat in categories:
                    category_map[cat.get('category_name', '')] = int(cat.get('category_id', 1))

                if "Прочее" not in category_map:
                    category_map["Прочее"] = list(category_map.values())[0] if category_map else 1

                # Создаём транзакции
                transactions = session.get_transactions()
                success = 0
                errors = []

                for item in transactions:
                    try:
                        cat_id = category_map.get(item.category, category_map.get("Прочее", 1))

                        await client.create_transaction(
                            transaction_type=0,
                            category_id=cat_id,
                            account_from_id=account_id,
                            amount=int(item.amount),
                            comment=item.description
                        )
                        success += 1
                    except Exception as e:
                        errors.append(f"{item.description}: {e}")

                # Формируем результат
                supplies = session.get_supplies()
                result_text = f"✅ Создано транзакций (Kaspi Pay): {success}\n"

                if errors:
                    result_text += f"❌ Ошибок: {len(errors)}\n"

                if supplies:
                    result_text += f"\n📦 Осталось поставок: {len(supplies)}\n"
                    for s in supplies[:10]:  # Ограничиваем 10
                        result_text += f"  • {s.amount:,.0f}₸ — {s.description}\n"
                    if len(supplies) > 10:
                        result_text += f"  ... и ещё {len(supplies) - 10}\n"

                # Очищаем сессию
                del context.user_data['expense_input']

                await query.edit_message_text(result_text)

                await context.bot.send_message(
                    chat_id=update.effective_user.id,
                    text="Главное меню:",
                    reply_markup=get_main_menu_keyboard()
                )

            finally:
                await client.close()

        except Exception as e:
            logger.error(f"Ошибка создания транзакций Kaspi: {e}", exc_info=True)
            await query.edit_message_text(f"❌ Ошибка: {str(e)[:200]}")

    # Отмена
    elif data == "exp_cancel":
        del context.user_data['expense_input']
        await query.edit_message_text("Ввод расходов отменён.")

        await context.bot.send_message(
            chat_id=update.effective_user.id,
            text="Главное меню:",
            reply_markup=get_main_menu_keyboard()
        )


async def handle_supply_mode_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало режима ввода поставок - показываем pending расходы типа 'supply'"""
    from database import get_database

    telegram_user_id = update.effective_user.id
    db = get_database()

    # Получаем pending расходы с типом supply
    pending_supplies = db.get_pending_supply_items(telegram_user_id)

    # Получаем уже созданные черновики поставок
    supply_drafts = db.get_supply_drafts(telegram_user_id, status="pending")

    # Начинаем режим ввода поставок
    context.user_data['supply_input'] = {
        'mode': 'waiting_invoice',
        'pending_supplies': pending_supplies
    }

    keyboard = [
        [KeyboardButton("✅ Готово"), KeyboardButton("❌ Отмена")]
    ]

    text = "📦 **Режим поставок**\n\n"

    if pending_supplies:
        text += f"📋 Ожидают накладных: **{len(pending_supplies)}** позиций\n\n"
        for i, item in enumerate(pending_supplies[:10], 1):
            text += f"{i}. {item['amount']:,.0f}₸ — {item['description'][:30]}\n"
        if len(pending_supplies) > 10:
            text += f"... и ещё {len(pending_supplies) - 10} позиций\n"
        text += "\n"

    if supply_drafts:
        text += f"📄 Черновиков поставок: **{len(supply_drafts)}**\n"
        text += f"(откройте веб-интерфейс для редактирования)\n\n"

    text += "**Отправьте фото накладной** для создания поставки.\n"
    text += "Бот распознает товары и свяжет с pending расходами."

    await update.message.reply_text(
        text,
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
        parse_mode='Markdown'
    )




async def handle_supply_input_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    """Обработка текста в режиме поставок"""
    supply_data = context.user_data.get('supply_input')
    if not supply_data:
        return

    if text == "❌ Отмена":
        del context.user_data['supply_input']
        await update.message.reply_text(
            "Режим поставок отменён.",
            reply_markup=get_main_menu_keyboard()
        )
        return

    if text == "✅ Готово":
        del context.user_data['supply_input']
        await update.message.reply_text(
            "Режим поставок завершён.\n\n"
            "Откройте /supplies для просмотра черновиков.",
            reply_markup=get_main_menu_keyboard()
        )
        return


async def handle_supply_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка inline кнопок для поставок"""
    from database import get_database

    query = update.callback_query
    data = query.data

    telegram_user_id = update.effective_user.id
    db = get_database()

    # Создать поставку из черновика
    if data.startswith("supply_create:"):
        supply_draft_id = int(data.split(":")[1])

        await query.edit_message_text("⏳ Создаю поставку в Poster...")

        try:
            # Получаем черновик с позициями
            draft = db.get_supply_draft_with_items(supply_draft_id)
            if not draft:
                await query.edit_message_text("❌ Черновик не найден")
                return

            # Передаём в стандартный process_supply
            parsed = {
                'type': 'supply',
                'supplier': draft.get('supplier_name', ''),
                'account': 'Оставил в кассе',
                'items': []
            }

            for item in draft.get('items', []):
                parsed['items'].append({
                    'name': item['item_name'],
                    'qty': item['quantity'],
                    'price': item['price_per_unit']
                })

            # Отмечаем черновик как обработанный
            db.mark_supply_draft_processed(supply_draft_id)

            # Если есть связанный расход - тоже отмечаем
            if draft.get('linked_expense_draft_id'):
                db.mark_drafts_processed([draft['linked_expense_draft_id']])

            await query.edit_message_text("✅ Передано в обработку поставки...")

            # Вызываем process_supply
            await process_supply(update, context, parsed)

        except Exception as e:
            logger.error(f"Ошибка создания поставки: {e}", exc_info=True)
            await query.edit_message_text(f"❌ Ошибка: {str(e)[:200]}")

    # Удалить черновик поставки
    elif data.startswith("supply_delete:"):
        supply_draft_id = int(data.split(":")[1])

        if db.delete_supply_draft(supply_draft_id):
            await query.edit_message_text("🗑️ Черновик поставки удалён")
        else:
            await query.edit_message_text("❌ Ошибка удаления черновика")


async def handle_cash_input_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка ввода данных на текущем шаге закрытия кассы"""
    message = update.message
    text = message.text.strip()

    # Проверяем, что есть активная сессия закрытия кассы
    if 'cash_closing_data' not in context.user_data:
        await message.reply_text("❌ Нет активной сессии закрытия кассы. Используйте /menu")
        return

    data = context.user_data['cash_closing_data']
    current_step = data['step']
    dept_name = data['dept_name']
    dept = data['dept']

    # Парсим введённое число
    try:
        amount = float(text.replace(',', '.').replace(' ', ''))
        if amount < 0:
            await message.reply_text("❌ Сумма не может быть отрицательной. Попробуйте ещё раз:")
            return
    except ValueError:
        await message.reply_text("❌ Неверный формат. Введите число (например: 5000 или 0):")
        return

    # Сохраняем введённое значение
    data['inputs'][current_step] = amount

    # ВАЖНО: Если это shift_start, сохраняем в poster_data (в тийинах)
    if current_step == 'shift_start':
        data['poster_data']['shift_start'] = int(amount * 100)

    # Определяем следующий шаг
    steps_order = ['shift_start', 'wolt', 'halyk', 'kaspi', 'cash_bills', 'cash_coins', 'deposits', 'expenses', 'cash_to_leave']

    # Для PizzBurg Cafe пропускаем Halyk
    if dept == 'pittsburgh_cafe' and current_step == 'wolt':
        data['inputs']['halyk'] = 0
        next_step_idx = steps_order.index('kaspi')
    else:
        current_idx = steps_order.index(current_step)
        next_step_idx = current_idx + 1

    # Если все шаги пройдены - показать сводку
    if next_step_idx >= len(steps_order):
        await show_cash_closing_summary(update, context)
        return

    # Переход к следующему шагу
    next_step = steps_order[next_step_idx]
    data['step'] = next_step

    # Формируем текст запроса для следующего шага
    step_prompts = {
        'wolt': "➡️ **Введите сумму Wolt** (в тенге):",
        'halyk': "➡️ **Введите сумму Halyk** (в тенге):",
        'kaspi': "➡️ **Введите сумму Kaspi** (в тенге):",
        'cash_bills': "➡️ **Введите наличные (бумажные)** (в тенге):",
        'cash_coins': "➡️ **Введите наличные (монеты)** (в тенге):",
        'deposits': "➡️ **Введите внесения** (в тенге, 0 если не было):",
        'expenses': "➡️ **Введите расходы с кассы** (в тенге, 0 если не было):",
        'cash_to_leave': "➡️ **Сколько оставить бумажных денег на смену?** (в тенге):"
    }

    prompt = step_prompts.get(next_step, "Введите значение:")

    # Показываем текущий прогресс
    progress = f"✅ {current_step.replace('_', ' ').title()}: {amount:,.0f}₸\n\n"

    keyboard = [[InlineKeyboardButton("❌ Отменить", callback_data="cash_closing_cancel")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await message.reply_text(
        f"{progress}{prompt}\nНапример: `5000` или `0`",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )


async def show_cash_closing_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать сводку и запросить подтверждение закрытия кассы"""
    message = update.message
    data = context.user_data['cash_closing_data']

    poster_data = data['poster_data']
    inputs = data['inputs']
    dept_name = data['dept_name']

    try:
        from cash_shift_closing import CashShiftClosing

        closing = CashShiftClosing(data['dept_user_id'])

        # Расчёт итогов
        calculations = closing.calculate_totals(
            poster_data=poster_data,
            wolt=int(inputs['wolt']),
            halyk=int(inputs['halyk']),
            kaspi=int(inputs['kaspi']),
            cash_bills=int(inputs['cash_bills']),
            cash_coins=int(inputs['cash_coins']),
            deposits=int(inputs.get('deposits', 0)),
            expenses=int(inputs.get('expenses', 0))
        )

        # Сохраняем расчёты
        data['calculations'] = calculations

        day_diff = calculations['day_diff']
        diff_emoji = "✅" if abs(day_diff) < 1 else ("📈" if day_diff > 0 else "📉")

        summary = f"""
📊 **СВОДКА ДЛЯ {dept_name}**

**Данные Poster:**
• Торговля (наличные + безнал): {calculations['trade_total']:,.0f}₸
• Бонусы: {calculations['bonus']:,.0f}₸
• **Итого Poster (без бонусов):** {calculations['poster_total']:,.0f}₸

**Фактические данные:**
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

💵 **На смену оставлено:** {inputs['cash_to_leave']:,.0f}₸
💰 **К инкассации:** {calculations['cash_bills'] + calculations['cash_coins'] - inputs['cash_to_leave']:,.0f}₸

**Будут созданы транзакции:**
"""

        if abs(day_diff) >= 1:
            summary += f"• {'Излишек' if day_diff > 0 else 'Недостача'}: {abs(day_diff):,.0f}₸\n"
        else:
            summary += f"• Излишек/недостача: нет (0₸)\n"

        cashless_diff = calculations['cashless_diff']
        if abs(cashless_diff) >= 1:
            summary += f"• Корректировка безнал: {cashless_diff:+,.0f}₸\n"
        else:
            summary += f"• Корректировка безнал: не требуется\n"

        summary += f"• Закрытие смены: {inputs['cash_to_leave']:,.0f}₸\n"
        summary += "\n✅ Всё верно?"

        keyboard = [
            [
                InlineKeyboardButton("✅ Подтвердить", callback_data="cash_closing_confirm"),
                InlineKeyboardButton("❌ Отменить", callback_data="cash_closing_cancel")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await message.reply_text(summary, reply_markup=reply_markup, parse_mode='Markdown')

    except Exception as e:
        logger.error(f"Ошибка расчёта сводки: {e}", exc_info=True)
        await message.reply_text(f"❌ Ошибка расчёта:\n{str(e)[:300]}")


async def handle_cash_closing_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выполнить закрытие кассы и создать транзакции"""
    query = update.callback_query

    if 'cash_closing_data' not in context.user_data:
        await query.edit_message_text("❌ Нет активной сессии закрытия кассы")
        return

    data = context.user_data['cash_closing_data']
    dept_name = data['dept_name']

    await query.edit_message_text(f"⏳ Создаю транзакции для {dept_name}...")

    try:
        from cash_shift_closing import CashShiftClosing
        from datetime import datetime

        closing = CashShiftClosing(data['dept_user_id'])

        # Создаём транзакции
        result = await closing.create_transactions(
            calculations=data['calculations'],
            cash_to_leave=int(data['inputs']['cash_to_leave']),
            date=datetime.now().strftime("%Y%m%d")
        )

        await closing.close()

        if not result.get('success'):
            await query.edit_message_text(
                f"❌ Ошибка создания транзакций:\n{result.get('error', 'Неизвестная ошибка')}"
            )
            return

        # Формируем итоговый отчёт
        report = closing.format_report(
            poster_data=data['poster_data'],
            calculations=data['calculations'],
            transactions=result
        )

        # Очищаем данные сессии
        context.user_data.pop('cash_closing_data', None)

        keyboard = [[InlineKeyboardButton("« Назад в меню", callback_data="back_to_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(report, reply_markup=reply_markup, parse_mode='Markdown')

    except Exception as e:
        logger.error(f"Ошибка закрытия кассы: {e}", exc_info=True)
        await query.edit_message_text(f"❌ Ошибка:\n{str(e)[:300]}")




async def handle_confirm_supply_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, supply_id: int):
    """Обработка подтверждения поставки через storage.updateSupply"""
    query = update.callback_query
    telegram_user_id = update.effective_user.id

    await query.edit_message_text(f"✅ Подтверждаю поставку #{supply_id}...")

    try:
        # Получить сохранённые данные поставки
        draft_key = f'supply_draft_{supply_id}'
        supply_data = context.user_data.get(draft_key)

        if not supply_data:
            await query.edit_message_text(
                f"❌ Не найдены данные поставки #{supply_id}\n\n"
                f"Попробуйте создать поставку заново."
            )
            return

        from poster_client import PosterClient

        client = PosterClient(telegram_user_id)

        # Активируем поставку через storage.updateSupply (status=1)
        await client.update_supply(
            supply_id=supply_id,
            supplier_id=supply_data['supplier_id'],
            storage_id=supply_data['storage_id'],
            date=supply_data['date'],
            ingredients=supply_data['ingredients'],
            account_id=supply_data['account_id'],
            comment=supply_data['comment'],
            status=1  # АКТИВИРОВАТЬ
        )

        await client.close()

        # Удалить сохранённые данные после успешной активации
        context.user_data.pop(draft_key, None)

        await query.edit_message_text(
            f"✅ Поставка #{supply_id} успешно подтверждена!\n\n"
            f"Товары добавлены на склад.\n"
            f"Можете проверить в Poster:\n"
            f"Склад → Приходы → #{supply_id}"
        )

    except Exception as e:
        logger.error(f"Ошибка подтверждения поставки {supply_id}: {e}", exc_info=True)
        await query.edit_message_text(
            f"❌ Ошибка при подтверждении поставки:\n{str(e)[:200]}"
        )


async def handle_change_supplier_for_supply_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, supply_id: int):
    """Показать список поставщиков для выбора"""
    query = update.callback_query
    telegram_user_id = update.effective_user.id

    await query.edit_message_text(f"📋 Загружаю список поставщиков...")

    try:
        from poster_client import PosterClient

        client = PosterClient(telegram_user_id)

        # Получаем список поставщиков
        result = await client._request('GET', 'storage.getSuppliers')
        suppliers = result.get('response', [])

        await client.close()

        if not suppliers:
            await query.edit_message_text("❌ Поставщики не найдены в Poster")
            return

        # Создаём кнопки с поставщиками (по 1 в ряд)
        keyboard = []
        for supplier in suppliers[:20]:  # Показываем первых 20
            supplier_name = supplier.get('supplier_name', 'Без названия')
            supplier_id_btn = supplier.get('supplier_id')
            keyboard.append([
                InlineKeyboardButton(
                    f"📦 {supplier_name}",
                    callback_data=f"select_supplier:{supply_id}:{supplier_id_btn}"
                )
            ])

        # Кнопка отмены
        keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel_supply")])

        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            f"🔄 Выберите поставщика для поставки #{supply_id}:",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Ошибка загрузки поставщиков: {e}", exc_info=True)
        await query.edit_message_text(f"❌ Ошибка загрузки поставщиков:\n{str(e)[:200]}")


async def handle_select_supplier_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, supply_id: int, supplier_id: int):
    """Обработка выбора поставщика"""
    query = update.callback_query
    telegram_user_id = update.effective_user.id

    await query.edit_message_text(f"🔄 Обновляю поставщика для поставки #{supply_id}...")

    try:
        from poster_client import PosterClient

        client = PosterClient(telegram_user_id)

        # Обновляем поставщика в поставке
        result = await client._request('POST', 'supply.updateIncomingOrder', data={
            'incoming_order_id': supply_id,
            'supplier_id': supplier_id
        })

        # Получаем информацию о новом поставщике
        suppliers_result = await client._request('GET', 'storage.getSuppliers')
        suppliers = suppliers_result.get('response', [])
        supplier_name = next((s['supplier_name'] for s in suppliers if int(s['supplier_id']) == supplier_id), 'Неизвестный')

        await client.close()

        if result:
            # Показываем обновлённую информацию с кнопками подтверждения
            message_text = (
                f"✅ Поставщик обновлён!\n\n"
                f"📦 Новый поставщик: {supplier_name}\n"
                f"📝 Черновик поставки #{supply_id}\n\n"
                f"Подтвердить поставку?"
            )

            keyboard = [
                [
                    InlineKeyboardButton("✅ Подтвердить", callback_data=f"confirm_supply:{supply_id}"),
                    InlineKeyboardButton("❌ Отмена", callback_data="cancel_supply")
                ],
                [
                    InlineKeyboardButton("🔄 Изменить поставщика", callback_data=f"change_supplier_for_supply:{supply_id}")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await query.edit_message_text(message_text, reply_markup=reply_markup)
        else:
            await query.edit_message_text(f"❌ Не удалось обновить поставщика")

    except Exception as e:
        logger.error(f"Ошибка обновления поставщика: {e}", exc_info=True)
        await query.edit_message_text(f"❌ Ошибка:\n{str(e)[:200]}")


async def handle_close_shift_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка нажатия кнопки 'Закрыть смену'"""
    query = update.callback_query
    telegram_user_id = update.effective_user.id

    await query.edit_message_text("⏳ Получаю отчёт о смене...")

    try:
        from shift_closing import ShiftClosing

        shift = ShiftClosing(telegram_user_id)
        report = await shift.get_shift_report()

        if report['success']:
            # Показать отчёт и спросить количество кассиров
            formatted_report = shift.format_shift_report(report)

            keyboard = [
                [
                    InlineKeyboardButton("👥 2 кассира", callback_data="close_shift_2"),
                    InlineKeyboardButton("👥👥 3 кассира", callback_data="close_shift_3")
                ],
                [
                    InlineKeyboardButton("❌ Отмена", callback_data="cancel_shift_closing")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await query.edit_message_text(
                formatted_report + "\n\n**Сколько кассиров на смене?**",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        else:
            await query.edit_message_text(
                f"❌ Ошибка получения отчёта:\n{report.get('error', 'Неизвестная ошибка')}"
            )

    except Exception as e:
        logger.error(f"Shift report failed: {e}", exc_info=True)
        await query.edit_message_text(
            f"❌ Ошибка получения отчёта:\n{str(e)[:300]}"
        )


async def handle_close_shift_with_count_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, cashier_count: int):
    """Обработка закрытия смены с указанным количеством кассиров"""
    query = update.callback_query
    telegram_user_id = update.effective_user.id

    await query.edit_message_text(
        f"⏳ Закрываю смену с {cashier_count} кассирами...",
        parse_mode='Markdown'
    )

    try:
        from shift_closing import ShiftClosing

        shift = ShiftClosing(telegram_user_id)
        result = await shift.close_shift(cashier_count)

        if result['success']:
            # Форматируем числа с пробелами
            def format_money(amount):
                return f"{amount:,}".replace(',', ' ')

            message = (
                f"✅ **СМЕНА ЗАКРЫТА УСПЕШНО**\n\n"
                f"💵 **Зарплаты:**\n"
                f"├ Кассиры ({cashier_count} чел): {format_money(result['cashier_salary'])}₸ каждому\n"
                f"│  ID транзакций: {', '.join(str(id) for id in result['cashier_transactions'])}\n"
                f"└ Донерщик: {format_money(result['doner_salary'])}₸\n"
                f"   ID транзакции: {result['doner_transaction']}\n"
            )

            await query.edit_message_text(message, parse_mode='Markdown')
        else:
            await query.edit_message_text(
                f"❌ Ошибка закрытия смены:\n{result.get('error', 'Неизвестная ошибка')}"
            )

    except Exception as e:
        logger.error(f"Shift closing failed: {e}", exc_info=True)
        await query.edit_message_text(
            f"❌ Ошибка закрытия смены:\n{str(e)[:300]}"
        )


# === Callback Handler ===

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline button callbacks"""
    query = update.callback_query
    await query.answer()

    # Обработка расходов (expense input)
    if query.data.startswith("exp_"):
        await handle_expense_callback(update, context)
        return

    # Обработка поставок (supply input)
    if query.data.startswith("supply_"):
        await handle_supply_callback(update, context)
        return

    # Обработка сверки счетов (из напоминания)
    if query.data == "accounts_check_start":
        from accounts_check import get_poster_balances, ACCOUNTS_TO_CHECK

        await query.edit_message_text("📊 Загружаю данные из Poster...")
        try:
            # Получаем балансы из Poster (без показа пользователю)
            poster_balances = await get_poster_balances(update.effective_user.id)

            # Сохраняем в context для последующих шагов
            context.user_data['accounts_check'] = {
                'step': 0,  # 0=Закуп, 1=Kaspi, 2=Халык
                'poster_balances': poster_balances,
                'actual_balances': {}
            }

            # Запрашиваем первый счет
            await context.bot.send_message(
                chat_id=update.effective_user.id,
                text="💵 Введите фактический остаток:\n\n"
                     "**Оставил в кассе (на закупы)**\n\n"
                     "Просто напишите число, например: 127500",
                reply_markup=ReplyKeyboardMarkup(
                    [[KeyboardButton("❌ Отмена")]],
                    resize_keyboard=True
                ),
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Accounts check failed: {e}", exc_info=True)
            await context.bot.send_message(
                chat_id=update.effective_user.id,
                text=f"❌ Ошибка: {str(e)[:200]}"
            )
        return

    # Обработка диалога расчета зарплат
    if query.data == "salary_flow_start":
        await salary_flow_handlers.handle_salary_flow_start(update, context)
        return
    elif query.data == "salary_flow_cashiers_2":
        await salary_flow_handlers.handle_salary_flow_cashiers(update, context, 2)
        return
    elif query.data == "salary_flow_cashiers_3":
        await salary_flow_handlers.handle_salary_flow_cashiers(update, context, 3)
        return
    elif query.data == "salary_flow_assistant_10":
        await salary_flow_handlers.handle_assistant_time(update, context, "10:00")
        return
    elif query.data == "salary_flow_assistant_12":
        await salary_flow_handlers.handle_assistant_time(update, context, "12:00")
        return
    elif query.data == "salary_flow_assistant_14":
        await salary_flow_handlers.handle_assistant_time(update, context, "14:00")
        return

    # Обработка пропущенных ежедневных транзакций
    if query.data.startswith("create_missed_daily_"):
        telegram_user_id = int(query.data.split("_")[-1])
        await query.edit_message_text("⏳ Создаю ежедневные транзакции...")

        try:
            scheduler = DailyTransactionScheduler(telegram_user_id)
            result = await scheduler.create_daily_transactions()

            if result.get('already_exists'):
                await query.edit_message_text(
                    "✅ Транзакции уже были созданы ранее. Дубли не созданы.",
                    parse_mode='Markdown'
                )
            elif result['success']:
                await query.edit_message_text(
                    f"✅ *Транзакции успешно созданы*\n\n"
                    f"Создано транзакций: {result['count']}\n\n"
                    f"Вы можете проверить их в Poster.",
                    parse_mode='Markdown'
                )
            else:
                await query.edit_message_text(
                    f"❌ *Ошибка создания транзакций*\n\n"
                    f"Ошибка: {result.get('error')}",
                    parse_mode='Markdown'
                )
        except Exception as e:
            logger.error(f"Ошибка создания пропущенных транзакций: {e}", exc_info=True)
            await query.edit_message_text(
                f"❌ *Произошла ошибка*\n\n"
                f"Не удалось создать транзакции. Попробуйте позже.",
                parse_mode='Markdown'
            )
        return

    elif query.data.startswith("skip_missed_daily_"):
        await query.edit_message_text("✅ Хорошо, транзакции не будут созданы.")
        return

    # Обработка меню кнопок
    if query.data == "close_cash_register":
        await handle_close_cash_register_callback(update, context)
        return
    elif query.data.startswith("close_cash_dept:"):
        # Выбран отдел для закрытия
        dept = query.data.split(":")[1]
        await handle_cash_closing_start(update, context, dept)
        return
    elif query.data.startswith("cash_input:"):
        # Ввод данных для закрытия кассы
        await handle_cash_input_callback(update, context)
        return
    elif query.data == "cash_closing_confirm":
        # Подтверждение закрытия кассы
        await handle_cash_closing_confirm(update, context)
        return
    elif query.data == "cash_closing_cancel":
        context.user_data.pop('cash_closing_data', None)
        await query.edit_message_text("❌ Закрытие кассы отменено.")
        return
    elif query.data == "close_shift":
        await handle_close_shift_callback(update, context)
        return
    elif query.data == "close_shift_2":
        await handle_close_shift_with_count_callback(update, context, 2)
        return
    elif query.data == "close_shift_3":
        await handle_close_shift_with_count_callback(update, context, 3)
        return
    elif query.data == "cancel_shift_closing":
        await query.edit_message_text("✖️ Закрытие смены отменено.")
        return
    elif query.data == "calculate_salaries":
        await handle_calculate_salaries_callback(update, context)
        return
    elif query.data == "create_daily_transactions":
        await handle_create_daily_transactions_callback(update, context)
        return
    elif query.data == "generate_weekly_report":
        await handle_generate_weekly_report_callback(update, context)
        return
    elif query.data == "generate_monthly_report":
        await handle_generate_monthly_report_callback(update, context)
        return
    elif query.data == "cashiers_2":
        await handle_cashiers_count_callback(update, context, 2)
        return
    elif query.data == "cashiers_3":
        await handle_cashiers_count_callback(update, context, 3)
        return
    elif query.data == "assistant_time_10":
        await handle_assistant_time_and_calculate(update, context, "10:00")
        return
    elif query.data == "assistant_time_12":
        await handle_assistant_time_and_calculate(update, context, "12:00")
        return
    elif query.data == "assistant_time_14":
        await handle_assistant_time_and_calculate(update, context, "14:00")
        return

    if query.data == "confirm":
        await confirm_transaction(update, context)
    elif query.data == "cancel":
        context.user_data.clear()
        await query.edit_message_text("✖️ Транзакция отменена.")
    elif query.data == "change_account":
        await show_account_selection(update, context, 'from')
    elif query.data == "change_account_from":
        await show_account_selection(update, context, 'from')
    elif query.data == "change_account_to":
        await show_account_selection(update, context, 'to')
    elif query.data == "change_supplier":
        await show_supplier_selection_for_draft(update, context)
    elif query.data.startswith("select_account_"):
        account_id = int(query.data.replace("select_account_", ""))
        await update_account_in_draft(update, context, account_id)
    elif query.data.startswith("select_supplier:"):
        # Supplier selection for new supply (before draft)
        supplier_id = int(query.data.split(":")[1])
        await handle_supplier_selection(update, context, supplier_id)
    elif query.data == "cancel_supplier_selection":
        context.user_data.clear()
        await query.edit_message_text("✖️ Выбор поставщика отменён.")
    elif query.data.startswith("select_supplier_"):
        supplier_id = int(query.data.replace("select_supplier_", ""))
        await update_supplier_in_draft(update, context, supplier_id)
    elif query.data == "back_to_draft":
        await show_draft_again(update, context)
    elif query.data.startswith("select_ingredient_"):
        ingredient_id = int(query.data.replace("select_ingredient_", ""))
        await handle_ingredient_selection(update, context, ingredient_id)
    elif query.data == "skip_ingredient":
        await handle_ingredient_skip(update, context)
    elif query.data == "manual_ingredient_search":
        await start_manual_ingredient_search(update, context)
    elif query.data == "back_to_suggestions":
        # Clear manual search flag and show original suggestions
        context.user_data['waiting_for_manual_ingredient'] = False
        await show_ingredient_selection(update, context)
    elif query.data.startswith("edit_item:"):
        # Edit item in draft
        item_index = int(query.data.split(":")[1])
        await show_item_edit_menu(update, context, item_index)
    elif query.data.startswith("change_item_ingredient:"):
        item_index = int(query.data.split(":")[1])
        await start_ingredient_change(update, context, item_index)
    elif query.data.startswith("change_item_qty:"):
        item_index = int(query.data.split(":")[1])
        await start_quantity_change(update, context, item_index)
    elif query.data.startswith("change_item_price:"):
        item_index = int(query.data.split(":")[1])
        await start_price_change(update, context, item_index)
    elif query.data.startswith("delete_item:"):
        item_index = int(query.data.split(":")[1])
        await delete_item_from_draft(update, context, item_index)
    elif query.data.startswith("select_new_ingredient:"):
        # User selected new ingredient for item
        parts = query.data.split(":")
        item_index = int(parts[1])
        ingredient_id = int(parts[2])
        await update_item_ingredient(update, context, item_index, ingredient_id)
    elif query.data.startswith("search_ingredient_for_item:"):
        # User wants to search for ingredient manually
        item_index = int(query.data.split(":")[1])
        context.user_data['editing_ingredient_for_item'] = item_index
        await query.edit_message_text(
            "🔍 Введите название ингредиента:\n\n"
            "Например: чеддер весовой, пломбир, молоко и т.д."
        )
    elif query.data.startswith("confirm_supply:"):
        # Confirm supply by ID
        supply_id = int(query.data.split(":")[1])
        await handle_confirm_supply_callback(update, context, supply_id)
    elif query.data == "cancel_supply":
        await query.edit_message_text("❌ Подтверждение поставки отменено.\n\nЧерновик остался в системе.")
        return
    elif query.data.startswith("change_supplier_for_supply:"):
        # Change supplier for supply
        supply_id = int(query.data.split(":")[1])
        await handle_change_supplier_for_supply_callback(update, context, supply_id)
    elif query.data.startswith("select_supplier:"):
        # Select supplier from list
        parts = query.data.split(":")
        supply_id = int(parts[1])
        supplier_id = int(parts[2])
        await handle_select_supplier_callback(update, context, supply_id, supplier_id)
    # Shipment template callbacks
    elif query.data == "save_as_template":
        # Save current draft as template
        message_id = context.user_data.get('current_message_id')
        drafts = context.user_data.get('drafts', {})
        draft = drafts.get(message_id)
        if draft and draft.get('type') == 'supply':
            await save_draft_as_template(update, context, draft)
        else:
            await query.answer("❌ Черновик не найден", show_alert=True)
    elif query.data.startswith("edit_template:"):
        template_name = query.data.split(":", 1)[1]
        await handle_edit_template_callback(update, context, template_name)
    elif query.data.startswith("delete_template:"):
        template_name = query.data.split(":", 1)[1]
        await handle_delete_template_callback(update, context, template_name)
    elif query.data.startswith("confirm_delete_template:"):
        template_name = query.data.split(":", 1)[1]
        await handle_confirm_delete_template_callback(update, context, template_name)
    elif query.data.startswith("edit_template_prices:"):
        template_name = query.data.split(":", 1)[1]
        await handle_edit_template_prices_callback(update, context, template_name)


async def show_item_edit_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, item_index: int):
    """Show edit menu for a specific item in draft"""
    query = update.callback_query

    # Get draft
    message_id = context.user_data.get('current_message_id')
    if not message_id:
        await query.edit_message_text("❌ Черновик не найден.")
        return

    drafts = context.user_data.get('drafts', {})
    draft = drafts.get(message_id)

    if not draft or draft.get('type') != 'supply':
        await query.edit_message_text("❌ Черновик поставки не найден.")
        return

    if item_index >= len(draft['items']):
        await query.edit_message_text("❌ Товар не найден.")
        return

    item = draft['items'][item_index]

    # Store current item index for editing
    context.user_data['editing_item_index'] = item_index

    message_text = (
        f"✏️ Редактирование товара:\n\n"
        f"📦 {item['name']}\n"
        f"Количество: {item['num']}\n"
        f"Цена: {item['price']:,} {CURRENCY}\n"
        f"Сумма: {item['sum']:,} {CURRENCY}\n\n"
        f"Выберите что изменить:"
    )

    keyboard = [
        [
            InlineKeyboardButton("🔄 Изменить ингредиент", callback_data=f"change_item_ingredient:{item_index}")
        ],
        [
            InlineKeyboardButton("📊 Изменить количество", callback_data=f"change_item_qty:{item_index}"),
            InlineKeyboardButton("💰 Изменить цену", callback_data=f"change_item_price:{item_index}")
        ],
        [
            InlineKeyboardButton("🗑️ Удалить товар", callback_data=f"delete_item:{item_index}")
        ],
        [
            InlineKeyboardButton("« Назад к черновику", callback_data="back_to_draft")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(message_text, reply_markup=reply_markup)


async def delete_item_from_draft(update: Update, context: ContextTypes.DEFAULT_TYPE, item_index: int):
    """Delete item from draft"""
    query = update.callback_query

    message_id = context.user_data.get('current_message_id')
    drafts = context.user_data.get('drafts', {})
    draft = drafts.get(message_id)

    if not draft or item_index >= len(draft['items']):
        await query.edit_message_text("❌ Ошибка: товар не найден.")
        return

    # Remove item
    removed_item = draft['items'].pop(item_index)
    draft['total_amount'] -= removed_item['sum']

    # Update draft
    drafts[message_id] = draft
    context.user_data['drafts'] = drafts

    await query.answer(f"Удалено: {removed_item['name']}")

    # Show updated draft with edit buttons
    await show_supply_draft_edit(query, context, draft)


async def start_ingredient_change(update: Update, context: ContextTypes.DEFAULT_TYPE, item_index: int):
    """Show ingredient selection for changing item"""
    query = update.callback_query
    telegram_user_id = update.effective_user.id

    message_id = context.user_data.get('current_message_id')
    drafts = context.user_data.get('drafts', {})
    draft = drafts.get(message_id)

    if not draft or item_index >= len(draft['items']):
        await query.edit_message_text("❌ Ошибка: товар не найден.")
        return

    item = draft['items'][item_index]

    # Get ingredient suggestions based on ORIGINAL name (from voice input)
    # This ensures we search based on what user said, not what was incorrectly matched
    ingredient_matcher = get_ingredient_matcher(telegram_user_id)
    search_name = item.get('original_name', item['name'])  # Fallback to current name if no original
    suggestions = ingredient_matcher.get_top_matches(search_name, limit=6, score_cutoff=60)

    message_text = (
        f"🔄 Изменение ингредиента:\n\n"
        f"Текущий: {item['name']}\n"
    )

    # Show original name if different from current
    if item.get('original_name') and item['original_name'] != item['name']:
        message_text += f"Распознано как: \"{item['original_name']}\"\n"

    message_text += "\nВыберите новый ингредиент:"

    keyboard = []
    for ing_id, ing_name, ing_unit, score in suggestions:
        keyboard.append([InlineKeyboardButton(
            f"{ing_name} ({score}%)",
            callback_data=f"select_new_ingredient:{item_index}:{ing_id}"
        )])

    # Add search button
    keyboard.append([InlineKeyboardButton("🔍 Поиск по названию", callback_data=f"search_ingredient_for_item:{item_index}")])
    keyboard.append([InlineKeyboardButton("« Назад", callback_data=f"edit_item:{item_index}")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(message_text, reply_markup=reply_markup)


async def handle_item_ingredient_search_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle user input for manual ingredient search when editing item"""
    item_index = context.user_data.pop('editing_ingredient_for_item')
    telegram_user_id = update.effective_user.id
    text = update.message.text.strip()

    # Search for ingredients
    ingredient_matcher = get_ingredient_matcher(telegram_user_id)
    suggestions = ingredient_matcher.get_top_matches(text, limit=6, score_cutoff=60)

    if not suggestions:
        await update.message.reply_text(
            f"❌ Ингредиенты не найдены по запросу: \"{text}\"\n\n"
            "Попробуйте другое название или добавьте ингредиент в Poster."
        )
        return

    # Show suggestions
    message_text = f"🔍 Найдено по запросу \"{text}\":\n\nВыберите ингредиент:"

    keyboard = []
    for ing_id, ing_name, ing_unit, score in suggestions:
        keyboard.append([InlineKeyboardButton(
            f"{ing_name} ({score}%)",
            callback_data=f"select_new_ingredient:{item_index}:{ing_id}"
        )])

    # Add back button
    keyboard.append([InlineKeyboardButton("« Назад", callback_data=f"edit_item:{item_index}")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(message_text, reply_markup=reply_markup)


async def update_item_ingredient(update: Update, context: ContextTypes.DEFAULT_TYPE, item_index: int, ingredient_id: int):
    """Update ingredient for an item in draft"""
    query = update.callback_query
    telegram_user_id = update.effective_user.id

    message_id = context.user_data.get('current_message_id')
    drafts = context.user_data.get('drafts', {})
    draft = drafts.get(message_id)

    if not draft or item_index >= len(draft['items']):
        await query.edit_message_text("❌ Ошибка: товар не найден.")
        return

    item = draft['items'][item_index]

    # Get ingredient info
    ingredient_matcher = get_ingredient_matcher(telegram_user_id)
    ingredient_info = ingredient_matcher.get_ingredient_info(ingredient_id)

    if not ingredient_info:
        await query.edit_message_text("❌ Ошибка: ингредиент не найден.")
        return

    # Auto-learning: Create alias from original name if available
    alias_created = False
    original_name = item.get('original_name', '').strip()
    new_name = ingredient_info['name']

    # Check conditions for creating alias
    if original_name and len(original_name) >= 3:
        # Normalize both names for comparison
        from matchers import normalize_text_for_matching
        original_normalized = normalize_text_for_matching(original_name)
        new_normalized = normalize_text_for_matching(new_name)

        # Only create alias if names are different
        if original_normalized != new_normalized:
            try:
                success = ingredient_matcher.add_alias(
                    alias_text=original_name,
                    ingredient_id=ingredient_id,
                    notes='Auto-learned from user correction'
                )
                if success:
                    alias_created = True
                    logger.info(f"📚 Auto-created alias: '{original_name}' -> {ingredient_id} ({new_name})")
            except Exception as e:
                logger.error(f"Failed to auto-create alias: {e}")
                # Don't fail the main operation if alias creation fails

    # Update item
    draft['items'][item_index]['id'] = ingredient_id
    draft['items'][item_index]['name'] = ingredient_info['name']
    draft['items'][item_index]['match_score'] = 100  # User confirmed

    # Save draft
    drafts[message_id] = draft
    context.user_data['drafts'] = drafts

    # Notify user with alias info if created
    notification = f"Изменено на: {ingredient_info['name']}"
    if alias_created:
        notification += "\n📚 Alias сохранён для будущих распознаваний"

    await query.answer(notification)

    # Show full draft with edit buttons for all items
    await show_supply_draft_edit(query, context, draft)


async def start_quantity_change(update: Update, context: ContextTypes.DEFAULT_TYPE, item_index: int):
    """Start quantity change flow"""
    query = update.callback_query

    message_id = context.user_data.get('current_message_id')
    drafts = context.user_data.get('drafts', {})
    draft = drafts.get(message_id)

    if not draft or item_index >= len(draft['items']):
        await query.edit_message_text("❌ Ошибка: товар не найден.")
        return

    item = draft['items'][item_index]

    # Set flag for text input
    context.user_data['waiting_for_quantity_change'] = item_index

    await query.edit_message_text(
        f"📊 Изменение количества:\n\n"
        f"Товар: {item['name']}\n"
        f"Текущее количество: {item['num']}\n\n"
        f"Отправьте новое количество (например: 5 или 2.5):"
    )


async def start_price_change(update: Update, context: ContextTypes.DEFAULT_TYPE, item_index: int):
    """Start price change flow"""
    query = update.callback_query

    message_id = context.user_data.get('current_message_id')
    drafts = context.user_data.get('drafts', {})
    draft = drafts.get(message_id)

    if not draft or item_index >= len(draft['items']):
        await query.edit_message_text("❌ Ошибка: товар не найден.")
        return

    item = draft['items'][item_index]

    # Set flag for text input
    context.user_data['waiting_for_price_change'] = item_index

    await query.edit_message_text(
        f"💰 Изменение цены:\n\n"
        f"Товар: {item['name']}\n"
        f"Текущая цена: {item['price']:,} {CURRENCY}\n\n"
        f"Отправьте новую цену (например: 5000):"
    )


async def show_account_selection(update: Update, context: ContextTypes.DEFAULT_TYPE, direction: str = 'from'):
    """Show account selection buttons"""
    query = update.callback_query
    telegram_user_id = update.effective_user.id

    # Get draft by message_id
    message_id = query.message.message_id
    drafts = context.user_data.get('drafts', {})
    draft = drafts.get(message_id)

    if not draft:
        await query.edit_message_text("❌ Черновик не найден.")
        return

    # Store direction and message_id in context
    context.user_data['account_direction'] = direction
    context.user_data['current_message_id'] = message_id

    # Get available accounts
    account_matcher = get_account_matcher(telegram_user_id)
    accounts = account_matcher.accounts

    # Create keyboard with account buttons (2 per row)
    keyboard = []
    row = []
    for account_id, account_info in accounts.items():
        button = InlineKeyboardButton(
            f"{account_info['name']}",
            callback_data=f"select_account_{account_id}"
        )
        row.append(button)
        if len(row) == 2:
            keyboard.append(row)
            row = []

    if row:  # Add remaining buttons
        keyboard.append(row)

    # Add back button
    keyboard.append([InlineKeyboardButton("« Назад", callback_data="back_to_draft")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    prompt = "💰 Выберите счёт для списания:" if direction == 'from' else "💰 Выберите счёт для зачисления:"
    await query.edit_message_text(prompt, reply_markup=reply_markup)


async def update_account_in_draft(update: Update, context: ContextTypes.DEFAULT_TYPE, account_id: int):
    """Update account in draft and show draft again"""
    query = update.callback_query
    telegram_user_id = update.effective_user.id

    # Get draft by stored message_id
    message_id = context.user_data.get('current_message_id')
    if not message_id:
        await query.edit_message_text("❌ Черновик не найден.")
        return

    drafts = context.user_data.get('drafts', {})
    draft = drafts.get(message_id)
    direction = context.user_data.get('account_direction', 'from')

    if not draft:
        await query.edit_message_text("❌ Черновик не найден.")
        return

    # Update account
    account_matcher = get_account_matcher(telegram_user_id)
    account_name = account_matcher.get_account_name(account_id)

    # Check draft type
    draft_type = draft.get('type')

    if draft_type == 'supply':
        # For supply, update account_id and account_name
        draft['account_id'] = account_id
        draft['account_name'] = account_name
    elif direction == 'from':
        draft['account_from_id'] = account_id
        draft['account_from_name'] = account_name
    elif direction == 'to':
        draft['account_to_id'] = account_id
        draft['account_to_name'] = account_name

    # Update draft in storage
    drafts[message_id] = draft
    context.user_data['drafts'] = drafts

    # Determine draft type and show appropriate view
    draft_type = draft.get('type')

    if draft_type == 'supply':
        # Show supply draft
        items_lines = []
        for item in draft['items']:
            line = f"• {item['name']}: {item['num']} × {item['price']} = {item['sum']:,} {CURRENCY}"
            # Add original name from invoice if available
            if item.get('original_name'):
                line += f"\n   _из накладной: {item['original_name']}_"
            items_lines.append(line)

        items_text = "\n".join(items_lines)

        message = (
            f"📦 Черновик поставки:\n\n"
            f"Поставщик: {draft['supplier_name']}\n"
            f"Счёт: {draft['account_name']}\n"
            f"Склад: {draft['storage_name']}\n\n"
            f"Товары:\n{items_text}\n\n"
            f"Итого: {draft['total_amount']:,} {CURRENCY}\n"
            f"Дата: {draft['date']}\n"
        )

        keyboard = [
            [
                InlineKeyboardButton("✅ Подтвердить", callback_data="confirm"),
            ],
            [
                InlineKeyboardButton("🏪 Изменить поставщика", callback_data="change_supplier"),
                InlineKeyboardButton("💰 Изменить счёт", callback_data="change_account")
            ],
            [
                InlineKeyboardButton("❌ Отмена", callback_data="cancel")
            ]
        ]

    elif draft_type == 2:  # Transfer
        # Show transfer draft
        message = (
            "🔄 Черновик перевода:\n\n"
            f"Откуда: {draft['account_from_name']}\n"
            f"Куда: {draft['account_to_name']}\n"
            f"Сумма: {draft['amount']:,} {CURRENCY}\n"
            f"Комментарий: {draft['comment'] or '—'}\n"
            f"Дата: {draft['date']}\n"
        )

        keyboard = [
            [
                InlineKeyboardButton("✅ Подтвердить", callback_data="confirm"),
            ],
            [
                InlineKeyboardButton("📤 Изменить откуда", callback_data="change_account_from"),
                InlineKeyboardButton("📥 Изменить куда", callback_data="change_account_to")
            ],
            [
                InlineKeyboardButton("❌ Отмена", callback_data="cancel")
            ]
        ]

    elif draft_type == 'multiple_expenses':
        # Show multiple expenses draft
        transactions_text = "\n".join([
            f"  • {txn['category_name']}: {txn['amount']:,} {CURRENCY} ({txn['comment'] or '—'})"
            for txn in draft['transactions']
        ])

        message = (
            "💸 Черновик множественных транзакций:\n\n"
            f"Счёт: {draft['account_from_name']}\n"
            f"Количество транзакций: {len(draft['transactions'])}\n\n"
            f"Транзакции:\n{transactions_text}\n\n"
            f"Итого: {draft['total_amount']:,} {CURRENCY}\n"
            f"Дата: {draft['date']}\n"
        )

        keyboard = [
            [
                InlineKeyboardButton("✅ Подтвердить всё", callback_data="confirm"),
            ],
            [
                InlineKeyboardButton("💰 Изменить счёт", callback_data="change_account")
            ],
            [
                InlineKeyboardButton("❌ Отмена", callback_data="cancel")
            ]
        ]

    else:  # Expense (type == 0)
        # Show expense draft
        message = (
            "💸 Черновик транзакции:\n\n"
            f"Категория: {draft['category_name']}\n"
            f"Сумма: {draft['amount']:,} {CURRENCY}\n"
            f"Счёт: {draft['account_from_name']}\n"
            f"Комментарий: {draft['comment'] or '—'}\n"
            f"Дата: {draft['date']}\n"
        )

        keyboard = [
            [
                InlineKeyboardButton("✅ Подтвердить", callback_data="confirm"),
                InlineKeyboardButton("💰 Изменить счёт", callback_data="change_account")
            ],
            [
                InlineKeyboardButton("❌ Отмена", callback_data="cancel")
            ]
        ]

    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(message, reply_markup=reply_markup)


async def show_supplier_selection_for_draft(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show supplier selection buttons for draft editing"""
    query = update.callback_query
    telegram_user_id = update.effective_user.id

    # Get draft by message_id
    message_id = query.message.message_id
    drafts = context.user_data.get('drafts', {})
    draft = drafts.get(message_id)

    if not draft:
        await query.edit_message_text("❌ Черновик не найден.")
        return

    # Store message_id in context
    context.user_data['current_message_id'] = message_id

    # Get available suppliers
    supplier_matcher = get_supplier_matcher(telegram_user_id)
    suppliers = supplier_matcher.suppliers

    # Create keyboard with supplier buttons (2 per row)
    keyboard = []
    row = []
    for supplier_id, supplier_info in suppliers.items():
        button = InlineKeyboardButton(
            f"{supplier_info['name']}",
            callback_data=f"select_supplier_{supplier_id}"
        )
        row.append(button)
        if len(row) == 2:
            keyboard.append(row)
            row = []

    if row:  # Add remaining buttons
        keyboard.append(row)

    # Add back button
    keyboard.append([InlineKeyboardButton("« Назад", callback_data="back_to_draft")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("🏪 Выберите поставщика:", reply_markup=reply_markup)


async def update_supplier_in_draft(update: Update, context: ContextTypes.DEFAULT_TYPE, supplier_id: int):
    """Update supplier in draft and show draft again"""
    query = update.callback_query
    telegram_user_id = update.effective_user.id

    # Get draft by stored message_id
    message_id = context.user_data.get('current_message_id')
    if not message_id:
        await query.edit_message_text("❌ Черновик не найден.")
        return

    drafts = context.user_data.get('drafts', {})
    draft = drafts.get(message_id)

    if not draft:
        await query.edit_message_text("❌ Черновик не найден.")
        return

    # Update supplier
    supplier_matcher = get_supplier_matcher(telegram_user_id)
    supplier_name = supplier_matcher.get_supplier_name(supplier_id)

    draft['supplier_id'] = supplier_id
    draft['supplier_name'] = supplier_name

    # Update draft in storage
    drafts[message_id] = draft
    context.user_data['drafts'] = drafts

    # Show supply draft again
    items_lines = []
    for item in draft['items']:
        line = f"• {item['name']}: {item['num']} × {item['price']} = {item['sum']:,} {CURRENCY}"
        # Add original name from invoice if available
        if item.get('original_name'):
            line += f"\n   _из накладной: {item['original_name']}_"
        items_lines.append(line)

    items_text = "\n".join(items_lines)

    message_text = (
        f"📦 Черновик поставки:\n\n"
        f"Поставщик: {draft['supplier_name']}\n"
        f"Счёт: {draft['account_name']}\n"
        f"Склад: {draft['storage_name']}\n\n"
        f"Товары:\n{items_text}\n\n"
        f"Итого: {draft['total_amount']:,} {CURRENCY}\n"
        f"Дата: {draft['date']}\n"
    )

    keyboard = [
        [
            InlineKeyboardButton("✅ Подтвердить", callback_data="confirm"),
        ],
        [
            InlineKeyboardButton("🏪 Изменить поставщика", callback_data="change_supplier"),
            InlineKeyboardButton("💰 Изменить счёт", callback_data="change_account")
        ],
        [
            InlineKeyboardButton("❌ Отмена", callback_data="cancel")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(message_text, reply_markup=reply_markup)


async def show_draft_again(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show draft again after going back from selection"""
    query = update.callback_query

    # Get draft by stored message_id
    message_id = context.user_data.get('current_message_id')
    if not message_id:
        await query.edit_message_text("❌ Черновик не найден.")
        return

    drafts = context.user_data.get('drafts', {})
    draft = drafts.get(message_id)

    if not draft:
        await query.edit_message_text("❌ Черновик не найден.")
        return

    # Determine draft type and show appropriate view
    draft_type = draft.get('type')

    if draft_type == 'supply':
        # Show supply draft
        items_lines = []
        for item in draft['items']:
            line = f"• {item['name']}: {item['num']} × {item['price']} = {item['sum']:,} {CURRENCY}"
            # Add original name from invoice if available
            if item.get('original_name'):
                line += f"\n   _из накладной: {item['original_name']}_"
            items_lines.append(line)

        items_text = "\n".join(items_lines)

        message_text = (
            f"📦 Черновик поставки:\n\n"
            f"Поставщик: {draft['supplier_name']}\n"
            f"Счёт: {draft['account_name']}\n"
            f"Склад: {draft['storage_name']}\n\n"
            f"Товары:\n{items_text}\n\n"
            f"Итого: {draft['total_amount']:,} {CURRENCY}\n"
            f"Дата: {draft['date']}\n"
        )

        keyboard = [
            [
                InlineKeyboardButton("✅ Подтвердить", callback_data="confirm"),
            ],
            [
                InlineKeyboardButton("🏪 Изменить поставщика", callback_data="change_supplier"),
                InlineKeyboardButton("💰 Изменить счёт", callback_data="change_account")
            ],
            [
                InlineKeyboardButton("❌ Отмена", callback_data="cancel")
            ]
        ]

    elif draft_type == 2:  # Transfer
        message_text = (
            "🔄 Черновик перевода:\n\n"
            f"Откуда: {draft['account_from_name']}\n"
            f"Куда: {draft['account_to_name']}\n"
            f"Сумма: {draft['amount']:,} {CURRENCY}\n"
            f"Комментарий: {draft['comment'] or '—'}\n"
            f"Дата: {draft['date']}\n"
        )

        keyboard = [
            [
                InlineKeyboardButton("✅ Подтвердить", callback_data="confirm"),
            ],
            [
                InlineKeyboardButton("📤 Изменить откуда", callback_data="change_account_from"),
                InlineKeyboardButton("📥 Изменить куда", callback_data="change_account_to")
            ],
            [
                InlineKeyboardButton("❌ Отмена", callback_data="cancel")
            ]
        ]

    else:  # Expense
        message_text = (
            "💸 Черновик транзакции:\n\n"
            f"Категория: {draft['category_name']}\n"
            f"Сумма: {draft['amount']:,} {CURRENCY}\n"
            f"Счёт: {draft['account_from_name']}\n"
            f"Комментарий: {draft['comment'] or '—'}\n"
            f"Дата: {draft['date']}\n"
        )

        keyboard = [
            [
                InlineKeyboardButton("✅ Подтвердить", callback_data="confirm"),
                InlineKeyboardButton("💰 Изменить счёт", callback_data="change_account")
            ],
            [
                InlineKeyboardButton("❌ Отмена", callback_data="cancel")
            ]
        ]

    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(message_text, reply_markup=reply_markup)


async def confirm_transaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirm and create transaction/supply in Poster"""
    query = update.callback_query

    # Get draft by message_id
    message_id = query.message.message_id
    drafts = context.user_data.get('drafts', {})
    draft = drafts.get(message_id)

    if not draft:
        await query.edit_message_text("❌ Черновик не найден.")
        logger.warning(f"Draft not found for message_id={message_id}, available: {list(drafts.keys())}")
        return

    try:
        telegram_user_id = update.effective_user.id
        poster = get_poster_client(telegram_user_id)

        # Check if it's a supply
        if draft.get('type') == 'supply':
            await query.edit_message_text("⏳ Создаю поставку в Poster...")

            # Объединить дубликаты ингредиентов (по id) перед отправкой в API
            ingredients_dict = {}
            for item in draft['items']:
                item_id = item['id']
                if item_id in ingredients_dict:
                    # Дубликат - складываем количество
                    ingredients_dict[item_id]['num'] += item['num']
                else:
                    # Новый ингредиент - только нужные поля
                    # item_type: 'ingredient', 'semi_product', or 'product'
                    ingredient_data = {
                        'id': item_id,
                        'num': item['num'],
                        'price': item['price']
                    }
                    if item.get('item_type'):
                        ingredient_data['type'] = item['item_type']
                    ingredients_dict[item_id] = ingredient_data

            # Конвертируем в список
            ingredients_for_api = list(ingredients_dict.values())

            supply_id = await poster.create_supply(
                supplier_id=draft['supplier_id'],
                storage_id=draft['storage_id'],
                date=draft['date'],
                ingredients=ingredients_for_api,
                account_id=draft['account_id'],
                comment=""
            )

            # Success message
            items_lines = []
            for item in draft['items']:
                line = f"  • {item['name']}: {item['num']} x {item['price']:,}"
                # Add original name from invoice if available
                if item.get('original_name'):
                    line += f"\n     _из накладной: {item['original_name']}_"
                items_lines.append(line)

            items_text = "\n".join(items_lines)

            await query.edit_message_text(
                f"✅ Поставка создана успешно!\n\n"
                f"ID в Poster: {supply_id}\n"
                f"Поставщик: {draft['supplier_name']}\n"
                f"Счёт: {draft['account_name']}\n\n"
                f"Товары:\n{items_text}\n\n"
                f"Итого: {draft['total_amount']:,} {CURRENCY}"
            )

            # Clear only this draft
            if message_id in drafts:
                del drafts[message_id]
                context.user_data['drafts'] = drafts
            return

        # Check if it's multiple expenses
        if draft.get('type') == 'multiple_expenses':
            await query.edit_message_text(f"⏳ Создаю {len(draft['transactions'])} транзакций в Poster...")

            created_ids = []
            failed_transactions = []

            for txn in draft['transactions']:
                try:
                    transaction_id = await poster.create_transaction(
                        transaction_type=0,  # Expense
                        category_id=txn['category_id'],
                        account_from_id=draft['account_from_id'],
                        amount=txn['amount'],
                        date=draft['date'],
                        comment=txn['comment']
                    )
                    created_ids.append((transaction_id, txn))
                except Exception as e:
                    logger.error(f"Failed to create transaction for {txn['category_name']}: {e}")
                    failed_transactions.append((txn, str(e)))

            # Build success message
            success_text = "\n".join([
                f"  • {txn['category_name']}: {txn['amount']:,} {CURRENCY} (ID: {tid})"
                for tid, txn in created_ids
            ])

            message = f"✅ Создано транзакций: {len(created_ids)}/{len(draft['transactions'])}\n\n"
            message += f"Счёт: {draft['account_from_name']}\n\n"
            message += f"Транзакции:\n{success_text}\n\n"
            message += f"Итого: {sum(txn['amount'] for _, txn in created_ids):,} {CURRENCY}"

            if failed_transactions:
                failed_text = "\n".join([
                    f"  • {txn['category_name']}: {error}"
                    for txn, error in failed_transactions
                ])
                message += f"\n\n❌ Ошибки:\n{failed_text}"

            await query.edit_message_text(message)

            # Clear only this draft
            if message_id in drafts:
                del drafts[message_id]
                context.user_data['drafts'] = drafts
            return

        # Otherwise it's a transaction
        await query.edit_message_text("⏳ Создаю транзакцию в Poster...")

        # Create transaction
        # Note: Amount is already in KZT, no conversion needed
        amount = draft['amount']

        # Check if it's a transfer
        if draft['type'] == 2:
            transaction_id = await poster.create_transaction(
                transaction_type=draft['type'],
                category_id=draft.get('category_id'),  # Can be None for transfers
                account_from_id=draft['account_from_id'],
                account_to_id=draft.get('account_to_id'),
                amount=amount,
                date=draft['date'],
                comment=draft['comment']
            )
        else:
            transaction_id = await poster.create_transaction(
                transaction_type=draft['type'],
                category_id=draft['category_id'],
                account_from_id=draft['account_from_id'],
                amount=amount,
                date=draft['date'],
                comment=draft['comment']
            )

        # Success message
        if draft['type'] == 2:
            await query.edit_message_text(
                f"✅ Перевод создан успешно!\n\n"
                f"ID в Poster: {transaction_id}\n"
                f"Откуда: {draft['account_from_name']}\n"
                f"Куда: {draft['account_to_name']}\n"
                f"Сумма: {draft['amount']:,} {CURRENCY}\n"
                f"Комментарий: {draft['comment']}"
            )
        else:
            await query.edit_message_text(
                f"✅ Транзакция создана успешно!\n\n"
                f"ID в Poster: {transaction_id}\n"
                f"Категория: {draft['category_name']}\n"
                f"Сумма: {draft['amount']:,} {CURRENCY}\n"
                f"Комментарий: {draft['comment']}"
            )

        # Clear only this draft
        if message_id in drafts:
            del drafts[message_id]
            context.user_data['drafts'] = drafts

    except Exception as e:
        logger.error(f"Transaction/supply creation failed: {e}", exc_info=True)
        await query.edit_message_text(
            f"❌ Ошибка создания:\n{str(e)}\n\n"
            f"Проверьте настройки Poster API."
        )


# === Main ===

async def post_init(application: Application) -> None:
    """Set up bot commands after initialization"""
    from telegram import BotCommand, MenuButtonWebApp, WebAppInfo

    commands = [
        BotCommand("menu", "🏠 Главное меню"),
        BotCommand("help", "❓ Помощь"),
        BotCommand("cancel", "❌ Отменить"),
    ]

    await application.bot.set_my_commands(commands)
    logger.info("✅ Bot commands menu set")

    # Установить кнопку Web App (кнопка mini-app справа от поля ввода)
    await application.bot.set_chat_menu_button(
        menu_button=MenuButtonWebApp(
            text="📱 Приложение",
            web_app=WebAppInfo(url=WEBAPP_URL)
        )
    )
    logger.info(f"✅ Web App menu button set: {WEBAPP_URL}")


async def run_daily_transactions_for_user(telegram_user_id: int):
    """
    Выполнить ежедневные транзакции для пользователя
    Вызывается scheduler'ом в 12:00
    """
    try:
        logger.info(f"⏰ Запуск ежедневных транзакций для пользователя {telegram_user_id}")

        scheduler = DailyTransactionScheduler(telegram_user_id)
        result = await scheduler.create_daily_transactions()

        if result['success']:
            logger.info(f"✅ Создано {result['count']} транзакций для пользователя {telegram_user_id}")
        else:
            logger.error(f"❌ Ошибка создания транзакций: {result.get('error')}")

    except Exception as e:
        logger.error(f"❌ Ошибка в run_daily_transactions_for_user: {e}", exc_info=True)


async def run_daily_transactions_cleanup(telegram_user_id: int):
    """
    Очистка дублей ежедневных транзакций.
    Запускается в 13:00 (через 2.5 часа после создания в 10:30),
    чтобы поймать дубли от старых деплоев/инстансов.
    """
    try:
        logger.info(f"🧹 Запуск очистки дублей для пользователя {telegram_user_id}")

        scheduler = DailyTransactionScheduler(telegram_user_id)

        # Очистка дублей с комментариями
        result1 = await scheduler.cleanup_duplicate_daily_transactions()
        # Очистка дублей без комментариев (по category_id)
        result2 = await scheduler.cleanup_no_comment_duplicates()

        total = result1.get('cleaned', 0) + result2.get('cleaned', 0)
        if total > 0:
            logger.info(f"✅ Очищено {total} дублей для пользователя {telegram_user_id}")
        else:
            logger.info(f"✅ Дублей не найдено для пользователя {telegram_user_id}")

    except Exception as e:
        logger.error(f"❌ Ошибка очистки дублей: {e}", exc_info=True)


async def run_weekly_report_for_user(telegram_user_id: int, bot_application):
    """
    Отправить еженедельный отчёт пользователю
    Вызывается scheduler'ом по понедельникам в 12:00
    """
    try:
        from weekly_report import send_weekly_report_to_user
        await send_weekly_report_to_user(telegram_user_id, bot_application)
    except Exception as e:
        logger.error(f"❌ Ошибка отправки еженедельного отчёта пользователю {telegram_user_id}: {e}", exc_info=True)


async def run_monthly_report_for_user(telegram_user_id: int, bot_application):
    """
    Отправить ежемесячный отчёт пользователю
    Вызывается scheduler'ом 1 числа каждого месяца в 12:00
    """
    try:
        from monthly_report import send_monthly_report_to_user
        await send_monthly_report_to_user(telegram_user_id, bot_application)
    except Exception as e:
        logger.error(f"❌ Ошибка отправки ежемесячного отчёта пользователю {telegram_user_id}: {e}", exc_info=True)


async def run_weekly_price_check_for_user(telegram_user_id: int, bot_application):
    """
    Выполнить еженедельную проверку цен для пользователя
    Вызывается scheduler'ом по понедельникам в 9:00
    """
    try:
        logger.info(f"⏰ Запуск еженедельной проверки цен для пользователя {telegram_user_id}")
        from price_monitoring import perform_weekly_price_check
        await perform_weekly_price_check(telegram_user_id, bot_application.bot)
        logger.info(f"✅ Проверка цен завершена для пользователя {telegram_user_id}")
    except Exception as e:
        logger.error(f"❌ Ошибка проверки цен для пользователя {telegram_user_id}: {e}", exc_info=True)


async def check_and_notify_missed_transactions(app: Application):
    """
    Проверить, были ли созданы ежедневные транзакции сегодня
    Если нет и уже после 12:00 - отправить сообщение пользователю с подтверждением
    """
    try:
        kz_tz = pytz.timezone('Asia/Almaty')
        kz_now = datetime.now(kz_tz)

        # Не проверять до 9:30 — cron запускается в 9:00, дадим ему время отработать
        if kz_now.hour < 9 or (kz_now.hour == 9 and kz_now.minute < 30):
            logger.info(f"⏭️ Проверка пропущенных транзакций пропущена: сейчас {kz_now.strftime('%H:%M')} (до 9:30)")
            return

        db = get_database()

        for telegram_user_id in ALLOWED_USER_IDS:
            # Проверить, зарегистрирован ли пользователь в базе данных
            user = db.get_user(telegram_user_id)
            if not user:
                logger.info(f"⚠️ Пользователь {telegram_user_id} не найден в базе данных, пропускаю проверку транзакций")
                continue

            if is_daily_transactions_enabled(telegram_user_id):
                scheduler = DailyTransactionScheduler(telegram_user_id)
                transactions_exist = await scheduler.check_transactions_created_today()

                if not transactions_exist:
                    logger.info(f"⚠️ Ежедневные транзакции не найдены для пользователя {telegram_user_id}. Отправляю уведомление...")

                    # Отправить сообщение с кнопкой подтверждения
                    keyboard = [
                        [
                            InlineKeyboardButton("✅ Да, создать транзакции", callback_data=f"create_missed_daily_{telegram_user_id}"),
                            InlineKeyboardButton("❌ Нет, не нужно", callback_data=f"skip_missed_daily_{telegram_user_id}")
                        ]
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)

                    await app.bot.send_message(
                        chat_id=telegram_user_id,
                        text="⚠️ *Ежедневные транзакции не были созданы сегодня*\n\n"
                             "Возможно, бот был перезапущен после 9:00.\n\n"
                             "Хотите создать транзакции сейчас?",
                        parse_mode='Markdown',
                        reply_markup=reply_markup
                    )

    except Exception as e:
        logger.error(f"❌ Ошибка проверки пропущенных транзакций: {e}", exc_info=True)


# Global reference to keep scheduler alive (prevent garbage collection)
_app_scheduler = None

def setup_scheduler(app: Application):
    """
    Настроить планировщик для автоматических задач
    Запускает ежедневные транзакции в 9:00 по времени Астаны
    """
    global _app_scheduler
    scheduler = AsyncIOScheduler()

    # Часовой пояс Астаны
    astana_tz = pytz.timezone('Asia/Almaty')

    # Для каждого пользователя с включенными авто-транзакциями
    for telegram_user_id in ALLOWED_USER_IDS:
        if is_daily_transactions_enabled(telegram_user_id):
            # Триггер: каждый день в 10:30 по времени Астаны
            trigger = CronTrigger(
                hour=10,
                minute=30,
                timezone=astana_tz
            )

            scheduler.add_job(
                run_daily_transactions_for_user,
                trigger=trigger,
                args=[telegram_user_id],
                id=f'daily_transactions_{telegram_user_id}',
                name=f'Ежедневные транзакции для пользователя {telegram_user_id}',
                replace_existing=True
            )

            logger.info(f"✅ Запланированы ежедневные транзакции для пользователя {telegram_user_id} в 10:30 (Asia/Almaty)")

            # Очистка дублей: каждый день в 13:00 по времени Астаны
            # (через 2.5 часа после создания, чтобы поймать дубли от старых инстансов)
            cleanup_trigger = CronTrigger(
                hour=13,
                minute=0,
                timezone=astana_tz
            )

            scheduler.add_job(
                run_daily_transactions_cleanup,
                trigger=cleanup_trigger,
                args=[telegram_user_id],
                id=f'daily_transactions_cleanup_{telegram_user_id}',
                name=f'Очистка дублей транзакций для пользователя {telegram_user_id}',
                replace_existing=True
            )

            logger.info(f"✅ Запланирована очистка дублей для пользователя {telegram_user_id} в 13:00 (Asia/Almaty)")

    # Еженедельные отчёты для всех активных пользователей по понедельникам в 12:00
    for telegram_user_id in ALLOWED_USER_IDS:
        # Триггер: каждый понедельник в 12:00
        weekly_trigger = CronTrigger(
            day_of_week='mon',  # Понедельник
            hour=12,
            minute=0,
            timezone=astana_tz
        )

        scheduler.add_job(
            run_weekly_report_for_user,
            trigger=weekly_trigger,
            args=[telegram_user_id, app],
            id=f'weekly_report_{telegram_user_id}',
            name=f'Еженедельный отчёт для пользователя {telegram_user_id}',
            replace_existing=True
        )

        logger.info(f"✅ Запланированы еженедельные отчёты для пользователя {telegram_user_id} в Пн 12:00 (Asia/Almaty)")

    # Ежемесячные отчёты для всех активных пользователей 1 числа в 12:00
    for telegram_user_id in ALLOWED_USER_IDS:
        # Триггер: 1 число каждого месяца в 12:00
        monthly_trigger = CronTrigger(
            day=1,  # 1 число месяца
            hour=12,
            minute=0,
            timezone=astana_tz
        )

        scheduler.add_job(
            run_monthly_report_for_user,
            trigger=monthly_trigger,
            args=[telegram_user_id, app],
            id=f'monthly_report_{telegram_user_id}',
            name=f'Ежемесячный отчёт для пользователя {telegram_user_id}',
            replace_existing=True
        )

        logger.info(f"✅ Запланированы ежемесячные отчёты для пользователя {telegram_user_id} 1 числа в 12:00 (Asia/Almaty)")

    # Еженедельная проверка цен для всех активных пользователей по понедельникам в 9:00
    for telegram_user_id in ALLOWED_USER_IDS:
        # Триггер: каждый понедельник в 9:00
        price_check_trigger = CronTrigger(
            day_of_week='mon',  # Понедельник
            hour=9,
            minute=0,
            timezone=astana_tz
        )

        scheduler.add_job(
            run_weekly_price_check_for_user,
            trigger=price_check_trigger,
            args=[telegram_user_id, app],
            id=f'weekly_price_check_{telegram_user_id}',
            name=f'Еженедельная проверка цен для пользователя {telegram_user_id}',
            replace_existing=True
        )

        logger.info(f"✅ Запланирована еженедельная проверка цен для пользователя {telegram_user_id} в Пн 9:00 (Asia/Almaty)")

    # Напоминание о зарплатах (21:30) — убрано, зарплаты считает кассир через свою страницу
    # Сверка счетов (22:30) — убрано, сверка через сайт расходов

    # Запустить scheduler
    scheduler.start()
    _app_scheduler = scheduler  # Store global reference to prevent GC

    # Log all registered jobs for debugging
    jobs = scheduler.get_jobs()
    logger.info(f"✅ Планировщик запущен с {len(jobs)} задачами:")
    for job in jobs:
        logger.info(f"   📋 {job.name} | next run: {job.next_run_time}")

    # Проверить пропущенные транзакции при старте бота
    import asyncio
    loop = asyncio.get_event_loop()
    loop.create_task(check_and_notify_missed_transactions(app))
    logger.info("✅ Проверка пропущенных транзакций запущена")

    return scheduler


async def auto_sync_poster_data(context: ContextTypes.DEFAULT_TYPE, telegram_user_id: int = None):
    """
    Автоматическая синхронизация данных из Poster API

    Args:
        context: Telegram context
        telegram_user_id: Optional user ID to sync for. If None, syncs for all users with accounts.
    """
    logger.info("🔄 Starting automatic sync from Poster API...")

    try:
        # Import sync functions
        from sync_ingredients import sync_ingredients
        from sync_products import sync_products
        from sync_suppliers import sync_suppliers
        from sync_accounts import sync_accounts

        # Determine which users to sync
        if telegram_user_id:
            # Sync for specific user
            user_ids = [telegram_user_id]
        else:
            # Sync for all users with poster accounts
            db = get_database()
            # Get all users from ALLOWED_USER_IDS who have accounts
            user_ids = []
            for user_id in ALLOWED_USER_IDS:
                accounts = db.get_accounts(user_id)
                if accounts:
                    user_ids.append(user_id)

            if not user_ids:
                logger.warning("No users with poster accounts found for sync")
                return

        logger.info(f"📋 Syncing for {len(user_ids)} user(s)...")

        # Sync for each user
        total_ingredients = 0
        total_products = 0

        for user_id in user_ids:
            logger.info(f"  👤 Syncing for user {user_id}...")

            # 1. Синхронизировать ингредиенты
            ingredients_result = await sync_ingredients(telegram_user_id=user_id)
            if isinstance(ingredients_result, tuple):
                ingredients_count, ingredient_map = ingredients_result
                total_ingredients += ingredients_count
            else:
                # Backward compatibility
                ingredients_count = ingredients_result
                total_ingredients += ingredients_count

            # 2. Синхронизировать продукты
            products_result = await sync_products(telegram_user_id=user_id)
            if isinstance(products_result, tuple):
                products_count, product_map = products_result
                total_products += products_count
            else:
                products_count = products_result
                total_products += products_count

        # 3. Синхронизировать поставщиков (пока по старому - без multi-account)
        suppliers_count = await sync_suppliers()

        # 4. Синхронизировать счета (пока по старому - без multi-account)
        accounts_count = await sync_accounts()

        # 6. Перезагрузить matchers (чтобы подхватили новые данные)
        from matchers import _ingredient_matchers, _product_matchers, _category_matchers, _account_matchers, _supplier_matchers

        _ingredient_matchers.clear()
        _product_matchers.clear()
        _category_matchers.clear()
        _account_matchers.clear()
        _supplier_matchers.clear()

        logger.info(
            f"✅ Auto-sync completed successfully: "
            f"Ingredients={total_ingredients}, Products={total_products}, "
            f"Suppliers={suppliers_count}, Accounts={accounts_count}"
        )

        # Уведомление админам отключено (по запросу пользователя)
        # Результаты синхронизации доступны в логах

    except Exception as e:
        logger.error(f"❌ Auto-sync failed: {e}", exc_info=True)
        # Уведомление об ошибке отключено (по запросу пользователя)
        # Ошибки доступны в логах


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global error handler for all bot errors"""
    try:
        logger.error("Exception while handling an update:", exc_info=context.error)

        # Try to get user info
        if isinstance(update, Update):
            user_id = update.effective_user.id if update.effective_user else "Unknown"
            chat_id = update.effective_chat.id if update.effective_chat else None

            # Log detailed error info
            error_msg = f"Error for user {user_id}: {context.error}"
            logger.error(error_msg)

            # Notify user about the error
            if chat_id:
                try:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=f"❌ Произошла ошибка при обработке вашего сообщения.\n\n"
                             f"Пожалуйста, попробуйте еще раз или обратитесь в поддержку.\n\n"
                             f"Ошибка: {str(context.error)[:200]}"
                    )
                except Exception as send_error:
                    logger.error(f"Failed to send error message to user: {send_error}")
        else:
            logger.error(f"Update type: {type(update)}, Error: {context.error}")
    except Exception as e:
        logger.error(f"Error in error_handler: {e}", exc_info=True)


def initialize_application():
    """Initialize and configure the bot application (for both webhook and polling)"""
    # Validate configuration
    validate_config()
    logger.info("✅ Configuration validated")

    # Initialize database (creates tables if needed)
    get_database()

    # Sync ingredients from Poster API if CSV doesn't exist (for Railway)
    sync_ingredients_if_needed()

    # Sync products from Poster API if CSV doesn't exist (for Railway)
    sync_products_if_needed()

    # Fix poster_base_url for existing users (auto-migration)
    fix_user_poster_urls()

    # Migrate CSV aliases to PostgreSQL (one-time auto-migration)
    migrate_csv_aliases_to_db()

    # Create application
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()

    # Register handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("menu", menu_command))
    app.add_handler(CommandHandler("myid", myid_command))
    app.add_handler(CommandHandler("settings", settings_command))
    app.add_handler(CommandHandler("subscription", subscription_command))
    app.add_handler(CommandHandler("daily_transfers", daily_transfers_command))
    app.add_handler(CommandHandler("sync", sync_command))
    app.add_handler(CommandHandler("force_sync", force_sync_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CommandHandler("test_daily", test_daily_command))
    app.add_handler(CommandHandler("cleanup_daily", cleanup_daily_command))
    app.add_handler(CommandHandler("check_ids", check_ids_command))
    app.add_handler(CommandHandler("test_report", test_report_command))
    app.add_handler(CommandHandler("test_monthly", test_monthly_report_command))
    app.add_handler(CommandHandler("check_doner_sales", check_doner_sales_command))
    app.add_handler(CommandHandler("price_check", price_check_command))
    # Cafe access token management
    app.add_handler(CommandHandler("cafe_token", cafe_token_command))

    # Cashier access token management
    app.add_handler(CommandHandler("cashier_token", cashier_token_command))

    # Web user management (login/password auth)
    app.add_handler(CommandHandler("staff", staff_command))

    # Accounts check commands (сверка счетов двух отделов)
    app.add_handler(CommandHandler("accounts_check", accounts_check_command))
    app.add_handler(CommandHandler("check", check_discrepancy_command))

    # Shipment template commands
    app.add_handler(CommandHandler("templates", templates_command))
    app.add_handler(CommandHandler("edit_template", edit_template_command))
    app.add_handler(CommandHandler("delete_template", delete_template_command))

    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.add_handler(CallbackQueryHandler(handle_callback))

    # Register global error handler
    app.add_error_handler(error_handler)

    # Зарегистрировать фоновую задачу автосинхронизации
    from datetime import timedelta
    job_queue = app.job_queue

    # Запуск каждые 24 часа, первый запуск через 1 час
    job_queue.run_repeating(
        auto_sync_poster_data,
        interval=timedelta(hours=24),
        first=timedelta(hours=1),
        name='auto_sync_poster'
    )

    logger.info("✅ Auto-sync job scheduled: every 24 hours, first run in 1 hour")

    # Setup scheduler для автоматических задач
    scheduler = setup_scheduler(app)

    return app


def main():
    """Run the bot (polling mode only - for local development)"""
    try:
        app = initialize_application()

        # Start bot in polling mode
        logger.info("🤖 Poster Helper Bot starting in POLLING mode...")
        logger.info(f"   Allowed users: {ALLOWED_USER_IDS}")

        app.run_polling(allowed_updates=Update.ALL_TYPES)

    except Exception as e:
        logger.error(f"Bot startup failed: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    main()
