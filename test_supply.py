"""Тестовый скрипт для создания поставки через Poster API"""
import asyncio
import logging
from poster_client import get_poster_client
from datetime import datetime

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


async def test_create_supply():
    """Создать тестовую поставку"""
    # Используем ваш аккаунт (167084307)
    telegram_user_id = 167084307

    poster = get_poster_client(telegram_user_id)

    try:
        # Данные поставки - как в вашем примере
        supplier_id = 1  # Метро (первый поставщик)
        storage_id = 1  # Продукты
        account_id = 4  # Kaspi Pay
        date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # ТЕСТ 1: Простая поставка с одним ингредиентом
        # Используем ID=83 (Картофель фри, который есть в вашей базе)
        ingredients = [
            {
                'id': 83,  # Картофель фри
                'num': 5,
                'price': 1050
            }
        ]

        logger.info("=" * 60)
        logger.info("ТЕСТ: Создание поставки с одним ингредиентом")
        logger.info(f"Supplier: {supplier_id}, Storage: {storage_id}, Account: {account_id}")
        logger.info(f"Ingredients: {ingredients}")
        logger.info("=" * 60)

        supply_id = await poster.create_supply(
            supplier_id=supplier_id,
            storage_id=storage_id,
            date=date,
            ingredients=ingredients,
            account_id=account_id,
            comment="Тест от Claude Code"
        )

        logger.info("=" * 60)
        logger.info(f"✅ SUCCESS! Supply ID: {supply_id}")
        logger.info("=" * 60)

        print(f"\n✅ Поставка создана успешно!")
        print(f"ID поставки: {supply_id}")
        print(f"Проверьте в Poster: Склад → Приходы → #{supply_id}")
        print(f"\nМожете удалить эту тестовую поставку.")

    except Exception as e:
        logger.error("=" * 60)
        logger.error(f"❌ ERROR: {e}")
        logger.error("=" * 60)
        print(f"\n❌ Ошибка: {e}")

    finally:
        await poster.close()


if __name__ == "__main__":
    asyncio.run(test_create_supply())
