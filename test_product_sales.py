"""Тестовый скрипт для поиска API метода получения продаж товаров"""
import asyncio
from datetime import datetime
from poster_client import PosterClient
import json

async def test_product_sales():
    """Исследовать доступные API методы для получения данных о продажах"""
    telegram_user_id = 167084307
    client = PosterClient(telegram_user_id)

    today = datetime.now().strftime("%Y%m%d")

    print("🔍 Исследуем API методы для получения данных о продажах товаров\n")
    print("=" * 70)

    # 1. Попробуем dash.getTransactions - получить заказы
    print("\n1️⃣ Пробуем dash.getTransactions (заказы)...")
    try:
        result = await client._request('GET', 'dash.getTransactions', params={
            'dateFrom': today,
            'dateTo': today
        })
        transactions = result.get('response', [])
        print(f"   ✅ Получено заказов за сегодня: {len(transactions)}")

        if transactions:
            # Посмотрим на структуру первого заказа
            first = transactions[0]
            print(f"\n   📋 Структура заказа:")
            print(f"      ID: {first.get('transaction_id')}")
            print(f"      Статус: {first.get('status')} (2=закрыт)")
            print(f"      Сумма: {int(first.get('payed_sum', 0))/100}₸")

            # Есть ли информация о товарах в заказе?
            if 'products' in first:
                print(f"      🎁 products: {first['products']}")
            if 'product' in first:
                print(f"      🎁 product: {first['product']}")

            # Выведем все ключи
            print(f"\n   🔑 Все ключи в заказе: {list(first.keys())}")
    except Exception as e:
        print(f"   ❌ Ошибка: {e}")

    # 2. Попробуем получить продукты
    print("\n2️⃣ Пробуем menu.getProducts...")
    try:
        products = await client.get_products()
        print(f"   ✅ Получено товаров: {len(products)}")

        # Найдём донеры
        doner_products = [p for p in products if 'донер' in p.get('product_name', '').lower()]
        print(f"\n   🌮 Найдено товаров с 'донер' в названии: {len(doner_products)}")
        for p in doner_products[:5]:
            print(f"      • {p.get('product_name')} (ID: {p.get('product_id')}, категория: {p.get('category_id')})")
    except Exception as e:
        print(f"   ❌ Ошибка: {e}")

    # 3. Попробуем menu.getCategories
    print("\n3️⃣ Пробуем menu.getCategories...")
    try:
        result = await client._request('GET', 'menu.getCategories')
        categories = result.get('response', [])
        print(f"   ✅ Получено категорий: {len(categories)}")

        # Найдём категорию "Донер"
        doner_cat = [c for c in categories if 'донер' in c.get('category_name', '').lower()]
        if doner_cat:
            print(f"\n   🌮 Категория 'Донер':")
            for c in doner_cat:
                print(f"      • {c.get('category_name')} (ID: {c.get('category_id')})")
    except Exception as e:
        print(f"   ❌ Ошибка: {e}")

    # 4. Попробуем dash.getProductsSales (если существует)
    print("\n4️⃣ Пробуем dash.getProductsSales...")
    try:
        result = await client._request('GET', 'dash.getProductsSales', params={
            'dateFrom': today,
            'dateTo': today
        })
        print(f"   ✅ Ответ получен!")
        print(f"   📊 Данные: {json.dumps(result, indent=2, ensure_ascii=False)[:500]}")
    except Exception as e:
        print(f"   ❌ Ошибка: {e}")

    # 5. Попробуем storage.getProductsSales
    print("\n5️⃣ Пробуем storage.getProductsSales...")
    try:
        result = await client._request('GET', 'storage.getProductsSales', params={
            'dateFrom': today,
            'dateTo': today
        })
        print(f"   ✅ Ответ получен!")
        print(f"   📊 Данные: {json.dumps(result, indent=2, ensure_ascii=False)[:500]}")
    except Exception as e:
        print(f"   ❌ Ошибка: {e}")

    # 6. Попробуем reports.getProductsSales
    print("\n6️⃣ Пробуем reports.getProductsSales...")
    try:
        result = await client._request('GET', 'reports.getProductsSales', params={
            'dateFrom': today,
            'dateTo': today
        })
        print(f"   ✅ Ответ получен!")
        print(f"   📊 Данные: {json.dumps(result, indent=2, ensure_ascii=False)[:500]}")
    except Exception as e:
        print(f"   ❌ Ошибка: {e}")

    # 7. Попробуем dash.getTransactionProducts (получить товары из конкретного заказа)
    print("\n7️⃣ Пробуем dash.getTransactionProducts...")
    try:
        # Получим ID первого заказа
        result = await client._request('GET', 'dash.getTransactions', params={
            'dateFrom': today,
            'dateTo': today
        })
        transactions = result.get('response', [])
        if transactions:
            first_tx_id = transactions[0].get('transaction_id')
            print(f"   🔍 Пробуем получить товары из заказа {first_tx_id}...")

            result = await client._request('GET', 'dash.getTransactionProducts', params={
                'transaction_id': first_tx_id
            })
            print(f"   ✅ Ответ получен!")
            print(f"   📊 Данные: {json.dumps(result, indent=2, ensure_ascii=False)[:800]}")
    except Exception as e:
        print(f"   ❌ Ошибка: {e}")

    await client.close()

    print("\n" + "=" * 70)
    print("✅ Исследование завершено!")

if __name__ == "__main__":
    asyncio.run(test_product_sales())
