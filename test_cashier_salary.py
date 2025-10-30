"""Тестовый скрипт для проверки расчёта зарплаты кассиров"""
import asyncio
from cashier_salary import CashierSalaryCalculator, CASHIER_SALARY_NORMS_2, CASHIER_SALARY_NORMS_3
from datetime import datetime

async def test_cashier_salary():
    """Протестировать расчёт зарплаты кассиров"""
    telegram_user_id = 167084307
    calculator = CashierSalaryCalculator(telegram_user_id)

    print("=" * 70)
    print("💰 ТЕСТ РАСЧЁТА ЗАРПЛАТЫ КАССИРОВ")
    print("=" * 70)
    print()

    # Получить данные о продажах за сегодня
    today = datetime.now().strftime("%Y%m%d")
    print(f"📅 Дата: {datetime.now().strftime('%d.%m.%Y')}")
    print()

    print("1️⃣ Получаем данные о продажах...")
    sales_data = await calculator.get_total_sales(today)

    print(f"\n   📊 Результаты продаж:")
    print(f"      • Общая сумма: {sales_data['total_sum']/100:,.0f}₸".replace(',', ' '))
    print(f"      • Наличные: {sales_data['cash']/100:,.0f}₸".replace(',', ' '))
    print(f"      • Картой: {sales_data['card']/100:,.0f}₸".replace(',', ' '))
    print(f"      • Бонусы (вычитаются): {sales_data['bonus']/100:,.0f}₸".replace(',', ' '))
    print(f"      • ИТОГО для расчёта: {sales_data['total_sales']/100:,.0f}₸".replace(',', ' '))
    print(f"      • Закрытых заказов: {sales_data['transactions_count']}")

    # Рассчитать зарплату для 2 кассиров
    print(f"\n2️⃣ Рассчитываем зарплату для 2 кассиров...")
    total_sales = sales_data['total_sales']
    salary_2 = calculator.calculate_salary(total_sales, 2)

    print(f"\n   💰 Зарплата каждого: {salary_2:,}₸".replace(',', ' '))
    print(f"   💰 Общая сумма: {salary_2 * 2:,}₸".replace(',', ' '))

    # Показать таблицу норм для 2 кассиров
    print(f"\n   📋 Таблица норм для 2 кассиров:")
    prev_max = 0
    for max_sales, norm_salary in sorted(CASHIER_SALARY_NORMS_2.items()):
        range_str = f"{prev_max/100:,.0f} - {max_sales/100:,.0f}₸".replace(',', ' ')
        indicator = "👉" if prev_max < total_sales <= max_sales else "  "
        print(f"      {indicator} {range_str:30} → {norm_salary:,}₸ каждому".replace(',', ' '))
        prev_max = max_sales

    # Рассчитать зарплату для 3 кассиров
    print(f"\n3️⃣ Рассчитываем зарплату для 3 кассиров...")
    salary_3 = calculator.calculate_salary(total_sales, 3)

    print(f"\n   💰 Зарплата каждого: {salary_3:,}₸".replace(',', ' '))
    print(f"   💰 Общая сумма: {salary_3 * 3:,}₸".replace(',', ' '))

    # Показать таблицу норм для 3 кассиров
    print(f"\n   📋 Таблица норм для 3 кассиров:")
    prev_max = 0
    for max_sales, norm_salary in sorted(CASHIER_SALARY_NORMS_3.items()):
        range_str = f"{prev_max/100:,.0f} - {max_sales/100:,.0f}₸".replace(',', ' ')
        indicator = "👉" if prev_max < total_sales <= max_sales else "  "
        print(f"      {indicator} {range_str:30} → {norm_salary:,}₸ каждому".replace(',', ' '))
        prev_max = max_sales

    # Предложить создать транзакции
    print(f"\n4️⃣ Создать транзакции?")
    print(f"   Счёт: Оставил в кассе (ID=4)")
    print(f"   Категория: Кассиры (ID=16)")
    print()

    user_input = input("   Сколько кассиров на смене? (2/3/нет): ").strip()

    if user_input in ['2', '3']:
        cashier_count = int(user_input)
        salary = salary_2 if cashier_count == 2 else salary_3

        print(f"\n   ⏳ Создаём {cashier_count} транзакции по {salary:,}₸...".replace(',', ' '))
        result = await calculator.create_salary_transactions(cashier_count, today)

        if result['success']:
            print(f"\n   ✅ Транзакции созданы успешно!")
            print(f"      Количество: {result['cashier_count']}")
            print(f"      Зарплата каждого: {result['salary_per_cashier']:,}₸".replace(',', ' '))
            print(f"      ID транзакций: {', '.join(str(id) for id in result['transaction_ids'])}")
        else:
            print(f"\n   ❌ Ошибка создания транзакций:")
            print(f"      {result.get('error')}")
    else:
        print(f"\n   ⏭️  Транзакции не созданы")

    print()
    print("=" * 70)
    print("✅ Тест завершён!")
    print("=" * 70)

if __name__ == "__main__":
    asyncio.run(test_cashier_salary())
