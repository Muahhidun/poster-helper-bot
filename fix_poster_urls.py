"""Скрипт для исправления poster_base_url для существующих пользователей"""
import logging
from database import get_database
from config import POSTER_BASE_URL

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def fix_poster_urls():
    """Исправить poster_base_url для всех пользователей с неправильным URL"""
    db = get_database()

    # Получаем всех пользователей
    conn = db._get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT telegram_user_id, poster_base_url FROM users")
    users = cursor.fetchall()

    conn.close()

    logger.info(f"Найдено пользователей: {len(users)}")
    logger.info(f"Правильный URL из конфига: {POSTER_BASE_URL}")

    fixed_count = 0
    for user in users:
        telegram_user_id = user['telegram_user_id'] if hasattr(user, '__getitem__') else user[0]
        current_url = user['poster_base_url'] if hasattr(user, '__getitem__') else user[1]

        # Проверяем нужно ли обновление
        if current_url != POSTER_BASE_URL:
            logger.info(f"Исправляю пользователя {telegram_user_id}:")
            logger.info(f"  Было: {current_url}")
            logger.info(f"  Стало: {POSTER_BASE_URL}")

            success = db.update_user(
                telegram_user_id=telegram_user_id,
                poster_base_url=POSTER_BASE_URL
            )

            if success:
                fixed_count += 1
                logger.info(f"  ✅ Обновлено")
            else:
                logger.error(f"  ❌ Ошибка обновления")
        else:
            logger.info(f"Пользователь {telegram_user_id}: URL уже правильный")

    logger.info(f"\n✅ Обновлено пользователей: {fixed_count}/{len(users)}")


if __name__ == "__main__":
    fix_poster_urls()
