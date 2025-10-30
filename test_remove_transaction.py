"""Тестовый скрипт для проверки удаления чека через API"""
import asyncio
from poster_client import PosterClient
from datetime import datetime


async def test_remove_transaction():
    """Протестировать удаление чека"""
    print("🧪 ТЕСТ УДАЛЕНИЯ ЧЕКА ЧЕРЕЗ API")
    print("=" * 70)
    print()

    client = PosterClient(167084307)

    # Получить сегодняшние заказы
    today = datetime.now().strftime("%Y%m%d")
    result = await client._request("GET", "dash.getTransactions", params={
        "dateFrom": today,
        "dateTo": today
    })

    transactions = result.get("response", [])

    # Найти закрытые чеки (status == '2')
    closed_orders = [t for t in transactions if t.get("status") == "2"]

    if not closed_orders:
        print("❌ Нет закрытых чеков за сегодня")
        await client.close()
        return

    # Берем чек с минимальной суммой для безопасного теста
    test_order = min(closed_orders, key=lambda x: float(x.get("payed_sum", 0)))
    tx_id = test_order.get("transaction_id")
    tx_sum = float(test_order.get("payed_sum", 0)) / 100
    tx_status = test_order.get("status")

    tx_date = test_order.get("date_close_date", "")

    print(f"📋 Найден ЗАКРЫТЫЙ чек с минимальной суммой для теста:")
    print(f"   ID: {tx_id}")
    print(f"   Сумма: {tx_sum:,.0f}₸")
    print(f"   Дата закрытия: {tx_date}")
    print(f"   Статус: {tx_status} (закрыт)")
    print()
    print(f"⚠️  ВНИМАНИЕ: Это ЗАКРЫТЫЙ чек! При удалении:")
    print(f"   - Обновятся отчёты")
    print(f"   - Пересчитается кассовая смена")
    print(f"   - Товары вернутся на склад")
    print()

    # Подтверждение
    confirm = input(f"❓ Удалить чек #{tx_id}? (yes/no): ").strip().lower()

    if confirm != "yes":
        print("❌ Отменено")
        await client.close()
        return

    print()
    print(f"🗑️  Удаляем чек #{tx_id}...")

    try:
        # УДАЛЕНИЕ ЧЕКА
        await client.remove_transaction(tx_id)

        print()
        print(f"✅ Чек #{tx_id} успешно удалён!")
        print()

        # Проверка: пытаемся снова получить заказы
        print("🔍 Проверяем, что чек действительно удалён...")
        result = await client._request("GET", "dash.getTransactions", params={
            "dateFrom": today,
            "dateTo": today
        })

        transactions_after = result.get("response", [])
        deleted = not any(t.get("transaction_id") == tx_id for t in transactions_after)

        if deleted:
            print(f"✅ Подтверждено: чек #{tx_id} больше не найден в системе")
        else:
            print(f"⚠️  Чек #{tx_id} всё ещё есть в системе")

    except Exception as e:
        print()
        print(f"❌ Ошибка удаления: {e}")

    await client.close()
    print()
    print("=" * 70)
    print("🏁 Тест завершён")


if __name__ == "__main__":
    asyncio.run(test_remove_transaction())
