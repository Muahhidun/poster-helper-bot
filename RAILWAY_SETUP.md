# Настройка Railway для постоянного хранения данных

## Проблема
Railway использует эфемерную файловую систему - при каждом деплое все файлы удаляются. База данных SQLite (`data/users.db`) теряется, и пользователи должны регистрироваться заново.

## Решение: Railway Volume

Railway Volumes - это постоянное хранилище, которое сохраняется между деплоями.

### Шаги настройки:

#### 1. Создать Volume в Railway Dashboard

1. Открыть проект на railway.app
2. Перейти в Settings проекта
3. Найти раздел "Volumes"
4. Нажать "New Volume"
5. Заполнить:
   - **Mount Path**: `/data`
   - **Name**: `poster-bot-data`
6. Сохранить

#### 2. Добавить переменную окружения

В разделе "Variables" проекта Railway добавить:

```
RAILWAY_VOLUME_MOUNT_PATH=/data
```

Railway автоматически установит эту переменную, когда создаётся Volume с mount path `/data`.

#### 3. Проверить другие переменные окружения

Убедиться, что все необходимые переменные установлены:

**Обязательные:**
- `TELEGRAM_BOT_TOKEN` - токен Telegram бота
- `POSTER_TOKEN` - токен Poster API (будет переопределён при регистрации пользователя)
- `ANTHROPIC_API_KEY` - ключ Claude API
- `OPENAI_API_KEY` - ключ OpenAI API (для Whisper транскрипции)
- `ALLOWED_USER_IDS` - список разрешённых Telegram ID через запятую

**Опциональные:**
- `POSTER_ACCOUNT` - имя аккаунта Poster (по умолчанию "pizz-burg")
- `POSTER_USER_ID` - ID пользователя Poster (по умолчанию "22")
- `LOG_LEVEL` - уровень логирования (по умолчанию "INFO")

#### 4. Задеплоить изменения

После изменения кода в `database.py` запушить в GitHub:

```bash
git add database.py RAILWAY_SETUP.md
git commit -m "Add Railway Volume support for persistent database"
git push
```

Railway автоматически задеплоит новую версию.

#### 5. Перерегистрироваться один раз

После настройки Volume нужно один раз зарегистрироваться заново в боте:
1. Отправить `/start` боту
2. Ввести токен Poster

После этого регистрация сохранится навсегда, даже после деплоев.

## Структура данных на Volume

После настройки Volume структура будет:

```
/data/                           # Railway Volume (постоянное хранилище)
├── users.db                     # База данных пользователей (SQLite)
└── users/                       # Папки с данными пользователей
    └── {telegram_id}/
        ├── alias_category_mapping.csv
        ├── alias_item_mapping.csv
        ├── poster_accounts.csv
        ├── poster_suppliers.csv
        ├── poster_ingredients.csv
        └── poster_products.csv
```

## Проверка работоспособности

После деплоя можно проверить логи Railway:

```
✅ Database initialized: /data/users.db
```

Если видишь эту строку - всё настроено правильно!

## Troubleshooting

### База данных всё равно сбрасывается

1. Проверь, что Volume создан в Railway Dashboard
2. Проверь, что Mount Path = `/data`
3. Проверь переменную `RAILWAY_VOLUME_MOUNT_PATH=/data`
4. Перезапусти деплой

### Ошибка "Permission denied" при записи в /data

Railway должен автоматически дать права на запись. Если ошибка всё равно есть:
1. Проверь логи Railway на наличие других ошибок
2. Попробуй пересоздать Volume

### Как посмотреть содержимое Volume

К сожалению, Railway не даёт прямого доступа к Volume через SSH. Можно добавить временный эндпоинт в бота для проверки:

```python
# Временный хендлер для проверки
@bot.message_handler(commands=['checkdb'])
async def check_db(message):
    import os
    db_path = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "data") + "/users.db"
    exists = os.path.exists(db_path)
    size = os.path.getsize(db_path) if exists else 0
    await message.reply(f"DB exists: {exists}, size: {size} bytes")
```
