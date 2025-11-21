#!/usr/bin/env python3
"""Показать ВСЕ проданные товары за день с категориями"""
import asyncio
import sys
sys.path.insert(0, '/home/user/poster-helper-bot')

from poster_client import PosterClient

async def main():
    # Прямой вызов без database
    from config import POSTER_BASE_URL, POSTER_TOKEN

    import aiohttp

    date_str = "20241120"

    async with aiohttp.ClientSession() as session:
        url = f"{POSTER_BASE_URL}/dash.getProductsSales"
        params = {
            'token': POSTER_TOKEN,
            'dateFrom': date_str,
            'dateTo': date_str
        }

        async with session.get(url, params=params) as response:
            result = await response.json()

            if 'error' in result:
                print(f"❌ Ошибка: {result['error']}")
                return

            products = result.get('response', [])

            print("="*80)
            print(f"ВСЕ ТОВАРЫ ЗА {date_str}")
            print("="*80)
            print(f"{'Название товара':<50} {'Кат.ID':<8} {'Кол-во':<8}")
            print("-"*80)

            # Фильтруем только донеры, комбо и пиццы
            doner_related = []

            for p in products:
                name = p.get('product_name', '')
                cat_id = p.get('category_id', '')
                count = float(p.get('count', 0))

                # Ищем все, что может быть связано с донерами
                if (cat_id == '6' or
                    'донер' in name.lower() or
                    'комбо' in name.lower() or
                    'твистер' in name.lower() or
                    'пицц' in name.lower()):

                    doner_related.append({
                        'name': name,
                        'cat_id': cat_id,
                        'count': count
                    })

            # Сортируем по количеству
            doner_related.sort(key=lambda x: x['count'], reverse=True)

            total = 0
            for item in doner_related:
                print(f"{item['name']:<50} {item['cat_id']:<8} {item['count']:<8.0f}")
                total += item['count']

            print("-"*80)
            print(f"{'ИТОГО:':<50} {'':<8} {total:<8.0f}")
            print("="*80)

if __name__ == '__main__':
    asyncio.run(main())
