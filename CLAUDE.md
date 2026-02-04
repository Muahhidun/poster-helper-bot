# CLAUDE.md

## Project Overview

**Poster Helper Bot** — Telegram-бот и веб-приложение для управления финансовыми операциями ресторанного бизнеса через POS-систему Poster. Включает распознавание голоса, AI-парсинг, OCR накладных и React Mini App.

## Tech Stack

**Backend:** Python 3.11, python-telegram-bot 22.5, Flask 3.0, aiohttp, APScheduler, psycopg2/sqlite3, Pydantic
**AI/ML:** Anthropic Claude API (парсинг), OpenAI Whisper (голос→текст), GPT-4 Vision (OCR), Google Document AI (OCR)
**Frontend (Mini App):** React 18.2, TypeScript 5.2, Vite 5.0, TanStack Query/Table, Tailwind CSS 3.4, Radix UI, Chart.js
**Deployment:** Docker, Railway (PostgreSQL), webhook/polling режимы

## Project Structure

```
├── bot.py                    # Главный Telegram-бот (~6500 строк, все команды и обработчики)
├── start_server.py           # Запуск Flask + Telegram webhook
├── config.py                 # Конфигурация (env-переменные, пути)
├── database.py               # Мультитенантный слой БД (~2970 строк, SQLite/PostgreSQL)
├── web_app.py                # Flask веб-приложение (~3570 строк, 30+ маршрутов)
│
├── poster_client.py          # Клиент Poster API (мульти-аккаунт)
├── sync_ingredients.py       # Синхронизация ингредиентов из Poster
├── sync_products.py          # Синхронизация товаров
├── sync_suppliers.py         # Синхронизация поставщиков
├── sync_accounts.py          # Синхронизация бизнес-аккаунтов
│
├── parser_service.py         # Парсер через Claude API
├── simple_parser.py          # Фолбэк regex-парсер
├── matchers.py               # Fuzzy-matching (категории, поставщики, ингредиенты)
├── invoice_processor.py      # Оркестрация OCR накладных
├── invoice_ocr.py            # GPT-4 Vision + Document AI OCR
├── advanced_supply_parser.py # Парсинг поставок
├── expense_input.py          # Workflow ввода расходов
│
├── shift_closing.py          # Закрытие смены с расчётом зарплат
├── cash_shift_closing.py     # Закрытие кассовой смены
├── cashier_salary.py         # Расчёт зарплаты кассиров
├── doner_salary.py           # Расчёт зарплаты донерщика
├── salary_flow_handlers.py   # Автоматические зарплатные потоки (21:30)
├── daily_transactions.py     # Ежедневные транзакции (12:00)
├── weekly_report.py          # Еженедельные отчёты
├── monthly_report.py         # Ежемесячные отчёты
├── shipment_templates.py     # Шаблоны поставок
├── price_monitoring.py       # Мониторинг цен ингредиентов
│
├── mini_app/                 # React Mini App
│   ├── src/
│   │   ├── App.tsx           # Роутинг
│   │   ├── pages/            # Страницы (Dashboard, Expenses, Supplies, Aliases, Templates, ShiftClosing)
│   │   ├── hooks/            # useTelegram, useApi, useExpenses, useSupplyDrafts
│   │   ├── api/client.ts     # Типизированный API-клиент
│   │   ├── components/       # UI-компоненты
│   │   └── styles/           # Tailwind + кастомные стили
│   ├── package.json
│   └── vite.config.ts
│
├── templates/                # HTML-шаблоны Flask (expenses.html, supplies.html и др.)
├── data/                     # CSV-файлы маппингов (алиасы, ингредиенты, поставщики)
├── test_*.py                 # 17 тестовых файлов
├── requirements.txt          # 52 Python-зависимости
├── Dockerfile                # Multi-stage сборка
├── Procfile                  # Railway
└── railway.json              # Railway конфиг
```

## Build & Run

```bash
# Установка Python-зависимостей
pip install -r requirements.txt

# Сборка Mini App
cd mini_app && npm install && npm run build && cd ..

# Запуск бота (polling-режим для разработки)
python3 bot.py

# Запуск сервера (бот + Flask вместе)
python3 start_server.py

# Docker
docker build -t poster-helper-bot .
docker run -p 8080:8080 --env-file .env poster-helper-bot
```

## Testing

Тесты — автономные скрипты без фреймворка. Запуск:

```bash
python3 test_api.py
python3 test_supply.py
python3 test_shift_closing.py
python3 test_cashier_salary.py
python3 test_doner_salary.py
python3 test_ingredient_matcher.py
python3 test_templates.py
python3 test_db_aliases.py
```

## Key Environment Variables

```
TELEGRAM_BOT_TOKEN          # Токен Telegram-бота
ALLOWED_USER_IDS            # ID пользователей через запятую
ADMIN_USER_IDS              # ID администраторов
POSTER_ACCOUNT              # Имя аккаунта Poster (например pizz-burg)
POSTER_TOKEN                # API-токен Poster
POSTER_USER_ID              # ID пользователя Poster
OPENAI_API_KEY              # Ключ OpenAI (Whisper, GPT-4 Vision)
ANTHROPIC_API_KEY           # Ключ Anthropic (Claude для парсинга)
GOOGLE_CLOUD_PROJECT_ID     # Google Cloud проект
GOOGLE_APPLICATION_CREDENTIALS_JSON  # Cредетнешалы Google (JSON-строка)
GOOGLE_DOCAI_OCR_PROCESSOR_ID       # Процессор Document AI
DATABASE_URL                # PostgreSQL URL (Railway); без него — SQLite
WEBHOOK_URL                 # URL вебхука для Railway
USE_WEBHOOK                 # true/false
FLASK_SECRET_KEY            # Секрет Flask
DEFAULT_WAREHOUSE_ID        # ID склада по умолчанию (1)
DEFAULT_ACCOUNT_FROM_ID     # ID счёта по умолчанию (4)
CURRENCY                    # Валюта (KZT)
TIMEZONE                    # Часовой пояс (Asia/Almaty)
```

## Database

- **PostgreSQL** в продакшене (Railway, через `DATABASE_URL`)
- **SQLite** локально (автоматический фолбэк)
- Все операции привязаны к `telegram_user_id` (мультитенантность)

### Основные таблицы

| Таблица | Назначение |
|---------|-----------|
| `users` | Пользователи с Poster-токенами и подпиской |
| `poster_accounts` | Мульти-аккаунт Poster (1 пользователь → N аккаунтов) |
| `user_settings` | Настройки (язык, часовой пояс, уведомления) |
| `ingredient_aliases` | Пользовательские алиасы ингредиентов |
| `supplier_aliases` | Алиасы поставщиков |
| `shipment_templates` | Шаблоны повторяющихся поставок |
| `ingredient_price_history` | История цен ингредиентов |
| `employees` | Сотрудники для расчёта зарплат |
| `expense_drafts` | Черновики расходов (pending → partial → completed) |
| `supply_drafts` | Черновики поставок |
| `supply_draft_items` | Позиции черновиков поставок |

## API Integrations

### Poster POS API
- Base: `https://{account}.joinposter.com/api`
- `finance.createTransaction` / `finance.getTransactions` — транзакции
- `menu.getIngredients` / `menu.getProducts` — меню
- `suppliers.getSuppliers` — поставщики
- `finance.createSupply` — поставки
- `dash.getTransactions` — данные продаж

### Telegram Bot API
- Команды: `/start`, `/help`, `/sync`, `/cancel` и др.
- Обработчики: голос, текст, фото, документы
- Inline/reply клавиатуры, callback queries
- Webhook (продакшн) или polling (разработка)

### OpenAI
- **Whisper** — голос → текст
- **GPT-4 Vision** — OCR фото накладных

### Anthropic Claude
- Извлечение структурированных данных из текста (суммы, категории, комментарии)

### Google Document AI
- OCR для накладных (гибрид с GPT-4 для парсинга результатов)

## Architecture & Patterns

- **Мультитенантность** — все данные привязаны к `telegram_user_id`
- **Async/await** — все API-вызовы асинхронные (aiohttp, python-telegram-bot)
- **Декораторы** — `@authorized_only`, `@admin_only` для контроля доступа
- **Стратегия** — Claude-парсер с фолбэком на regex; множественные матчеры (fuzzy matching через rapidfuzz)
- **State Machine** — черновики расходов/поставок: pending → partial → completed
- **Scheduled Jobs** — APScheduler: ежедневные транзакции (12:00), зарплаты (21:30), еженедельные/месячные отчёты
- **Repository** — `UserDatabase` абстрагирует SQL-операции, поддерживает SQLite и PostgreSQL

## Key Workflows

### Голосовое сообщение → Транзакция
1. Voice → Whisper API → текст
2. Текст → Claude API → {сумма, категория, комментарий}
3. CategoryMatcher (fuzzy) → ID категории Poster
4. Предпросмотр → подтверждение → `PosterClient.create_transaction()`

### Фото накладной → Поставка
1. Фото → Google Document AI / GPT-4 Vision → OCR текст
2. Парсинг: поставщик, позиции, количества, цены
3. IngredientMatcher → привязка к ингредиентам Poster
4. Создание supply_draft → редактирование в веб-интерфейсе → подтверждение → `create_all_supplies()`

### Автоматический расчёт зарплат (21:30)
1. APScheduler → запрос имён сотрудников
2. Парсинг имён → расчёт зарплат по продажам
3. Создание транзакций (кассиры, донерщик, помощник)

## Code Style & Conventions

- Язык кода: Python, комментарии и строки — русский/английский
- Все handler-функции бота — async
- Poster API вызовы через `poster_client.py` (не напрямую)
- Матчинг через классы в `matchers.py` (CategoryMatcher, SupplierMatcher и др.)
- Черновики (drafts) — основной паттерн для расходов и поставок перед созданием в Poster
- Валюта по умолчанию — KZT, часовой пояс — Asia/Almaty
- Frontend: компоненты в `mini_app/src/pages/`, хуки в `mini_app/src/hooks/`
