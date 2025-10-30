"""Тестовый скрипт для проверки кассовых смен"""
import asyncio
from datetime import datetime, timedelta
from poster_client import PosterClient

async def test_cash_shifts():
    """Проверить структуру кассовых смен в транзакциях"""
    telegram_user_id = 167084307

    client = PosterClient(telegram_user_id)

    # Даты за последние 7 дней
    date_to = datetime.now()
    date_from = date_to - timedelta(days=7)

    date_from_str = date_from.strftime("%Y%m%d")
    date_to_str = date_to.strftime("%Y%m%d")

    print(f"📅 Период: {date_from_str} - {date_to_str}\n")

    # Получить транзакции
    transactions = await client.get_transactions(date_from_str, date_to_str)

    # Фильтруем кассовые смены (category_name содержит "Кассовые смены" или type=1 доход)
    cash_shifts = []

    print("🔍 Ищем кассовые смены...\n")

    for tx in transactions:
        if tx.get('delete') == '0':  # Не удалённые
            category_name = tx.get('category_name', '')
            tx_type = tx.get('type', '')
            comment = tx.get('comment', '')

            # Ищем кассовые смены
            if 'Кассов' in category_name or 'смен' in category_name.lower() or 'смен' in comment.lower():
                cash_shifts.append(tx)
                print(f"✅ Найдена кассовая смена:")
                print(f"   Категория: {category_name}")
                print(f"   Тип: {tx_type} (0=расход, 1=доход, 2=перевод)")
                print(f"   Счёт: {tx.get('account_name')}")
                print(f"   Сумма: {int(tx.get('amount', 0)) / 100}₸")
                print(f"   Комментарий: {comment}")
                print(f"   Дата: {tx.get('date')}")
                print()

    print(f"\n📊 Всего найдено кассовых смен: {len(cash_shifts)}")

    # Также проверим все доходы (type=1)
    incomes = [tx for tx in transactions if tx.get('type') == '1' and tx.get('delete') == '0']
    print(f"\n📈 Всего транзакций с type=1 (доходы): {len(incomes)}")

    if incomes:
        print("\nПримеры доходов:")
        for tx in incomes[:5]:
            print(f"  • {tx.get('category_name')}: {int(tx.get('amount', 0)) / 100}₸ - {tx.get('comment', 'без комментария')}")

    await client.close()

if __name__ == "__main__":
    asyncio.run(test_cash_shifts())
