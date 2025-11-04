"""Скрипт для проверки алиасов в базе данных"""
import logging
from database import get_database

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def main():
    db = get_database()

    # Проверяем алиасы для пользователя 167084307
    user_id = 167084307

    aliases = db.get_ingredient_aliases(user_id)

    print(f"\n📊 Алиасов в БД для пользователя {user_id}: {len(aliases)}")

    if aliases:
        print("\nПервые 10 алиасов:")
        for i, alias in enumerate(aliases[:10], 1):
            print(f"{i}. {alias['alias_text']} → {alias['poster_item_name']} (ID: {alias['poster_item_id']})")
    else:
        print("\n❌ Алиасов НЕТ в базе данных!")
        print("\nПопытка импорта из railway_aliases.py...")

        try:
            from railway_aliases import RAILWAY_ALIASES

            aliases_to_import = []
            for alias_text, item_id, item_name, source in RAILWAY_ALIASES:
                aliases_to_import.append({
                    'alias_text': alias_text,
                    'poster_item_id': item_id,
                    'poster_item_name': item_name,
                    'source': source,
                    'notes': 'Manual import via check_aliases.py'
                })

            if aliases_to_import:
                count = db.bulk_add_aliases(user_id, aliases_to_import)
                print(f"✅ Импортировано {count} алиасов")

                # Проверяем снова
                aliases = db.get_ingredient_aliases(user_id)
                print(f"📊 Теперь в БД: {len(aliases)} алиасов")
        except Exception as e:
            print(f"❌ Ошибка импорта: {e}")

if __name__ == "__main__":
    main()
