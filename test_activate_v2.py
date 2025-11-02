"""Тестирование storage.updateSupply с полными параметрами"""
import asyncio
import logging
from poster_client import get_poster_client
from datetime import datetime

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


async def test_update_supply():
    """Создать черновик и активировать через storage.updateSupply"""
    telegram_user_id = 167084307
    poster = get_poster_client(telegram_user_id)

    try:
        # ШАГ 1: Создать черновик поставки
        logger.info("="*60)
        logger.info("ШАГ 1: Создание черновика поставки")
        logger.info("="*60)

        supplier_id = 1
        storage_id = 1
        account_id = 4
        date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        ingredients = [{
            'id': 83,
            'num': 3,
            'price': 1050
        }]

        supply_id = await poster.create_supply(
            supplier_id=supplier_id,
            storage_id=storage_id,
            date=date,
            ingredients=ingredients,
            account_id=account_id,
            comment="Тест v2 от Claude"
        )

        logger.info(f"✅ Черновик создан: ID={supply_id}")
        print(f"\n✅ Черновик поставки создан: #{supply_id}")

        # ШАГ 2: Активировать через storage.updateSupply с полными параметрами
        logger.info("="*60)
        logger.info(f"ШАГ 2: Активация через storage.updateSupply")
        logger.info("="*60)

        # Попробуем передать все параметры как в create
        total_amount = sum(int(item['num'] * item['price']) for item in ingredients)

        data = {
            'supply_id': supply_id,
            'date': date,
            'supplier_id': supplier_id,
            'storage_id': storage_id,
            'status': 1,  # АКТИВИРОВАТЬ!
            'type': 1,
            'source': 'manage',
            'supply_comment': 'Тест v2 от Claude - АКТИВИРОВАН'
        }

        # Добавляем ингредиенты
        for idx, item in enumerate(ingredients):
            ingredient_sum = int(item['num'] * item['price'])
            data[f'ingredients[{idx}][id]'] = item['id']
            data[f'ingredients[{idx}][num]'] = item['num']
            data[f'ingredients[{idx}][price]'] = int(item['price'])
            data[f'ingredients[{idx}][ingredient_sum]'] = ingredient_sum
            data[f'ingredients[{idx}][tax_id]'] = 0
            data[f'ingredients[{idx}][packing]'] = 1

        # Добавляем транзакции
        data['transactions[0][transaction_id]'] = ''
        data['transactions[0][account_id]'] = account_id
        data['transactions[0][date]'] = date
        data['transactions[0][amount]'] = total_amount
        data['transactions[0][delete]'] = 0

        logger.info(f"Отправляю storage.updateSupply с параметрами: {data}")

        result = await poster._request('POST', 'storage.updateSupply', data=data, use_json=False)

        logger.info(f"✅ УСПЕХ! Результат: {result}")
        print(f"\n✅ Поставка #{supply_id} АКТИВИРОВАНА!")
        print(f"Проверьте в Poster: Склад → Приходы → #{supply_id}")

    except Exception as e:
        logger.error(f"❌ Ошибка: {e}", exc_info=True)
        print(f"\n❌ Ошибка: {e}")

    finally:
        await poster.close()


if __name__ == "__main__":
    asyncio.run(test_update_supply())
