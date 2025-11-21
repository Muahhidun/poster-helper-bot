#!/usr/bin/env python3
"""Проверка продаж донеров за 20 ноября 2024"""
import asyncio
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from doner_salary import DonerSalaryCalculator
from config import ALLOWED_USER_IDS

async def main():
    if not ALLOWED_USER_IDS:
        print('❌ Нет пользователей в ALLOWED_USER_IDS')
        return

    user_id = ALLOWED_USER_IDS[0]
    print(f'👤 Используем пользователя: {user_id}\n')

    calculator = DonerSalaryCalculator(user_id)

    # Получить данные за 20 ноября 2024
    date = '20241120'
    print(f'📅 Получаю данные за {date}...\n')

    try:
        sales = await calculator.get_doner_sales_count(date)

        print('='*70)
        print('📊 ПРОДАЖИ ДОНЕРОВ ЗА 20 НОЯБРЯ 2024')
        print('='*70)
        print()
        print(f'📦 Категория "Донер" (ID=6):     {sales["category_count"]:>6.0f} шт')
        print(f'🎁 Комбо Донер:                  {sales["combo_count"]:>6.0f} шт')
        print(f'🍕 Донерная пицца:               {sales["pizza_count"]:>6.0f} шт')
        print('-'*70)
        print(f'📊 ВСЕГО для расчёта зарплаты:   {sales["total_count"]:>6.0f} шт')
        print('='*70)
        print()

        # Рассчитать зарплату
        salary = calculator.calculate_salary(int(sales['total_count']))
        print(f'💰 Зарплата донерщика (по таблице): {salary:,}₸')
        print()

        # Детали по товарам
        print('='*70)
        print('ДЕТАЛЬНАЯ РАЗБИВКА ПО ТОВАРАМ:')
        print('='*70)

        # Группировка по источникам
        category_items = [x for x in sales['details'] if x['source'] == 'category']
        combo_items = [x for x in sales['details'] if x['source'] == 'combo']
        pizza_items = [x for x in sales['details'] if x['source'] == 'pizza']

        if category_items:
            print('\n[КАТЕГОРИЯ "ДОНЕР"]')
            for item in sorted(category_items, key=lambda x: x['count'], reverse=True):
                print(f'  • {item["name"]:45} {item["count"]:>6.0f} шт')

        if combo_items:
            print('\n[КОМБО ДОНЕР]')
            for item in combo_items:
                print(f'  • {item["name"]:45} {item["count"]:>6.0f} шт')

        if pizza_items:
            print('\n[ДОНЕРНАЯ ПИЦЦА]')
            for item in pizza_items:
                print(f'  • {item["name"]:45} {item["count"]:>6.0f} шт')

        print()
        print('='*70)

    except Exception as e:
        print(f'❌ Ошибка: {e}')
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    asyncio.run(main())
