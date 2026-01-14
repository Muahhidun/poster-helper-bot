#!/usr/bin/env python3
"""Script to activate subscription for a user"""
import sys
from datetime import datetime, timedelta
from database import get_database, DB_TYPE

def activate_subscription(telegram_user_id: int, days: int = 365):
    """Activate subscription for specified days"""
    db = get_database()

    # Check if user exists
    user = db.get_user(telegram_user_id)
    if not user:
        print(f"❌ Пользователь {telegram_user_id} не найден в базе данных")
        print(f"   Сначала пользователь должен отправить /start боту")
        return False

    print(f"✅ Пользователь найден: {telegram_user_id}")
    print(f"   Текущий статус: {user['subscription_status']}")
    if user['subscription_expires_at']:
        print(f"   Истекает: {user['subscription_expires_at']}")

    # Calculate expiration date
    expires_at = datetime.now() + timedelta(days=days)
    expires_str = expires_at.strftime('%Y-%m-%d %H:%M:%S')

    # Update subscription
    success = db.update_user(
        telegram_user_id=telegram_user_id,
        subscription_status='active'
    )

    if not success:
        print(f"❌ Не удалось обновить статус подписки")
        return False

    # Update expiration date manually
    try:
        conn = db._get_connection()
        cursor = conn.cursor()

        if DB_TYPE == "sqlite":
            cursor.execute(
                "UPDATE users SET subscription_expires_at = ?, updated_at = ? WHERE telegram_user_id = ?",
                (expires_str, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), telegram_user_id)
            )
        else:  # PostgreSQL
            cursor.execute(
                "UPDATE users SET subscription_expires_at = %s, updated_at = %s WHERE telegram_user_id = %s",
                (expires_at, datetime.now(), telegram_user_id)
            )

        conn.commit()
        conn.close()

        print(f"\n✅ Подписка активирована!")
        print(f"   Статус: active")
        print(f"   Дней: {days}")
        print(f"   Истекает: {expires_str}")
        print(f"\n🎉 Пользователь {telegram_user_id} может пользоваться ботом!")
        return True

    except Exception as e:
        print(f"❌ Ошибка при обновлении даты: {e}")
        return False

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 activate_subscription.py <telegram_user_id> [days]")
        print("\nПримеры:")
        print("  python3 activate_subscription.py 167084307          # 1 год (365 дней)")
        print("  python3 activate_subscription.py 167084307 30       # 30 дней")
        print("  python3 activate_subscription.py 167084307 3650     # 10 лет")
        sys.exit(1)

    telegram_user_id = int(sys.argv[1])
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 365

    print(f"🔄 Активация подписки для пользователя {telegram_user_id}...")
    print(f"   На {days} дней\n")

    success = activate_subscription(telegram_user_id, days)
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()
