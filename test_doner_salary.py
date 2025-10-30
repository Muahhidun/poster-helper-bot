"""Тестовый скрипт для проверки расчёта зарплаты донерщика"""
import asyncio
from doner_salary import DonerSalaryCalculator
from datetime import datetime

async def test_doner_salary():
    """Протестировать расчёт зарплаты донерщика"""
    telegram_user_id = 167084307
    calculator = DonerSalaryCalculator(telegram_user_id)

    print("=" * 70)
    print("🌮 ТЕСТ РАСЧЁТА ЗАРПЛАТЫ ДОНЕРЩИКА")
    print("=" * 70)
    print()

    # Получить данные о продажах за сегодня
    today = datetime.now().strftime("%Y%m%d")
    print(f"📅 Дата: {datetime.now().strftime('%d.%m.%Y')}")
    print()

    print("1️⃣ Получаем данные о продажах донеров...")
    sales_data = await calculator.get_doner_sales_count(today)

    print(f"\n   📊 Результаты продаж:")
    print(f"      • Категория 'Донер': {sales_data['category_count']} шт")
    print(f"      • Комбо Донер: {sales_data['combo_count']} шт")
    print(f"      • Донерная пицца: {sales_data['pizza_count']} шт")
    print(f"      • ИТОГО: {sales_data['total_count']} шт")

    if sales_data['details']:
        print(f"\n   📝 Детализация:")
        for detail in sales_data['details']:
            source_emoji = {
                'category': '🌮',
                'combo': '🎁',
                'pizza': '🍕'
            }.get(detail['source'], '❓')
            print(f"      {source_emoji} {detail['name']}: {detail['count']} шт")

    # Рассчитать зарплату
    print(f"\n2️⃣ Рассчитываем зарплату...")
    total_count = int(sales_data['total_count'])
    salary = calculator.calculate_salary(total_count)

    print(f"\n   💰 Зарплата: {salary:,}₸".replace(',', ' '))
    print(f"   🎯 Норма для {total_count} донеров")

    # Показать все нормы для справки
    print(f"\n   📋 Таблица норм:")
    from doner_salary import DONER_SALARY_NORMS
    prev_max = 0
    for max_count, norm_salary in sorted(DONER_SALARY_NORMS.items()):
        range_str = f"{prev_max + 1}-{max_count}" if prev_max > 0 else f"До {max_count}"
        indicator = "👉" if prev_max < total_count <= max_count else "  "
        print(f"      {indicator} {range_str:12} → {norm_salary:,}₸".replace(',', ' '))
        prev_max = max_count

    # Предложить создать транзакцию
    print(f"\n3️⃣ Создать транзакцию?")
    print(f"   Счёт: Оставил в кассе (ID=4)")
    print(f"   Категория: Донерщик (ID=19)")
    print(f"   Сумма: {salary:,}₸".replace(',', ' '))
    print()

    user_input = input("   Создать транзакцию? (да/нет): ").strip().lower()

    if user_input in ['да', 'yes', 'y', 'д']:
        print(f"\n   ⏳ Создаём транзакцию...")
        result = await calculator.create_salary_transaction(today)

        if result['success']:
            print(f"\n   ✅ Транзакция создана успешно!")
            print(f"      ID: {result['transaction_id']}")
            print(f"      Сумма: {result['salary']:,}₸".replace(',', ' '))
            print(f"      Донеров: {result['doner_count']} шт")
        else:
            print(f"\n   ❌ Ошибка создания транзакции:")
            print(f"      {result.get('error')}")
    else:
        print(f"\n   ⏭️  Транзакция не создана")

    print()
    print("=" * 70)
    print("✅ Тест завершён!")
    print("=" * 70)

if __name__ == "__main__":
    asyncio.run(test_doner_salary())
