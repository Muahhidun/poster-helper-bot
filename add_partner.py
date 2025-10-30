"""Скрипт для добавления партнера в базу данных с основным аккаунтом"""
import sys
from database import get_database

def add_partner(partner_telegram_id: int, main_account_id: int = 167084307):
    """
    Добавить партнера в базу с теми же credentials что и основной аккаунт

    Args:
        partner_telegram_id: Telegram ID партнера
        main_account_id: Telegram ID основного аккаунта (по умолчанию 167084307)
    """
    db = get_database()

    # Получить данные основного аккаунта
    main_account = db.get_user(main_account_id)

    if not main_account:
        print(f"❌ Основной аккаунт {main_account_id} не найден в базе!")
        return False

    print(f"✅ Найден основной аккаунт:")
    print(f"   Poster Token: {main_account['poster_token']}")
    print(f"   Poster User ID: {main_account['poster_user_id']}")
    print(f"   Poster URL: {main_account['poster_base_url']}")

    # Проверить, не существует ли уже партнер
    existing_partner = db.get_user(partner_telegram_id)
    if existing_partner:
        print(f"\n⚠️  Партнер {partner_telegram_id} уже существует в базе!")
        response = input("Обновить его данные? (yes/no): ")
        if response.lower() != 'yes':
            print("Отменено.")
            return False

        # Обновить существующего пользователя
        success = db.update_user(
            partner_telegram_id,
            poster_token=main_account['poster_token'],
            poster_user_id=main_account['poster_user_id'],
            poster_base_url=main_account['poster_base_url']
        )

        if success:
            print(f"\n✅ Партнер {partner_telegram_id} обновлен!")
        else:
            print(f"\n❌ Ошибка обновления партнера!")

        return success

    # Создать нового партнера с теми же credentials
    success = db.create_user(
        telegram_user_id=partner_telegram_id,
        poster_token=main_account['poster_token'],
        poster_user_id=main_account['poster_user_id'],
        poster_base_url=main_account['poster_base_url']
    )

    if success:
        print(f"\n✅ Партнер {partner_telegram_id} добавлен в базу!")
        print(f"   Подписка: trial (14 дней)")
        print(f"\n📝 Следующий шаг:")
        print(f"   1. Добавьте {partner_telegram_id} в ALLOWED_USER_IDS в .env файле")
        print(f"   2. Перезапустите бота: bash ~/poster-helper-bot/restart_bot.sh")
    else:
        print(f"\n❌ Ошибка создания партнера!")

    return success


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("❌ Использование: python3 add_partner.py <telegram_user_id>")
        print("\nПример: python3 add_partner.py 123456789")
        print("\nДля получения telegram_user_id попросите партнера отправить команду /myid боту")
        sys.exit(1)

    try:
        partner_id = int(sys.argv[1])
        add_partner(partner_id)
    except ValueError:
        print("❌ Ошибка: telegram_user_id должен быть числом!")
        sys.exit(1)
