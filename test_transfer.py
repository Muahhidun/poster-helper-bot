#!/usr/bin/env python3
"""Test transfer transaction"""

import asyncio
import aiohttp
from datetime import datetime
from config import POSTER_BASE_URL, POSTER_TOKEN, POSTER_USER_ID

async def test_transfer():
    """Test creating transfer from Kaspi Pay to Wolt"""

    # Transfer data (exactly as bot sends)
    data = {
        'type': 2,  # Transfer
        'account_from': 1,  # Kaspi Pay
        'amount_from': 1,
        'amount_to': 1,  # Добавили!
        'user_id': POSTER_USER_ID,
        'date': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        'comment': 'Тест перевод из скрипта',
        'account_to': 8  # Wolt
    }

    print("📤 Отправка запроса к Poster API...")
    print(f"   URL: {POSTER_BASE_URL}/finance.createTransactions")
    print(f"   Данные: {data}\n")

    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{POSTER_BASE_URL}/finance.createTransactions",
            params={'token': POSTER_TOKEN},
            data=data
        ) as response:
            result = await response.json()

            print(f"📥 Ответ от API:")
            print(f"   Status: {response.status}")
            print(f"   Response: {result}\n")

            if 'error' in result:
                error = result['error']
                if isinstance(error, dict):
                    print(f"❌ Ошибка {error.get('code', 'N/A')}: {error.get('message', 'Unknown')}")
                else:
                    print(f"❌ Ошибка: {error}")
            else:
                print(f"✅ Успех! Transaction ID: {result.get('response')}")

if __name__ == "__main__":
    asyncio.run(test_transfer())
