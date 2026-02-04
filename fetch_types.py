"""Fetch all ingredients and products from both Poster accounts with their types"""
import asyncio
import sys
sys.path.insert(0, '.')

from database import get_database
from poster_client import PosterClient


async def main():
    db = get_database()

    # Get all users who have accounts
    # We'll try to find accounts - let's check what users exist
    import psycopg2
    import os
    from psycopg2.extras import RealDictCursor

    database_url = os.getenv("DATABASE_URL")
    if database_url:
        conn = psycopg2.connect(database_url)
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT DISTINCT telegram_user_id FROM poster_accounts")
        user_rows = cursor.fetchall()
        conn.close()

        for user_row in user_rows:
            uid = user_row['telegram_user_id']
            accounts = db.get_accounts(uid)

            for account in accounts:
                account_name = account['account_name']
                print(f"\n{'='*60}")
                print(f"АККАУНТ: {account_name} (user_id={uid})")
                print(f"{'='*60}")

                client = PosterClient(
                    telegram_user_id=uid,
                    poster_token=account['poster_token'],
                    poster_user_id=account['poster_user_id'],
                    poster_base_url=account['poster_base_url']
                )

                try:
                    # Fetch ingredients
                    ingredients = await client.get_ingredients()
                    print(f"\n--- ИНГРЕДИЕНТЫ ({len(ingredients)} шт.) ---")
                    print(f"{'ID':<8} {'Type':<6} {'Тип':<15} {'Название':<40} {'Единица'}")
                    print("-" * 85)

                    type_names = {'1': 'ингредиент', '2': 'полуфабрикат', '3': 'тип3', '4': 'товар'}

                    for ing in sorted(ingredients, key=lambda x: str(x.get('type', '1'))):
                        ing_type = str(ing.get('type', '?'))
                        type_name = type_names.get(ing_type, f'неизвестный({ing_type})')
                        print(f"{ing.get('ingredient_id', '?'):<8} {ing_type:<6} {type_name:<15} {ing.get('ingredient_name', '?'):<40} {ing.get('unit', '?')}")

                    # Fetch products
                    products = await client.get_products()
                    print(f"\n--- ТОВАРЫ ({len(products)} шт.) ---")
                    print(f"{'ID':<8} {'Type':<6} {'Название':<40} {'Категория'}")
                    print("-" * 70)

                    for prod in products:
                        prod_type = str(prod.get('type', '?'))
                        print(f"{prod.get('product_id', '?'):<8} {prod_type:<6} {prod.get('product_name', '?'):<40} {prod.get('category_name', '?')}")

                finally:
                    await client.close()
    else:
        print("DATABASE_URL not set, trying legacy mode...")
        client = PosterClient()
        try:
            ingredients = await client.get_ingredients()
            print(f"\n--- ИНГРЕДИЕНТЫ ({len(ingredients)} шт.) ---")
            for ing in ingredients:
                print(f"  ID={ing.get('ingredient_id')} type={ing.get('type')} name={ing.get('ingredient_name')} unit={ing.get('unit')}")

            products = await client.get_products()
            print(f"\n--- ТОВАРЫ ({len(products)} шт.) ---")
            for prod in products:
                print(f"  ID={prod.get('product_id')} type={prod.get('type')} name={prod.get('product_name')} category={prod.get('category_name')}")
        finally:
            await client.close()


if __name__ == "__main__":
    asyncio.run(main())
