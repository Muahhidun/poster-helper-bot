# Multi-Account Support Implementation Plan

## Этап 1: База данных ✅ (В ПРОЦЕССЕ)

### Новая структура:

```
users (обновленная таблица)
  - telegram_user_id (primary key)
  - subscription_status
  - subscription_expires_at
  - created_at
  - updated_at

poster_accounts (новая таблица)
  - id (primary key, auto increment)
  - telegram_user_id (foreign key to users)
  - account_name (varchar) - "Pizzburg", "Pizzburg-cafe"
  - poster_token (varchar)
  - poster_user_id (varchar)
  - poster_base_url (varchar)
  - is_primary (boolean) - главный аккаунт (Pizzburg = true)
  - created_at
  - updated_at
```

### Изменения в database.py:

1. ✅ Добавить таблицу `poster_accounts` в _init_db()
2. ✅ Создать метод миграции `migrate_to_multi_account()`
3. ✅ Обновить методы:
   - get_user() - теперь возвращает данные без poster_token
   - create_user() - создает user + первый account
   - get_accounts(telegram_user_id) - получить все аккаунты пользователя
   - get_primary_account() - получить основной аккаунт
   - add_account() - добавить новый аккаунт

### Данные для миграции:

**Pizzburg (основной):**
- Существующие данные из users таблицы
- is_primary = true
- account_name = "Pizzburg"

**Pizzburg-cafe (второй):**
- API Token: 881862:431800518a877398e5c4d1d3b9c76cee
- User ID: (из токена) 881862
- Base URL: https://joinposter.com/api (по умолчанию)
- is_primary = false
- account_name = "Pizzburg-cafe"

## Этап 2: Синхронизация (TODO)

1. Обновить sync_ingredients.py для работы с несколькими аккаунтами
2. Хранить справочники отдельно для каждого аккаунта
3. Обновить matchers.py для поиска в нескольких аккаунтах

## Этап 3: Умный matching (TODO)

1. Создать MultiAccountMatcher
2. Логика: поиск в Pizzburg → если не найдено → поиск в Pizzburg-cafe
3. Создание двух черновиков вместо одного

## Этап 4: UI (TODO)

1. Показывать оба черновика
2. Ручной поиск показывает результаты из обоих аккаунтов
3. Алиасы с привязкой к аккаунту
