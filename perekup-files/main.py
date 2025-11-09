import asyncio
import logging
from datetime import datetime
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import pytz

from src.config import (
    TELEGRAM_BOT_TOKEN,
    ALLOWED_USER_IDS,
    USE_WEBHOOK,
    WEBHOOK_HOST,
    WEBHOOK_PATH,
    DAILY_REPORT_TIME,
    TIMEZONE
)
from src.db.database import init_db, get_db_session
from src.db.models import CapitalOperation, CapitalOperationType
from src.config import INITIAL_CAPITAL
from src.bot.middlewares import AccessMiddleware
from src.bot.handlers import start, project, expense, capital
from src.services.report_service import ReportService

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Инициализация бота и диспетчера
bot = Bot(
    token=TELEGRAM_BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)


async def send_daily_report():
    """Отправка ежедневного отчёта"""
    logger.info("Отправка ежедневного отчёта...")

    try:
        async with get_db_session() as session:
            balance_data = await ReportService.get_capital_balance(session)
            sold_stats = await ReportService.get_sold_projects_stats(session)
            withdrawals = await ReportService.get_withdrawals_by_partner(session)

            report_text = ReportService.format_daily_report(balance_data, sold_stats, withdrawals)

            # Отправляем всем разрешенным пользователям
            for user_id in ALLOWED_USER_IDS:
                try:
                    await bot.send_message(user_id, report_text)
                    logger.info(f"Отчёт отправлен пользователю {user_id}")
                except Exception as e:
                    logger.error(f"Ошибка отправки отчёта пользователю {user_id}: {e}")

    except Exception as e:
        logger.error(f"Ошибка при генерации ежедневного отчёта: {e}")


async def initialize_capital():
    """Инициализация начального капитала (если не существует)"""
    async with get_db_session() as session:
        from sqlalchemy import select
        query = select(CapitalOperation).where(
            CapitalOperation.type == CapitalOperationType.initial
        )
        result = await session.execute(query)
        existing = result.scalar_one_or_none()

        if not existing:
            # Создаем запись о начальном капитале
            initial_op = CapitalOperation(
                date=datetime.now().date(),
                type=CapitalOperationType.initial,
                amount=INITIAL_CAPITAL,
                who="author",
                notes="Начальный капитал",
                created_by=ALLOWED_USER_IDS[0] if ALLOWED_USER_IDS else 0
            )
            session.add(initial_op)
            await session.commit()
            logger.info(f"Создан начальный капитал: {INITIAL_CAPITAL} KZT")


async def setup_scheduler():
    """Настройка планировщика задач"""
    scheduler = AsyncIOScheduler(timezone=pytz.timezone(TIMEZONE))

    # Парсим время из настроек (формат: "22:00")
    hour, minute = map(int, DAILY_REPORT_TIME.split(":"))

    # Добавляем задачу ежедневной отправки отчёта
    scheduler.add_job(
        send_daily_report,
        trigger='cron',
        hour=hour,
        minute=minute,
        id='daily_report',
        replace_existing=True
    )

    scheduler.start()
    logger.info(f"Планировщик запущен. Ежедневный отчёт в {DAILY_REPORT_TIME} ({TIMEZONE})")

    return scheduler


async def on_startup():
    """Действия при запуске бота"""
    logger.info("Инициализация базы данных...")
    await init_db()

    logger.info("Инициализация капитала...")
    await initialize_capital()

    logger.info("Настройка планировщика...")
    scheduler = await setup_scheduler()

    logger.info(f"Бот запущен. Разрешённые пользователи: {ALLOWED_USER_IDS}")

    if USE_WEBHOOK:
        logger.info(f"Режим webhook: {WEBHOOK_HOST}{WEBHOOK_PATH}")
        await bot.set_webhook(f"{WEBHOOK_HOST}{WEBHOOK_PATH}")
    else:
        logger.info("Режим polling")

    return scheduler


async def on_shutdown(scheduler):
    """Действия при остановке бота"""
    logger.info("Остановка бота...")

    if scheduler:
        scheduler.shutdown()

    if USE_WEBHOOK:
        await bot.delete_webhook()

    await bot.session.close()


async def main():
    """Главная функция запуска бота"""
    # Регистрация middleware
    dp.message.middleware(AccessMiddleware())
    dp.callback_query.middleware(AccessMiddleware())

    # Регистрация роутеров
    dp.include_router(start.router)
    dp.include_router(project.router)
    dp.include_router(expense.router)
    dp.include_router(capital.router)

    # Запуск
    scheduler = await on_startup()

    try:
        if USE_WEBHOOK:
            # TODO: Настройка webhook сервера (aiohttp)
            # Для простоты используем polling
            logger.warning("Webhook режим не реализован, используется polling")
            await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
        else:
            await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await on_shutdown(scheduler)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем")
