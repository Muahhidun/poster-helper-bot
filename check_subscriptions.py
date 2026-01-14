#!/usr/bin/env python3
"""Check subscription status for all users"""
from database import get_database, DB_TYPE
from datetime import datetime

def check_all_subscriptions():
    """Check subscription status for all users"""
    db = get_database()

    try:
        conn = db._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT telegram_user_id, subscription_status, subscription_expires_at, created_at
            FROM users
            ORDER BY telegram_user_id
        """)

        users = cursor.fetchall()
        conn.close()

        if not users:
            print("❌ Нет пользователей в базе данных")
            return

        print(f"📊 Всего пользователей: {len(users)}\n")
        print("=" * 80)

        for user in users:
            telegram_user_id = user[0]
            status = user[1]
            expires_at = user[2]
            created_at = user[3]

            print(f"\n👤 User ID: {telegram_user_id}")
            print(f"   Статус: {status}")

            if expires_at:
                if DB_TYPE == "sqlite":
                    expires_dt = datetime.fromisoformat(str(expires_at))
                else:
                    expires_dt = expires_at

                days_left = (expires_dt - datetime.now()).days

                if days_left > 0:
                    print(f"   Истекает: {expires_at}")
                    print(f"   Осталось дней: {days_left}")
                    if days_left <= 7:
                        print(f"   ⚠️  Подписка скоро истечёт!")
                else:
                    print(f"   ⛔ Истекла: {expires_at}")
                    print(f"   Просрочена на: {abs(days_left)} дней")
            else:
                print(f"   ⚠️  Дата истечения не установлена")

            print(f"   Создан: {created_at}")

            # Check if subscription is actually active
            is_active = db.is_subscription_active(telegram_user_id)
            if is_active:
                print(f"   ✅ Подписка активна")
            else:
                print(f"   ❌ Подписка НЕ активна - бот не будет работать!")

        print("\n" + "=" * 80)
        print("\n💡 Для активации подписки используйте:")
        print("   python3 activate_subscription.py <telegram_user_id> [days]")

    except Exception as e:
        print(f"❌ Ошибка: {e}")

if __name__ == "__main__":
    check_all_subscriptions()
