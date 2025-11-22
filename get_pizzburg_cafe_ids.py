"""Скрипт для получения ID счетов и категорий из Pizzburg-cafe"""
import asyncio
import sys
from database import get_database
from poster_client import PosterClient


async def get_ids():
    """Получить ID для Pizzburg-cafe"""

    # ID пользователя (укажите свой)
    telegram_user_id = 167084307  # Замените на ваш ID

    # Получить аккаунты из базы данных
    db = get_database()
    accounts = db.get_accounts(telegram_user_id)

    # Найти Pizzburg-cafe
    pizzburg_cafe = None
    for acc in accounts:
        if acc['account_name'] == 'Pizzburg-cafe':
            pizzburg_cafe = acc
            break

    if not pizzburg_cafe:
        print("❌ Аккаунт Pizzburg-cafe не найден!")
        return

    print(f"✅ Найден аккаунт: {pizzburg_cafe['account_name']}")
    print()

    # Создать клиент для Pizzburg-cafe
    client = PosterClient(
        telegram_user_id=telegram_user_id,
        poster_token=pizzburg_cafe['poster_token'],
        poster_user_id=pizzburg_cafe['poster_user_id'],
        poster_base_url=pizzburg_cafe['poster_base_url']
    )

    try:
        # Получить счета
        print("📊 Счета (Accounts):")
        print("-" * 80)
        accounts = await client.get_accounts()
        for acc in accounts:
            acc_id = acc.get('account_id')
            acc_name = acc.get('account_name', 'Unknown')
            print(f"  ID={acc_id:3} | {acc_name}")

        print()

        # Получить категории расходов
        print("📋 Категории расходов (Finance Categories):")
        print("-" * 80)
        categories = await client.get_categories()
        for cat in categories:
            cat_id = cat.get('finance_category_id')
            cat_name = cat.get('finance_category_name', 'Unknown')
            cat_type = cat.get('category_type', 0)  # 0=расход, 1=доход, 2=возврат

            if cat_type == 0:  # Только расходы
                print(f"  ID={cat_id:3} | {cat_name}")

        print()
        print("=" * 80)
        print()

        # Подсказки для поиска нужных ID
        print("🔍 Ищем нужные ID:")
        print()

        # Поиск счетов
        print("Счета:")
        for acc in accounts:
            acc_id = acc.get('account_id')
            acc_name = acc.get('account_name', '').lower()

            if 'оставил' in acc_name or 'касс' in acc_name:
                print(f"  ✅ Оставил в кассе: ID={acc_id} ({acc.get('account_name')})")
            elif 'инкасс' in acc_name:
                print(f"  ✅ Инкассация: ID={acc_id} ({acc.get('account_name')})")
            elif 'kaspi' in acc_name or 'каспи' in acc_name:
                print(f"  ✅ Kaspi Pay: ID={acc_id} ({acc.get('account_name')})")
            elif 'wolt' in acc_name or 'волт' in acc_name:
                print(f"  ✅ Wolt доставка: ID={acc_id} ({acc.get('account_name')})")

        print()
        print("Категории:")
        for cat in categories:
            cat_id = cat.get('finance_category_id')
            cat_name = cat.get('finance_category_name', '').lower()
            cat_type = cat.get('category_type', 0)

            if cat_type != 0:  # Пропускаем доходы
                continue

            if 'кассир' in cat_name:
                print(f"  ✅ Кассир: ID={cat_id} ({cat.get('finance_category_name')})")
            elif 'суш' in cat_name:
                print(f"  ✅ Сушист: ID={cat_id} ({cat.get('finance_category_name')})")
            elif 'повар' in cat_name and 'санд' in cat_name:
                print(f"  ✅ Повар Сандей: ID={cat_id} ({cat.get('finance_category_name')})")

    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(get_ids())
