"""Тестовый скрипт для проверки закрытия смены"""
import asyncio
from shift_closing import ShiftClosing
from datetime import datetime

async def test_shift_closing():
    """Протестировать закрытие смены"""
    telegram_user_id = 167084307
    shift = ShiftClosing(telegram_user_id)

    print("=" * 70)
    print("🔐 ТЕСТ ЗАКРЫТИЯ СМЕНЫ")
    print("=" * 70)
    print()

    # Получить данные за сегодня
    today = datetime.now().strftime("%Y%m%d")
    print(f"📅 Дата: {datetime.now().strftime('%d.%m.%Y')}")
    print()

    print("1️⃣ Получаем отчёт о смене...")
    report = await shift.get_shift_report(today)

    if report['success']:
        # Вывести форматированный отчёт
        formatted_report = shift.format_shift_report(report)
        print()
        print(formatted_report)
        print()
    else:
        print(f"\n   ❌ Ошибка получения отчёта: {report.get('error')}")
        return

    # Предложить закрыть смену
    print("2️⃣ Закрыть смену?")
    print()

    user_input = input("   Сколько кассиров на смене? (2/3/нет): ").strip()

    if user_input in ['2', '3']:
        cashier_count = int(user_input)

        print(f"\n   ⏳ Закрываем смену с {cashier_count} кассирами...")
        result = await shift.close_shift(cashier_count, today)

        if result['success']:
            print(f"\n   ✅ Смена закрыта успешно!")
            print()
            print(f"   💵 Зарплаты:")
            print(f"      • Кассиры ({cashier_count} чел): {result['cashier_salary']:,}₸ каждому".replace(',', ' '))
            print(f"        ID транзакций: {', '.join(str(id) for id in result['cashier_transactions'])}")
            print(f"      • Донерщик: {result['doner_salary']:,}₸".replace(',', ' '))
            print(f"        ID транзакции: {result['doner_transaction']}")
        else:
            print(f"\n   ❌ Ошибка закрытия смены:")
            print(f"      {result.get('error')}")
    else:
        print(f"\n   ⏭️  Смена не закрыта")

    print()
    print("=" * 70)
    print("✅ Тест завершён!")
    print("=" * 70)

if __name__ == "__main__":
    asyncio.run(test_shift_closing())
