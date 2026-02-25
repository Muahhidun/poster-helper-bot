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
| `shift_reconciliation` | Сверка смены по источникам (cash/kaspi/halyk) |
| `shift_closings` | Данные закрытия смены (ввод, Poster, расчёты, переводы, зарплаты) |
| `cashier_shift_data` | 5 значений от кассира (wolt, halyk, cash_bills, cash_coins, expenses) |
| `cashier_shift_details` | Детали зарплат кассира (имена, суммы, статус) |
| `web_users` | Веб-пользователи с ролями (owner/admin/cashier), bcrypt пароли |

### Ключевые колонки

**expense_drafts:**
- `amount` — сумма, которую может редактировать пользователь
- `poster_amount` — оригинальная сумма из Poster (для сравнения)
- `poster_transaction_id` — ID транзакции в Poster (для синхронизации)
- `source` — источник: 'cash', 'kaspi', 'halyk'
- `completion_status` — 'pending' → 'partial' → 'completed'

**supply_draft_items:**
- `item_type` — 'ingredient', 'semi_product', 'product'
- `poster_account_id` — к какому Poster-аккаунту относится ингредиент
- `poster_account_name` — название заведения
- `storage_id`, `storage_name` — склад

**supply_drafts:**
- `source` — метод оплаты: 'cash', 'kaspi', 'halyk'
- `linked_expense_draft_id` — связь с расходом (для сверки сумм)

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
- **Scheduled Jobs** — APScheduler: ежедневные транзакции (12:00), еженедельные отчёты (пн 12:00), месячные (1-е 12:00), мониторинг цен (пн 9:00); фоновая синхронизация расходов (каждые 5 мин)
- **Role-Based Auth** — Flask session с ролями owner/admin/cashier, bcrypt пароли, middleware `check_auth()`
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

### Зарплаты через веб-интерфейс
1. **Основной отдел:** Кассир вводит данные на `/cashier/shift-closing` → расчёт по таблице норм → подтверждение → транзакции в Poster
2. **Кафе:** Админ вводит имена + суммы на `/cafe/shift-closing` → транзакции в Poster (3 роли: Кассир, Сушист, Повар Сандей)
3. Защита от дублей: `salaries_created` флаг в БД

### Закрытие дня (автоматизировано)
1. Админ Кафе заполняет зарплаты + данные смены → автоперевод (инкассация, Wolt)
2. Кассир основного отдела заполняет зарплаты + 5 значений → данные появляются у владельца
3. Владелец проверяет, вводит Kaspi → нажимает "Скопировать отчёт" → автоперевод (инкассация, Wolt, Halyk, корректировка безнала)

## Known Issues & Solutions (История отладки)

### createSupply: типы ингредиентов и мульти-аккаунт (PRs #119–#128)

**Проблема:** При создании поставок через `finance.createSupply` Poster API возвращал ошибки 32 и 50. Работало для одного заведения (Pizzburg), но ломалось для другого (Pizzburg-cafe).

**Корневые причины (было несколько слоёв проблем):**

1. **Неправильный формат запроса к API.** Код отправлял плоские поля (`date`, `supplier_id`, `ingredients[N]`) вместо формата из документации (`supply[date]`, `supply[supplier_id]`, `ingredient[N]`). Также использовал `price` вместо `sum`. Для Pizzburg это работало по совпадению, для Pizzburg-cafe — нет.

2. **Путаница с type mapping.** Документация Poster противоречива — в разных местах типы описаны по-разному. После многих итераций выяснили рабочую схему:
   - В `createSupply` (формат docs): `ingredient=4`, `product=1`
   - В `createSupply` (формат legacy/flat): `ingredient=1`, `semi_product=2`, `product=4`
   - Финальное решение — fallback-стратегия: пробуем docs-формат, при ошибке 32 пробуем legacy, при повторной ошибке — смешанный формат.

3. **Кросс-аккаунтные ID ингредиентов.** У разных заведений (Pizzburg и Pizzburg-cafe) разные наборы ингредиентов с разными ID. Ингредиент "Фри" в Pizzburg имеет `id=83`, а в Pizzburg-cafe — `id=295`. Если пользователь добавлял ингредиент из одного аккаунта и отправлял в API другого — ошибка 32.

4. **Смешение пространств имён ingredients и products.** Poster использует отдельные ID для ингредиентов (`ingredient_id`) и товаров (`product_id`). Валидация объединяла их в один словарь, что давало ложные срабатывания.

5. **Тип не передавался по всему flow.** Тип ингредиента (ingredient/semi_product/product) корректно читался из Poster API при синхронизации, но терялся по пути: не сохранялся в CSV, не передавался через frontend, не записывался в БД черновиков.

**Решение (файлы, которые были изменены):**

- **`poster_client.py`** — метод `create_supply()`: 3 стратегии формата с fallback (docs → legacy → mixed). Каждая стратегия имеет свой `type_map`.
- **`web_app.py`** — `process_supply()`: раздельная валидация `valid_ingredient_ids` и `valid_product_ids`, фильтрация удалённых, определение типа каждого item перед отправкой. Привязка ингредиентов к конкретному аккаунту.
- **`sync_ingredients.py`** — сохранение поля `type` в CSV при синхронизации.
- **`matchers.py`** — `IngredientMatcher` загружает и возвращает `type` ингредиента.
- **`database.py`** — колонки `item_type`, `poster_account_id`, `poster_account_name`, `storage_id`, `storage_name` в таблице `supply_draft_items`.

**Как проверить работоспособность:**
- Создать поставку в веб-интерфейсе (`/supplies`) для каждого заведения
- Проверить с ингредиентом (Фри) и товаром (Кока-Кола 1л)
- В логах должно быть `✅ Supply created successfully: ID=XXXX`
- Типы в логах: ингредиент → `type=4` (docs формат), товар → `type=1`

### Poster API: формат createSupply

Рабочий формат (docs), используется как основной:
```
supply[date] = "2026-02-04 12:00:00"
supply[supplier_id] = 6
supply[storage_id] = 1
supply[supply_comment] = "Накладная от Смолл"
supply[account_id] = 5
ingredient[0][id] = 295
ingredient[0][type] = 4        # 4=ingredient, 1=product
ingredient[0][num] = 1         # количество
ingredient[0][sum] = 1440      # сумма за единицу (не price!)
```

Если docs-формат вернул ошибку 32, автоматически пробуется legacy-формат:
```
date, supplier_id, storage_id  (без supply[] обёртки)
ingredients[0][id], ingredients[0][type]=1, ingredients[0][num], ingredients[0][price]
```

## Текущий план и прогресс

### Что сделано

#### 1. Закрытие смены (Shift Closing) — ПОЛНОСТЬЮ ГОТОВО

**Backend файлы:**
- **`shift_closing.py`** — оркестрация: получение данных продаж, расчёт зарплат, создание транзакций
- **`cash_shift_closing.py`** — закрытие кассовой смены в 4 шага: получение данных Poster → расчёт итогов → коррекционные транзакции → отчёт
- **`cashier_salary.py`** — расчёт зарплат кассиров по таблице норм (зависит от выручки и количества кассиров 2/3)
- **`doner_salary.py`** — расчёт зарплаты донерщика по количеству проданных (10k-20k₸), бонусная система помощника
- **`salary_flow_handlers.py`** — диалоговый flow для ручного запуска зарплат (APScheduler триггерит в 21:30)

**Frontend (Mini App):**
- **`ShiftClosing.tsx`** — калькулятор закрытия смены с debounce-расчётом

**Поля ввода (все в тенге):**
| Поле | Назначение |
|------|-----------|
| `wolt` | Wolt терминал |
| `halyk` | Halyk терминал |
| `kaspi` | Kaspi терминал (общий) |
| `kaspi_cafe` | МИНУС Kaspi от PizzBurg-Cafe (вычитается из kaspi) |
| `cash_bills` | Наличка бумажными |
| `cash_coins` | Наличка мелочью |
| `shift_start` | Смена (остаток на начало) — подтягивается из Poster |
| `deposits` | Внесения |
| `expenses` | Расходы с кассы |
| `cash_to_leave` | Оставить на смену (по умолчанию 15000) |

**Формулы расчёта — Основной отдел (`web_app.py: api_shift_closing_calculate`):**
```python
# 1. Итого безнал факт = Wolt + Halyk + (Kaspi - Kaspi от Cafe)
fact_cashless = wolt + halyk + (kaspi - kaspi_cafe)

# 2. Фактический = безнал + наличка (бумажная + мелочь)
fact_total = fact_cashless + cash_bills + cash_coins

# 3. Итого фактический = Фактический - Смена - Внесения + Расходы
fact_adjusted = fact_total - shift_start - deposits + expenses

# 4. Итого Poster = Торговля - Бонусы
poster_total = poster_trade - poster_bonus

# 5. ИТОГО ДЕНЬ = Итого фактический - Итого Poster
day_result = fact_adjusted - poster_total  # >0 излишек, <0 недостача

# 6. Смена оставили = бумажные оставить + мелочь
shift_left = cash_to_leave + cash_coins

# 7. Разница безнала
cashless_diff = fact_cashless - poster_card

# 8. Инкассация = Бумажные - оставить + расходы + разница безнала
collection = cash_bills - cash_to_leave + expenses + cashless_diff
```

**Формулы расчёта — Кафе (`web_app.py: api_cafe_calculate`):**
```python
# Кафе: kaspi_pizzburg ПРИБАВЛЯЕТСЯ (курьеры через основной Kaspi), нет Halyk
fact_cashless = wolt + kaspi + kaspi_pizzburg
fact_total = fact_cashless + cash_bills + cash_coins
fact_adjusted = fact_total - shift_start + expenses  # нет deposits
# Остальные формулы идентичны основному отделу
```

**ВАЖНО — Логика инкассации и коррекции безнала:**

Инкассация (`collection`) — это **скорректированная** сумма, которую кассир/админ вводит в Poster POS при закрытии смены. Она уже включает поправку на `cashless_diff`, чтобы:
1. POS-смена в Poster закрывалась зелёной (без расхождений)
2. Вместе с переводом коррекции безнала — оба счёта (Безнал и Наличка) сходились с реальностью

**Знак `+cashless_diff` в формуле collection (НЕ минус!):**
- `cashless_diff > 0` (факт безнал > Poster): Poster думает что наличных в кассе больше, чем есть → инкассируем БОЛЬШЕ чтобы Poster увидел правильный остаток
- `cashless_diff < 0` (факт безнал < Poster): Poster думает что наличных меньше → инкассируем МЕНЬШЕ

**Пример (cashless_diff = +2 000₸):**
```
Ввод: Бумажные=45000, Мелочь=5000, Оставить=10000, Безнал факт=100000, Poster card=98000

Физическая инкассация: 45000 - 10000 = 35000₸
cashless_diff: 100000 - 98000 = +2000₸
collection (в отчёте): 35000 + 2000 = 37000₸  ← эту сумму кассир вводит в Poster

Poster POS: 10000 + 42000(нал по Poster) - 37000 = 15000 = факт в кассе ✓ GREEN

Переводы:
  1. Инкассация(2) → Оставил(5): 37000₸
  2. Оставил(5) → Каспий(1): 2000₸ (корр. безнала)

Счета после переводов:
  Безнал: 200000 + 98000 + 2000 = 300000 ✓ (совпадает с реальным 300000)
  Наличка: 20000 + 37000 - 2000 = 55000 ✓ (совпадает с реальным 55000)
```

**Кассир/админ не считает коррекцию вручную** — сайт показывает готовую цифру инкассации, которую нужно просто ввести в Poster.

Формула используется в 4 местах (должна быть `+cashless_diff` везде):
1. `web_app.py: api_shift_closing_calculate` — backend основного отдела
2. `web_app.py: api_cafe_calculate` — backend кафе
3. `shift_closing.html` — JS fallback в генерации отчёта
4. `shift_closing_cafe.html` — JS fallback в генерации отчёта кафе

**API endpoints:**
- `GET /api/shift-closing/poster-data` — получает trade_total, bonus, poster_card, shift_start, transactions_count из всех бизнес-аккаунтов
- `POST /api/shift-closing/calculate` — расчёт по формулам выше, возвращает calculations

**UI особенности:**
- Два блока: "Фактические данные" (ввод) и "Данные Poster" (readonly)
- Блок "ИТОГО ДЕНЬ" с цветовой индикацией: зелёный (излишек), красный (недостача)
- Блок "Инкассация" — итоговая **скорректированная** сумма для ввода в Poster POS
- Кнопка "Скопировать отчёт" → копирует + создаёт авто-переводы в Poster

**Страница Кассира (`/cashier/shift-closing`):**
- 4 шага: ввод данных для зарплат → подтверждение зарплат → 5 значений → готово
- Авторасчёт зарплат через `cashier_salary.py` и `doner_salary.py`
- Данные автоматически подтягиваются на страницу владельца

**Страница Админа Кафе (`/cafe/shift-closing`):**
- 2 шага: зарплаты (3 роли: Кассир, Сушист, Повар Сандей) → закрытие смены
- Авто-переводы при нажатии "Скопировать отчёт" (инкассация, Wolt, корректировка)

**Owner View:**
- На страницах Кассира и Админа видит все шаги одновременно (readonly)
- Может свободно переключаться между страницами без блокировки

**Система аутентификации:**
- Flask session с ролями: owner, admin, cashier
- bcrypt хеширование паролей (`web_users` таблица)
- Команда `/staff` в боте для управления пользователями
- Middleware `check_auth()` проверяет роль и доступ к аккаунту

**Авто-переводы при закрытии смены:**
- Основной отдел: инкассация→кассе, Каспий→Wolt, Каспий→Halyk, корректировка безнала
- Кафе: инкассация→кассе, Каспий→Wolt, корректировка безнала
- Защита от дублей: `transfers_created` флаг
- Корректировка безнала: автовыравнивание при разнице >0.5₸
- **ВАЖНО:** Инкассация (`collection`) уже включает `+cashless_diff` — в сумме с переводом коррекции это даёт правильные балансы обоих счетов и зелёную POS-смену

#### 2. Поставки (Supplies) — ПОЛНОСТЬЮ ГОТОВО

**Мульти-аккаунт исправлен (PRs #119–#128):**
- Fallback-стратегия форматов: docs → legacy → mixed (см. "Known Issues")
- Раздельная валидация ingredient/product ID
- Привязка ингредиентов к конкретному аккаунту

**Новые фичи:**
- **Селектор источника оплаты** (`selectedSource`: cash/kaspi/halyk) вместо аккаунта
- **Preload всех ингредиентов** без лимита 50 при загрузке страницы
- **Instant autocomplete** — данные уже загружены, поиск мгновенный
- **Dropdown открывается вверх** — fixed positioning для видимости
- **Автокомплит поставщиков** с fuzzy-поиском по алиасам
- **Повтор последней поставки** — загружает позиции предыдущей поставки от поставщика
- **Подсказки по последней цене** — кликабельные, подставляют цену

#### 3. Синхронизация расходов с Poster — готово
- Синхронизация **обновляет** существующие черновики если сумма в Poster изменилась
- Колонка `poster_amount` в `expense_drafts` — хранит сумму из Poster отдельно от пользовательской
- Логика: если пользователь вручную менял сумму — его правка сохраняется; если нет — обновляется вместе с Poster
- Жёлтая подсветка мисматчей в UI: badge "Poster: X₸"
- Авто-синхронизация каждые 5 минут

### Poster API — ID финансовых счетов (ВАЖНО!)

Для расчёта выручки по источникам используются ID из `dash.getTransactions`:
- **Kaspi** — несколько ID (1, 2, 6, ...) — нужно суммировать все non-cash счета кроме Halyk
- **Halyk** — отдельный ID
- **Cash** — наличка

**Код получения выручки (`web_app.py:3712+`):**
```python
# Totals for reconciliation (sum from ALL business accounts)
# Суммируем транзакции по account_id чтобы получить Kaspi, Halyk, Cash
```

### Бизнес-логика Pizzburg

**Два заведения:**
- **Pizzburg** (основной) — свои ингредиенты, поставщики, Kaspi терминал
- **Pizzburg-Cafe** — отдельный аккаунт в Poster, но **использует Kaspi терминал Pizzburg**

**Особенность:** При закрытии смены Pizzburg нужно вычесть `kaspi_cafe` (оплаты которые прошли через общий терминал, но относятся к Cafe).

### Оптимизация производительности

Страница `/expenses` и API `/api/expenses` оптимизированы:
- **Параллельные запросы** — все Poster-аккаунты загружаются через `asyncio.gather` одновременно (было последовательно)
- **Кэш категорий/счетов** — `_poster_cache` с TTL 5 минут (категории и счета меняются редко)
- **Транзакции только за выбранную дату** — вместо 30 дней (было раньше)
- То же для `/api/poster-transactions` — аккаунты загружаются параллельно

### Навигация и роли

- `base.html` — общий шаблон с навигацией, используется для `/expenses`, `/supplies`, `/shift-closing`
- `shift_closing_cafe.html` и `shift_closing_cashier.html` — standalone шаблоны с условной навигацией
- Навигация (Расходы/Поставки/Смена/Кассир/Кафе) показывается **только для owner** через `{% if user_role == 'owner' %}`
- Admin видит только `/cafe/*`, cashier только `/cashier/*` — middleware `check_role_access()`
- Стиль навигации одинаковый на всех страницах (`.modern-nav` из `base.html`)

### Известные ограничения / TODO

1. **`shift_start`** — берётся из Poster API, но не всегда доступен (fallback = 0). В UI есть поле для ручного ввода
2. **Poster API createSupply** — требует fallback-стратегии из-за противоречивой документации (см. "Known Issues")
3. **Rate limit на /login** — не реализован (Flask-Limiter), некритично для внутреннего инструмента
4. **Статистика** — endpoint `/api/statistics` возвращает mock-данные (TODO в web_app.py)
5. **Надбавка кассира за доставку** — 100₸ × количество заказов, зависит от API Poster
6. **`FLASK_SECRET_KEY`** — нужно задать в Railway env, иначе сессии теряются при каждом деплое

## Бизнес-флоу: Закрытие дня (подробное описание)

> Детальный план реализации и автоматизации — см. **PLAN.md**

### Контекст

Два заведения: **Pizzburg** (основной отдел) и **Pizzburg-Cafe** (кафе). У них общий Kaspi-терминал (физически в основном отделе). Halyk есть только в основном отделе. Закрытие дня происходит каждый вечер ~21:30-22:30.

### Шаг 1: Закрытие Кафе (~21:30) — автоматизировано через `/cafe/shift-closing`

1. Админ заходит на `/cafe/shift-closing` → вводит зарплаты (Кассир, Сушист, Повар Сандей: имена + суммы)
2. Нажимает "Далее" → транзакции зарплат создаются в Poster
3. Заполняет данные закрытия смены (Wolt, Kaspi, наличка, расходы и т.д.)
4. Нажимает "Скопировать отчёт" → **авто-переводы** создаются в Poster:
   - **Инкассация → Оставил в кассе** (на закупы)
   - **Каспий Пей → Вольт** (вся торговля Wolt за день)
   - **Корректировка безнала** (если разница >0.5₸)
   - ⚠️ Halyk в Кафе **нет**

### Шаг 2: Зарплаты основного отдела — через `/cashier/shift-closing`

1. Кассир заходит на `/cashier/shift-closing`
2. Вводит количество кассиров (2/3), имена, время помощника
3. Нажимает "Далее" → **авторасчёт** зарплат по таблицам норм
4. Подтверждает → транзакции создаются в Poster
5. Вводит 5 значений (Wolt, Halyk, бумажные, мелочь, расходы) → данные появляются у владельца

### Шаг 3: Закрытие основного отдела — Mini App владельца

1. Владелец проверяет данные от кассира (автозаполнены) и от Кафе (kaspi_cafe)
2. Вводит Kaspi (из банковского приложения) — единственное ручное поле
3. Расчёт разницы:
   - **Недостача** → создаём расход на эту сумму
   - **Излишек** → создаём внесение
4. Нажимает "Скопировать отчёт" → **авто-переводы** в Poster:
   - **Инкассация → Оставил в кассе** (скорректированная инкассация)
   - **Каспий → Вольт** (вся торговля Wolt за сегодня)
   - **Каспий → Халык** (вся торговля Halyk за сегодня)
   - **Корректировка безнала** (если разница >0.5₸)

### Шаг 4: Итоговая сверка

1. Пересчёт всех денег физически
2. Сравнение с суммой счетов двух отделов в Poster:
   - **Наличные** — сумма счетов "Оставил в кассе" обоих отделов
   - **Kaspi** — сумма счетов Kaspi обоих отделов
   - **Halyk** — только основной отдел
3. Если суммы примерно совпадают → смена закрыта
4. Если большая разница по какому-то счёту → пропущена транзакция или поставка → сверка расходов с галочками

### ID финансовых счетов Poster (для переводов)

**Основной отдел (Pizzburg) — `MAIN_ACCOUNTS` в `web_app.py`:**

| ID | Название | Используется |
|----|----------|-------------|
| 1  | Каспи Пей | Kaspi, переводы Kaspi→Wolt, Kaspi→Halyk |
| 2  | Halyk Bank | Halyk |
| 4  | Оставил в кассе | Наличка, зарплаты, инкассация |
| 5  | Денежный ящик кассира | Кассовая смена |

**Кафе (Pizzburg-cafe) — `CAFE_ACCOUNTS` в `web_app.py`:**

| ID | Название | Используется |
|----|----------|-------------|
| 1  | Каспи Пей | Kaspi, перевод Kaspi→Wolt |
| 2  | Инкассация | Источник перевода инкассации |
| 5  | Оставил в кассе | Наличка, зарплаты, цель инкассации |
| 7  | Wolt | Wolt доставка |

## Mini App Types (TypeScript)

Ключевые типы в `mini_app/src/types/index.ts`:

### Shift Closing (Закрытие смены)

```typescript
interface ShiftClosingPosterData {
  trade_total: number      // Торговля за день (без бонусов) - в тийинах
  bonus: number            // Бонусы (онлайн-оплата) - в тийинах
  poster_card: number      // Безнал в Poster (картой) - в тийинах
  shift_start: number      // Остаток на начало смены - в тийинах
  transactions_count: number
}

interface ShiftClosingCalculations {
  fact_cashless: number    // Итого безнал факт
  fact_total: number       // Фактический
  fact_adjusted: number    // Итого фактический
  poster_total: number     // Итого Poster
  day_result: number       // ИТОГО ДЕНЬ (>0 излишек, <0 недостача)
  shift_left: number       // Смена оставили
  collection: number       // Инкассация
  cashless_diff: number    // Разница безнала
}
```

### Поставки (Supplies)

```typescript
interface PosterItem {
  id: number
  name: string
  type: 'ingredient' | 'semi_product' | 'product'
  poster_account_id?: number    // К какому аккаунту Poster относится
  poster_account_name?: string  // Название заведения
  storage_id?: number           // ID склада
}

interface SupplyItemInput {
  id: number
  name: string
  type: 'ingredient' | 'semi_product' | 'product'
  quantity: number
  price: number
  unit: string
  poster_account_id?: number  // ВАЖНО: привязка к конкретному аккаунту
  lastPrice?: number          // Для подсказки о прошлой цене
}

interface CreateSupplyRequest {
  supplier_id: number
  source: ExpenseSource  // 'cash' | 'kaspi' | 'halyk'
  items: SupplyItemInput[]
}
```

### Расходы (Expenses)

```typescript
type ExpenseSource = 'cash' | 'kaspi' | 'halyk'
type ExpenseType = 'transaction' | 'supply'
type CompletionStatus = 'pending' | 'partial' | 'completed'

interface ExpenseDraft {
  id: number
  amount: number
  poster_amount: number | null  // Сумма из Poster (для сравнения)
  source: ExpenseSource
  completion_status: CompletionStatus
  is_income: boolean
}

interface AccountTotals {
  kaspi: number  // Сумма по Kaspi счетам
  halyk: number  // Сумма по Halyk счетам
  cash: number   // Остаток в кассе
}
```

## Ежедневные автоматические транзакции (12:00)

Файл `daily_transactions.py`, запускается APScheduler в 12:00 Asia/Almaty.

**Pizzburg (основной, счёт "Оставил в кассе" ID=4):**
| Категория | ID | Сумма | Комментарий |
|-----------|-----|-------|-------------|
| Повара | 17 | 1₸ | "Заготовка" |
| Повара | 17 | 1₸ | "Мадира Т" |
| Повара | 17 | 1₸ | "Нургуль Т" |
| КухРабочая | 18 | 1₸ | — |
| Курьер | 15 | 1₸ | "Курьеры" |
| Зарплаты | auto | 1₸ | "Мадина админ" |
| Логистика | 24 | 1₸ | "Караганда" |
| Логистика | 24 | 700₸ | "Фарш" |
| Логистика | 24 | 1000₸ | "Кюрдамир" |

**Pizzburg (счёт "Kaspi Pay" ID=1):**
| Категория | ID | Сумма | Комментарий |
|-----------|-----|-------|-------------|
| Маркетинг | 7 | 4100₸ | "Реклама" |
| Логистика | 24 | 1₸ | "Астана" |
| Банковские услуги | 5 | 1₸ | "Комиссия" |

**Pizzburg (перевод):**
- Оставил в кассе(4) → Деньги дома(5) — 1₸, "Забрал - Имя"

**Pizzburg-cafe (счёт "Оставил в кассе" ID=5):**
| Категория | ID | Сумма | Комментарий |
|-----------|-----|-------|-------------|
| Кассир | 16 | 1₸ | — |
| Сушист | 17 | 1₸ | — |
| Повар Сандей | auto | 1₸ | — |

> Категории с `auto` — ID определяется автоматически из API через `_find_category_id()`.

## Валюта и единицы

**ВАЖНО:** Poster API возвращает суммы в **тийинах** (1/100 тенге). При отображении в UI нужно делить на 100.

```python
# Backend: конвертация из тийинов в тенге
poster_trade = float(data.get('poster_trade', 0)) / 100

# Frontend: суммы уже в тенге после API
formatMoney(posterData.trade_total / 100)  // делим при отображении
```

## Code Style & Conventions

- Язык кода: Python, комментарии и строки — русский/английский
- Все handler-функции бота — async
- Poster API вызовы через `poster_client.py` (не напрямую)
- Матчинг через классы в `matchers.py` (CategoryMatcher, SupplierMatcher и др.)
- Черновики (drafts) — основной паттерн для расходов и поставок перед созданием в Poster
- Валюта по умолчанию — KZT, часовой пояс — Asia/Almaty
- Frontend: компоненты в `mini_app/src/pages/`, хуки в `mini_app/src/hooks/`

## Важные нюансы для разработки

### 1. Мульти-аккаунт Poster
- У пользователя может быть несколько бизнес-аккаунтов (Pizzburg, Pizzburg-Cafe)
- Каждый аккаунт имеет свои ингредиенты с уникальными ID
- При создании поставки нужно отправлять `poster_account_id` ингредиента
- Ошибка 32 от Poster API часто означает что ID ингредиента не существует в целевом аккаунте

### 2. Dropdown autocomplete в Mobile UI
- Используй `position: fixed` для dropdown чтобы не обрезался родительским контейнером
- Для мобильных экранов dropdown лучше открывать вверх (`bottom: 100%`)
- Preload данных — загружай все ингредиенты при входе на страницу, не на каждый keystroke

### 3. Debounce для расчётов
- Используй 300ms debounce для пересчёта при вводе в калькуляторах
- Показывай индикатор "Пересчёт..." во время расчёта

### 4. Синхронизация с Poster
- Автоматическая синхронизация расходов каждые 5 минут
- При синхронизации проверяй `poster_transaction_id` чтобы не дублировать
- Храни `poster_amount` отдельно от `amount` чтобы сохранять ручные правки пользователя
