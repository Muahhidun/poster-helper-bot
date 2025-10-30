# Настройка Railway для постоянного хранения данных

## Проблема
Railway использует эфемерную файловую систему - при каждом деплое все файлы удаляются. База данных SQLite (`data/users.db`) теряется, и пользователи должны регистрироваться заново.

## Решение: PostgreSQL

Railway предоставляет бесплатную PostgreSQL базу данных, которая сохраняется между деплоями.

### Шаги настройки:

#### 1. Добавить PostgreSQL в проект Railway

1. Открой свой проект на railway.app
2. Нажми **"+ New"** справа вверху
3. Выбери **"Database" → "Add PostgreSQL"**
4. Railway создаст новый сервис PostgreSQL и автоматически добавит переменную `DATABASE_URL` в твой проект

#### 2. Связать PostgreSQL с ботом

Railway автоматически добавит переменную `DATABASE_URL` во все сервисы проекта.

Чтобы проверить:
1. Перейди в свой сервис (worker)
2. Зайди в **Variables**
3. Должна появиться переменная `DATABASE_URL` (начинается с `postgres://`)

#### 3. Проверить другие переменные окружения

Убедись, что все необходимые переменные установлены:

**Обязательные:**
- `TELEGRAM_BOT_TOKEN` - токен Telegram бота
- `POSTER_TOKEN` - токен Poster API (будет переопределён при регистрации пользователя)
- `ANTHROPIC_API_KEY` - ключ Claude API
- `OPENAI_API_KEY` - ключ OpenAI API (для Whisper транскрипции)
- `ALLOWED_USER_IDS` - список разрешённых Telegram ID через запятую
- `DATABASE_URL` - (автоматически добавляется Railway)

**Опциональные:**
- `POSTER_ACCOUNT` - имя аккаунта Poster (по умолчанию "pizz-burg")
- `POSTER_USER_ID` - ID пользователя Poster (по умолчанию "22")
- `LOG_LEVEL` - уровень логирования (по умолчанию "INFO")

#### 4. Задеплоить изменения

Изменения уже запушены в GitHub. Railway автоматически задеплоит новую версию.

#### 5. Зарегистрироваться один раз

После настройки PostgreSQL:
1. Отправь `/start` боту
2. Введи токен Poster

**Всё! После этого регистрация сохранится НАВСЕГДА** - даже после деплоев.

## Как это работает

Бот автоматически определяет тип базы данных:

- **На Railway** (есть `DATABASE_URL`): использует PostgreSQL
- **Локально** (нет `DATABASE_URL`): использует SQLite в `data/users.db`

Никаких дополнительных настроек не нужно!

## Структура данных

### База данных пользователей (PostgreSQL/SQLite)

Таблица `users`:
- `telegram_user_id` - ID пользователя в Telegram
- `poster_token` - токен Poster API
- `poster_user_id` - ID пользователя в Poster
- `poster_base_url` - URL API Poster
- `subscription_status` - статус подписки (trial/active/expired)
- `subscription_expires_at` - дата окончания подписки
- `created_at` - дата создания
- `updated_at` - дата обновления

### Файлы CSV (эфемерные на Railway)

CSV файлы с алиасами и данными хранятся в файловой системе и создаются заново при каждом деплое. Это нормально, потому что они генерируются автоматически из Poster API при регистрации.

```
data/                           # Локально или эфемерно на Railway
├── users/                      # Папки с данными пользователей
    └── {telegram_id}/
        ├── alias_category_mapping.csv    # Авто-генерируется
        ├── alias_item_mapping.csv        # Авто-генерируется
        ├── poster_accounts.csv           # Авто-генерируется
        ├── poster_suppliers.csv          # Авто-генерируется
        ├── poster_ingredients.csv        # Авто-генерируется
        └── poster_products.csv           # Авто-генерируется
```

## Проверка работоспособности

После деплоя посмотри логи Railway:

### Успешное подключение к PostgreSQL:
```
Using PostgreSQL database
✅ PostgreSQL database initialized
```

### Локальная разработка (SQLite):
```
Using SQLite database at data/users.db
✅ SQLite database initialized: data/users.db
```

## Troubleshooting

### База данных всё равно сбрасывается

1. Проверь, что PostgreSQL добавлен в проект
2. Проверь переменную `DATABASE_URL` в Variables
3. Посмотри логи - должно быть "Using PostgreSQL database"
4. Перерегистрируйся в боте

### Ошибка подключения к PostgreSQL

1. Проверь, что `DATABASE_URL` правильный (должен начинаться с `postgres://` или `postgresql://`)
2. Проверь, что PostgreSQL сервис запущен (зелёный статус в Railway)
3. Посмотри логи PostgreSQL на наличие ошибок

### Ошибка "psycopg2 not found"

1. Проверь, что `requirements.txt` содержит `psycopg2-binary==2.9.9`
2. Перезапусти деплой в Railway

### CSV файлы пропадают после деплоя

Это нормально! CSV файлы генерируются автоматически при регистрации пользователя. Главное - что база данных PostgreSQL сохраняется и пользователю не нужно регистрироваться заново.

## Преимущества PostgreSQL

✅ Данные сохраняются между деплоями
✅ Бесплатно на Railway
✅ Надёжнее чем SQLite для production
✅ Поддержка concurrent запросов
✅ Автоматические бэкапы от Railway
✅ Работает локально со SQLite (без изменений кода)
