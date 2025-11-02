"""Тестирование активации черновика поставки"""
import asyncio
import logging
from poster_client import get_poster_client
from datetime import datetime

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


async def test_activate_supply():
    """Создать черновик и попробовать активировать"""
    telegram_user_id = 167084307
    poster = get_poster_client(telegram_user_id)

    try:
        # ШАГ 1: Создать черновик поставки (без status или status=0)
        logger.info("="*60)
        logger.info("ШАГ 1: Создание черновика поставки")
        logger.info("="*60)

        supplier_id = 1  # Метро
        storage_id = 1  # Продукты
        account_id = 4  # Kaspi Pay
        date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        ingredients = [{
            'id': 83,  # Картофель фри
            'num': 2,
            'price': 1050
        }]

        supply_id = await poster.create_supply(
            supplier_id=supplier_id,
            storage_id=storage_id,
            date=date,
            ingredients=ingredients,
            account_id=account_id,
            comment="Тест черновика от Claude"
        )

        logger.info(f"✅ Черновик создан: ID={supply_id}")
        print(f"\n✅ Черновик поставки создан: #{supply_id}")

        # ШАГ 2: Попробовать активировать разными способами
        logger.info("="*60)
        logger.info(f"ШАГ 2: Пробую активировать черновик #{supply_id}")
        logger.info("="*60)

        # ВАРИАНТ 1: storage.updateSupply
        try:
            logger.info("\nВАРИАНТ 1: storage.updateSupply")
            result = await poster._request('POST', 'storage.updateSupply', data={
                'supply_id': supply_id,
                'status': 1
            }, use_json=False)
            logger.info(f"✅ ВАРИАНТ 1 СРАБОТАЛ! Результат: {result}")
            print(f"\n✅ ВАРИАНТ 1 (storage.updateSupply) СРАБОТАЛ!")
            return
        except Exception as e:
            logger.warning(f"❌ Вариант 1 не сработал: {e}")

        # ВАРИАНТ 2: supply.updateIncomingOrder
        try:
            logger.info("\nВАРИАНТ 2: supply.updateIncomingOrder")
            result = await poster._request('POST', 'supply.updateIncomingOrder', data={
                'incoming_order_id': supply_id,
                'status': 1
            }, use_json=False)
            logger.info(f"✅ ВАРИАНТ 2 СРАБОТАЛ! Результат: {result}")
            print(f"\n✅ ВАРИАНТ 2 (supply.updateIncomingOrder) СРАБОТАЛ!")
            return
        except Exception as e:
            logger.warning(f"❌ Вариант 2 не сработал: {e}")

        # ВАРИАНТ 3: storage.changeSupplyStatus
        try:
            logger.info("\nВАРИАНТ 3: storage.changeSupplyStatus")
            result = await poster._request('POST', 'storage.changeSupplyStatus', data={
                'supply_id': supply_id,
                'status': 1
            }, use_json=False)
            logger.info(f"✅ ВАРИАНТ 3 СРАБОТАЛ! Результат: {result}")
            print(f"\n✅ ВАРИАНТ 3 (storage.changeSupplyStatus) СРАБОТАЛ!")
            return
        except Exception as e:
            logger.warning(f"❌ Вариант 3 не сработал: {e}")

        # ВАРИАНТ 4: storage.activateSupply
        try:
            logger.info("\nВАРИАНТ 4: storage.activateSupply")
            result = await poster._request('POST', 'storage.activateSupply', data={
                'supply_id': supply_id
            }, use_json=False)
            logger.info(f"✅ ВАРИАНТ 4 СРАБОТАЛ! Результат: {result}")
            print(f"\n✅ ВАРИАНТ 4 (storage.activateSupply) СРАБОТАЛ!")
            return
        except Exception as e:
            logger.warning(f"❌ Вариант 4 не сработал: {e}")

        # ВАРИАНТ 5: storage.confirmSupply
        try:
            logger.info("\nВАРИАНТ 5: storage.confirmSupply")
            result = await poster._request('POST', 'storage.confirmSupply', data={
                'supply_id': supply_id
            }, use_json=False)
            logger.info(f"✅ ВАРИАНТ 5 СРАБОТАЛ! Результат: {result}")
            print(f"\n✅ ВАРИАНТ 5 (storage.confirmSupply) СРАБОТАЛ!")
            return
        except Exception as e:
            logger.warning(f"❌ Вариант 5 не сработал: {e}")

        print(f"\n❌ НИ ОДИН ВАРИАНТ НЕ СРАБОТАЛ!")
        print(f"Черновик #{supply_id} остался в системе, можете проверить его статус в Poster")

    except Exception as e:
        logger.error(f"❌ Ошибка: {e}", exc_info=True)
        print(f"\n❌ Ошибка: {e}")

    finally:
        await poster.close()


if __name__ == "__main__":
    asyncio.run(test_activate_supply())
