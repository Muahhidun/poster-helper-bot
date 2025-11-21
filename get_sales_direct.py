#!/usr/bin/env python3
"""Прямое получение данных о продажах донеров через Poster API"""
import asyncio
import aiohttp

# Настройки из .env
POSTER_ACCOUNT = "pizz-burg"
POSTER_TOKEN = "701489:0304445864dfa829233142b4ec899628"
POSTER_BASE_URL = f"https://{POSTER_ACCOUNT}.joinposter.com/api"

# ID категории "Донер"
DONER_CATEGORY_ID = 6

async def get_doner_sales(date_str="20241120"):
    """Получить продажи донеров за дату"""

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
                print(f"❌ Ошибка API: {result['error']}")
                return None

            products_sales = result.get('response', [])

            # Подсчёт
            category_count = 0.0
            combo_count = 0.0
            pizza_count = 0.0
            details = []

            for product in products_sales:
                product_name = product.get('product_name', '')
                category_id = product.get('category_id', '')
                count = float(product.get('count', 0))

                # Категория "Донер"
                if category_id == str(DONER_CATEGORY_ID):
                    category_count += count
                    details.append({
                        'name': product_name,
                        'count': count,
                        'source': 'category'
                    })

                # Комбо Донер
                elif 'комбо донер' in product_name.lower():
                    combo_count += count
                    details.append({
                        'name': product_name,
                        'count': count,
                        'source': 'combo'
                    })

                # Донерная пицца
                elif 'донерная пицца' in product_name.lower():
                    pizza_count += count
                    details.append({
                        'name': product_name,
                        'count': count,
                        'source': 'pizza'
                    })

            total_count = category_count + combo_count + pizza_count

            return {
                'category_count': category_count,
                'combo_count': combo_count,
                'pizza_count': pizza_count,
                'total_count': total_count,
                'details': details
            }

def calculate_salary(total_count):
    """Рассчитать зарплату по таблице"""
    SALARY_NORMS = {
        139: 8500,
        159: 7400,
        179: 8300,
        199: 9150,
        219: 10050,
        239: 10900,
        259: 11800,
        279: 12650,
        299: 13550,
        319: 14400,
        339: 15300,
        359: 16150,
        379: 17050,
        399: 17900,
    }

    for max_count, salary in sorted(SALARY_NORMS.items()):
        if total_count <= max_count:
            return salary

    # Если больше максимума
    return SALARY_NORMS[max(SALARY_NORMS.keys())]

async def main():
    date_str = "20241120"

    print("="*70)
    print(f"📊 ПОЛУЧЕНИЕ ДАННЫХ О ПРОДАЖАХ ДОНЕРОВ ЗА {date_str}")
    print("="*70)
    print()

    sales = await get_doner_sales(date_str)

    if not sales:
        print("❌ Не удалось получить данные")
        return

    print(f"📦 Категория \"Донер\" (ID=6):     {sales['category_count']:>6.0f} шт")
    print(f"🎁 Комбо Донер:                  {sales['combo_count']:>6.0f} шт")
    print(f"🍕 Донерная пицца:               {sales['pizza_count']:>6.0f} шт")
    print("-"*70)
    print(f"📊 ВСЕГО для расчёта зарплаты:   {sales['total_count']:>6.0f} шт")
    print("="*70)
    print()

    # Рассчитать зарплату
    salary = calculate_salary(int(sales['total_count']))
    print(f"💰 Зарплата донерщика (по таблице): {salary:,}₸")
    print()

    # Детали
    print("="*70)
    print("ДЕТАЛЬНАЯ РАЗБИВКА ПО ТОВАРАМ:")
    print("="*70)

    category_items = [x for x in sales['details'] if x['source'] == 'category']
    combo_items = [x for x in sales['details'] if x['source'] == 'combo']
    pizza_items = [x for x in sales['details'] if x['source'] == 'pizza']

    if category_items:
        print('\n[КАТЕГОРИЯ "ДОНЕР"]')
        for item in sorted(category_items, key=lambda x: x['count'], reverse=True):
            print(f"  • {item['name']:45} {item['count']:>6.0f} шт")

    if combo_items:
        print('\n[КОМБО ДОНЕР]')
        for item in combo_items:
            print(f"  • {item['name']:45} {item['count']:>6.0f} шт")

    if pizza_items:
        print('\n[ДОНЕРНАЯ ПИЦЦА]')
        for item in pizza_items:
            print(f"  • {item['name']:45} {item['count']:>6.0f} шт")

    print()
    print("="*70)

if __name__ == '__main__':
    asyncio.run(main())
