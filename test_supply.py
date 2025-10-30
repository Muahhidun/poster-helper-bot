#!/usr/bin/env python3
"""Test supply creation for Lavash Astana"""

import asyncio
import aiohttp
from datetime import datetime
from config import POSTER_BASE_URL, POSTER_TOKEN

async def test_supply():
    """Test creating supply for supplier 27"""

    # Minimal supply data
    data = {
        'date': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        'supplier_id': 27,  # Лаваш Астана
        'storage_id': 1,    # Продукты
        'source': 'manage',
        'type': 1,
        'supply_comment': 'Test from bot',
        'ingredients[0][id]': 81,  # Лаваш
        'ingredients[0][num]': 500.0,  # Как в боте - с float
        'ingredients[0][price]': 40,
        'ingredients[0][ingredient_sum]': 20000,
        'ingredients[0][tax_id]': 0,
        'ingredients[0][packing]': 1,
        'transactions[0][transaction_id]': '',
        'transactions[0][account_id]': 1,  # Kaspi Pay
        'transactions[0][date]': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        'transactions[0][amount]': 20000,
        'transactions[0][delete]': 0
    }

    print("📤 Отправка запроса к Poster API...")
    print(f"   URL: {POSTER_BASE_URL}/storage.createSupply")
    print(f"   Данные: {data}\n")

    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{POSTER_BASE_URL}/storage.createSupply",
            params={'token': POSTER_TOKEN},
            data=data
        ) as response:
            result = await response.json()

            print(f"📥 Ответ от API:")
            print(f"   Status: {response.status}")
            print(f"   Response: {result}\n")

            if 'error' in result:
                print(f"❌ Ошибка: {result['error']}")
            else:
                print(f"✅ Успех! Supply ID: {result.get('response')}")

if __name__ == "__main__":
    asyncio.run(test_supply())
