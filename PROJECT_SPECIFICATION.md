# Poster Helper Bot — Полная спецификация проекта

> Этот документ описывает всю логику, механику и функции веб-приложения для управления финансовыми операциями ресторанного бизнеса через POS-систему Poster. Предназначен для воссоздания сервиса с нуля на новом сервере.

---

## 1. Обзор системы

### Что это
Веб-приложение + Telegram-бот для управления двумя ресторанными заведениями (**Pizzburg** — основной отдел, **Pizzburg-cafe** — кафе) через POS-систему [Poster](https://joinposter.com/). Один владелец (owner) управляет обоими заведениями.

### Основные функции
1. **Расходы** — синхронизация транзакций из Poster, создание новых, сверка по источникам оплаты
2. **Поставки** — создание поставок ингредиентов/товаров в Poster через веб-интерфейс
3. **Закрытие смены** — ежедневный расчёт итогов дня, автопереводы между счетами в Poster
4. **Зарплаты** — автоматический расчёт зарплат кассиров и донерщиков по нормам продаж
5. **Ежедневные транзакции** — автоматическое создание плановых расходов в 12:00

### Пользователи и роли
| Роль | Доступ | Описание |
|------|--------|----------|
| `owner` | Все страницы | Владелец бизнеса. Видит всё, может переключаться между страницами |
| `admin` | `/cafe/*` | Администратор кафе. Закрывает смену кафе, создаёт зарплаты кафе |
| `cashier` | `/cashier/*` | Кассир основного отдела. Вводит зарплаты и 5 значений смены |

---

## 2. Архитектура

### Tech Stack (текущий)
- **Backend:** Python 3.11, Flask 3.0 (sync + asyncio через `run_async()`), aiohttp
- **Database:** PostgreSQL (production, Railway), SQLite (local fallback)
- **Frontend:** Server-side HTML (Jinja2 templates) + vanilla JavaScript
- **POS:** Poster API (`https://{account}.joinposter.com/api`)
- **Scheduler:** APScheduler (ежедневные транзакции в 12:00, зарплаты в 21:30)
- **Auth:** Flask sessions, bcrypt пароли, role-based middleware

### Ключевой паттерн: `run_async()`
Flask синхронный, но Poster API вызовы асинхронные (aiohttp). Используется обёртка:
```python
def run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
```

### Мультитенантность
Все данные привязаны к `telegram_user_id` (ID пользователя Telegram). Один пользователь может иметь несколько бизнес-аккаунтов Poster (например Pizzburg + Pizzburg-cafe).

### Кэширование
In-memory кэш для категорий и финансовых счетов Poster (меняются редко):
```python
_poster_cache = {}
_CACHE_TTL = 300  # 5 минут
```

---

## 3. База данных — Полная схема

### 3.1 `users` — Пользователи
```sql
telegram_user_id  BIGINT PRIMARY KEY  -- Telegram ID
poster_token      TEXT NOT NULL        -- API-токен Poster (legacy, для обратной совместимости)
poster_user_id    TEXT NOT NULL        -- ID пользователя в Poster
poster_base_url   TEXT NOT NULL        -- https://{account}.joinposter.com/api
subscription_status TEXT DEFAULT 'trial'
subscription_expires_at TIMESTAMP
created_at        TIMESTAMP NOT NULL
updated_at        TIMESTAMP NOT NULL
```

### 3.2 `poster_accounts` — Бизнес-аккаунты Poster (мульти-аккаунт)
```sql
id                SERIAL PRIMARY KEY
telegram_user_id  BIGINT NOT NULL      -- FK → users
account_name      TEXT NOT NULL         -- "Pizzburg" или "Pizzburg-cafe"
poster_token      TEXT NOT NULL         -- API-токен этого аккаунта
poster_user_id    TEXT NOT NULL
poster_base_url   TEXT NOT NULL         -- https://pizz-burg.joinposter.com/api
is_primary        BOOLEAN DEFAULT false -- true для основного отдела
created_at        TIMESTAMP
updated_at        TIMESTAMP
UNIQUE(telegram_user_id, account_name)
```

### 3.3 `web_users` — Пользователи веб-интерфейса
```sql
id                SERIAL PRIMARY KEY
telegram_user_id  BIGINT NOT NULL      -- FK → users (к какому владельцу относится)
username          TEXT UNIQUE NOT NULL  -- Логин
password_hash     TEXT NOT NULL         -- bcrypt хеш
role              TEXT NOT NULL         -- CHECK('owner', 'admin', 'cashier')
label             TEXT                  -- Отображаемое имя
poster_account_id INTEGER              -- К какому аккаунту привязан (для admin/cashier)
is_active         BOOLEAN DEFAULT TRUE
created_at        TIMESTAMP
last_login        TIMESTAMP
```

### 3.4 `expense_drafts` — Черновики расходов
```sql
id                    SERIAL PRIMARY KEY
telegram_user_id      BIGINT NOT NULL
amount                DECIMAL(12,2) NOT NULL    -- Сумма (может быть изменена пользователем)
poster_amount         REAL                      -- Оригинальная сумма из Poster (для сравнения)
description           TEXT NOT NULL              -- Комментарий / название расхода
expense_type          TEXT DEFAULT 'transaction' -- 'transaction' или 'supply'
category              TEXT                       -- Название категории расхода
source                TEXT DEFAULT 'cash'        -- 'cash', 'kaspi', 'halyk'
account_id            INTEGER                    -- ID финансового счёта в Poster
poster_account_id     INTEGER                    -- К какому бизнес-аккаунту относится
poster_transaction_id TEXT                       -- ID транзакции в Poster (для синхронизации)
is_income             INTEGER DEFAULT 0          -- 1 = доход (не расход)
completion_status     TEXT DEFAULT 'pending'     -- 'pending' → 'partial' → 'completed'
status                TEXT DEFAULT 'pending'     -- 'pending' или 'processed'
created_at            TIMESTAMP
processed_at          TIMESTAMP
```
**Логика статусов:**
- `completion_status = 'pending'` — не обработан (чёрный цвет)
- `completion_status = 'partial'` — создан в Poster, но не оплачен (жёлтый)
- `completion_status = 'completed'` — полностью завершён (зелёная галочка)
- Если `amount ≠ poster_amount` → жёлтый badge "Poster: X₸"

### 3.5 `supply_drafts` — Черновики поставок
```sql
id                       SERIAL PRIMARY KEY
telegram_user_id         BIGINT NOT NULL
supplier_name            TEXT              -- Название поставщика
supplier_id              INTEGER           -- ID поставщика в Poster
invoice_date             DATE
total_sum                DECIMAL(12,2)     -- Общая сумма (рассчитывается автоматически)
source                   TEXT DEFAULT 'cash' -- Метод оплаты: 'cash', 'kaspi', 'halyk'
status                   TEXT DEFAULT 'pending'
linked_expense_draft_id  INTEGER           -- FK → expense_drafts (связь с расходом)
ocr_text                 TEXT              -- Распознанный текст накладной
created_at               TIMESTAMP
processed_at             TIMESTAMP
```

### 3.6 `supply_draft_items` — Позиции поставки
```sql
id                     SERIAL PRIMARY KEY
supply_draft_id        INTEGER NOT NULL     -- FK → supply_drafts
item_name              TEXT NOT NULL         -- Название из накладной
quantity               DECIMAL(10,3) DEFAULT 1
unit                   TEXT DEFAULT 'шт'
price_per_unit         DECIMAL(12,2) DEFAULT 0
total                  DECIMAL(12,2) DEFAULT 0  -- quantity × price_per_unit
poster_ingredient_id   INTEGER              -- ID ингредиента/товара в Poster
poster_ingredient_name TEXT                  -- Название в Poster
item_type              TEXT                  -- 'ingredient', 'semi_product', 'product'
poster_account_id      INTEGER              -- К какому аккаунту Poster относится
poster_account_name    TEXT                  -- Название заведения
storage_id             INTEGER              -- ID склада в Poster
storage_name           TEXT                  -- Название склада
```

### 3.7 `shift_closings` — История закрытий смены
```sql
id                  SERIAL PRIMARY KEY
telegram_user_id    BIGINT NOT NULL
date                DATE NOT NULL
poster_account_id   INTEGER          -- NULL = основной, NOT NULL = кафе

-- Введённые значения (тенге):
wolt                REAL DEFAULT 0
halyk               REAL DEFAULT 0
kaspi               REAL DEFAULT 0
kaspi_cafe          REAL DEFAULT 0   -- Касп от кафе (вычитается из kaspi у основного)
kaspi_pizzburg      REAL DEFAULT 0   -- Касп от основного (прибавляется у кафе)
cash_bills          REAL DEFAULT 0
cash_coins          REAL DEFAULT 0
shift_start         REAL DEFAULT 0
deposits            REAL DEFAULT 0
expenses            REAL DEFAULT 0
cash_to_leave       REAL DEFAULT 15000

-- Данные Poster (тенге):
poster_trade        REAL DEFAULT 0
poster_bonus        REAL DEFAULT 0
poster_card         REAL DEFAULT 0
poster_cash         REAL DEFAULT 0
transactions_count  INTEGER DEFAULT 0

-- Расчётные значения (тенге):
fact_cashless       REAL DEFAULT 0
fact_total          REAL DEFAULT 0
fact_adjusted       REAL DEFAULT 0
poster_total        REAL DEFAULT 0
day_result          REAL DEFAULT 0
shift_left          REAL DEFAULT 0
collection          REAL DEFAULT 0
cashless_diff       REAL DEFAULT 0

-- Флаги:
salaries_created    BOOLEAN DEFAULT FALSE
salaries_data       TEXT              -- JSON массив [{role, name, amount}]
transfers_created   BOOLEAN DEFAULT FALSE

created_at          TIMESTAMP
updated_at          TIMESTAMP
```
**UNIQUE constraint:** Два partial unique indexes:
- `(telegram_user_id, date) WHERE poster_account_id IS NULL` — один основной на дату
- `(telegram_user_id, date, poster_account_id) WHERE poster_account_id IS NOT NULL` — один кафе на дату

### 3.8 `cashier_shift_data` — Данные кассира для закрытия смены
```sql
id                    SERIAL PRIMARY KEY
telegram_user_id      BIGINT NOT NULL
date                  DATE NOT NULL

-- Шаг 1: Зарплаты
cashier_count         INTEGER            -- 2 или 3 кассира
cashier_names         TEXT               -- JSON массив имён ["Имя1", "Имя2"]
assistant_start_time  TEXT               -- "10:00", "12:00" или "14:00"
doner_name            TEXT
assistant_name        TEXT
salaries_data         TEXT               -- JSON [{role, name, salary}]
salaries_created      BOOLEAN DEFAULT FALSE

-- Шаг 2: 5 значений смены
wolt                  REAL DEFAULT 0
halyk                 REAL DEFAULT 0
cash_bills            REAL DEFAULT 0
cash_coins            REAL DEFAULT 0
expenses              REAL DEFAULT 0
shift_data_submitted  BOOLEAN DEFAULT FALSE

created_at            TIMESTAMP
updated_at            TIMESTAMP
UNIQUE(telegram_user_id, date)
```

### 3.9 `shift_reconciliation` — Сверка смены по источникам
```sql
id                SERIAL PRIMARY KEY
telegram_user_id  BIGINT NOT NULL
date              DATE NOT NULL
source            TEXT NOT NULL          -- 'cash', 'kaspi', 'halyk'
opening_balance   REAL                   -- Фактический баланс (вводит пользователь)
closing_balance   REAL
total_difference  REAL                   -- Разница с Poster
notes             TEXT
created_at        TIMESTAMP
updated_at        TIMESTAMP
UNIQUE(telegram_user_id, date, source)
```

### 3.10 `ingredient_aliases` — Алиасы ингредиентов
```sql
id                SERIAL PRIMARY KEY
telegram_user_id  BIGINT NOT NULL
alias_text        TEXT NOT NULL           -- "Фри 10кг" → ингредиент "Картофель Фри"
poster_item_id    INTEGER NOT NULL        -- ID в Poster
poster_item_name  TEXT NOT NULL           -- Название в Poster
source            TEXT DEFAULT 'user'     -- 'user', 'auto', 'ocr'
notes             TEXT
created_at        TIMESTAMP
UNIQUE(telegram_user_id, alias_text)
```

### 3.11 `supplier_aliases` — Алиасы поставщиков
```sql
id                    SERIAL PRIMARY KEY
telegram_user_id      BIGINT NOT NULL
alias_text            TEXT NOT NULL        -- "ИП Федорова" → "Кока-Кола"
poster_supplier_id    INTEGER NOT NULL
poster_supplier_name  TEXT NOT NULL
notes                 TEXT
created_at            TIMESTAMP
UNIQUE(telegram_user_id, alias_text)
```

### 3.12 `ingredient_price_history` — История цен ингредиентов
```sql
id                SERIAL PRIMARY KEY
telegram_user_id  BIGINT NOT NULL
ingredient_id     INTEGER NOT NULL
ingredient_name   TEXT
supplier_id       INTEGER
supplier_name     TEXT
date              DATE NOT NULL
price             DECIMAL(10,2) NOT NULL
quantity          DECIMAL(10,3)
unit              TEXT
supply_id         INTEGER
created_at        TIMESTAMP
```

### 3.13 `shipment_templates` — Шаблоны поставок
```sql
id                SERIAL PRIMARY KEY
telegram_user_id  BIGINT NOT NULL
template_name     TEXT NOT NULL
supplier_id       INTEGER NOT NULL
supplier_name     TEXT NOT NULL
account_id        INTEGER NOT NULL
account_name      TEXT NOT NULL
storage_id        INTEGER DEFAULT 1
items             TEXT NOT NULL          -- JSON массив [{id, name, quantity, unit, price}]
created_at        TIMESTAMP
updated_at        TIMESTAMP
UNIQUE(telegram_user_id, template_name)
```

### 3.14 `employees` — Сотрудники
```sql
id                SERIAL PRIMARY KEY
telegram_user_id  BIGINT NOT NULL
employee_name     TEXT NOT NULL
role              TEXT NOT NULL          -- 'cashier', 'doner', 'assistant' и др.
last_mentioned_date DATE
created_at        TIMESTAMP
UNIQUE(telegram_user_id, employee_name, role)
```

### 3.15 `daily_transactions_log` — Лог ежедневных транзакций
```sql
id                SERIAL PRIMARY KEY
telegram_user_id  BIGINT NOT NULL
date              DATE NOT NULL
count             INTEGER DEFAULT 0     -- Сколько транзакций создано
created_at        TIMESTAMP
UNIQUE(telegram_user_id, date)
```

### 3.16 `user_settings` — Настройки пользователя
```sql
telegram_user_id      BIGINT PRIMARY KEY
language              TEXT DEFAULT 'ru'
timezone              TEXT DEFAULT 'UTC+6'
notifications_enabled INTEGER DEFAULT 1
```

---

## 4. Аутентификация и авторизация

### Механизм
1. Пользователь заходит на `/login`, вводит логин + пароль
2. Сервер проверяет bcrypt хеш в таблице `web_users`
3. При успехе создаётся Flask session с полями:
   - `web_user_id` — ID в таблице web_users
   - `role` — 'owner', 'admin', 'cashier'
   - `telegram_user_id` — ID владельца (для запросов к БД)
   - `poster_account_id` — к какому аккаунту Poster привязан (для admin/cashier)
   - `label` — отображаемое имя
4. Session lifetime: 30 дней (`permanent_session_lifetime`)

### Middleware `check_auth()`
Выполняется **перед каждым запросом** (`@app.before_request`):
1. Пропускает открытые пути: `/login`, `/static`, `/health`, `/telegram-webhook`, `/mini-app`
2. Пропускает API-запросы с Telegram Init Data (для Mini App)
3. Если нет session → redirect `/login` (или 401 для API)
4. Проверяет доступ роли к пути через `check_role_access()`

### Матрица доступа ролей
| Путь | owner | admin | cashier |
|------|-------|-------|---------|
| `/expenses`, `/supplies`, `/shift-closing` | ✅ | ❌ | ❌ |
| `/cafe/*`, `/api/cafe/*` | ✅ | ✅ | ❌ |
| `/cashier/*`, `/api/cashier/*` | ✅ | ❌ | ✅ |
| `/logout` | ✅ | ✅ | ✅ |

### Redirect по ролям
- `owner` → `/` (→ `/expenses`)
- `admin` → `/cafe/shift-closing`
- `cashier` → `/cashier/shift-closing`

### Rate limiting
`/login` — 10 запросов в минуту (Flask-Limiter, in-memory storage)

---

## 5. API Endpoints — Полный список

### 5.1 Расходы (Expenses)

#### `GET /expenses` — Страница расходов
Отображает черновики расходов за выбранную дату. Загружает из Poster: категории, финансовые счета, транзакции за дату. Показывает сверку балансов (факт vs Poster) по трём источникам.

**Query params:** `?date=YYYY-MM-DD` (default: сегодня по Asia/Almaty)

**Логика загрузки:**
1. Получает все drafts из БД (`status="all"`)
2. Фильтрует по дате (`created_at[:10] == selected_date`)
3. Параллельно загружает из всех Poster-аккаунтов: категории, счета, транзакции
4. Кэширует категории/счета на 5 минут
5. Суммирует балансы по типам: kaspi (все счета с "kaspi"), halyk ("халык"/"halyk"), cash ("оставил")

**Баланс считается в тенге** (Poster возвращает в тийинах, делим на 100).

#### `POST /expenses/sync-from-poster` — Синхронизация расходов из Poster
Загружает транзакции за сегодня из Poster и создаёт/обновляет черновики.

**Логика синхронизации:**
1. Для каждого аккаунта Poster — параллельно загружает транзакции + финансовые счета
2. Пропускает транзакции типа `2` (переводы), категории "Переводы", "Кассовые смены", "Актуализация"
3. Для каждой транзакции:
   - Если черновик с таким `poster_transaction_id` уже есть → обновляет сумму/описание (если изменилось в Poster)
   - Если это поставка (`Поставка №12685`) → пропускает (уже учтена через supply_draft)
   - Иначе → создаёт новый черновик с `completion_status='completed'`
4. Определяет `source` (cash/kaspi/halyk) по названию финансового счёта
5. Определяет `is_income` по типу транзакции или категории ("приход", "поступлен")
6. Удаляет черновики, чьи `poster_transaction_id` больше не существуют в Poster (транзакция удалена)

**Защита ручных правок:** Если пользователь вручную менял `amount` и оно отличается от `poster_amount` → сумма НЕ перезаписывается.

#### `POST /expenses/create` — Создать черновик расхода вручную
```json
{
  "amount": 5000,
  "description": "Канцтовары",
  "expense_type": "transaction",
  "category": "Канцелярия",
  "source": "cash",
  "account_id": 4,
  "poster_account_id": 1
}
```

#### `POST /expenses/process` — Создать транзакции в Poster
Принимает список `draft_ids`, для каждого создаёт транзакцию в Poster через API `finance.createTransaction`.

**Для типа `transaction`:**
```python
poster_client.create_transaction(
    transaction_type=0,  # expense (1 = income)
    category_id=...,     # ID категории
    account_from_id=..., # ID финансового счёта
    amount=...,          # в тенге
    date=...,            # "YYYY-MM-DD HH:MM:SS"
    comment=...          # описание
)
```

**Для типа `supply`:** перенаправляет на обработку поставки.

#### `POST /expenses/toggle-type/<draft_id>` — Переключить тип расхода
Переключает между `transaction` и `supply`. При переключении на `supply` → автоматически создаёт связанный `supply_draft`.

#### `POST /expenses/update/<draft_id>` — Обновить черновик
Обновляет amount, description, category, source, account_id.

#### `POST /expenses/delete/<draft_id>` — Удалить черновик
#### `POST /expenses/delete` — Удалить выбранные черновики (массовое удаление)

#### `POST /api/shift-reconciliation` — Сохранить сверку
```json
{
  "source": "cash",
  "fact_balance": 150000,
  "date": "2026-02-26"
}
```

### 5.2 Поставки (Supplies)

#### `GET /supplies` — Страница поставок
Показывает pending черновики поставок. Предзагружает ВСЕ ингредиенты из CSV для мгновенного автокомплита.

**Данные для autocomplete:**
1. Из CSV `poster_ingredients.csv` — все ингредиенты всех аккаунтов
2. Из CSV `poster_products.csv` — только товары из категории "Напитки"
3. Поставщики из CSV `poster_suppliers.csv`

#### `POST /supplies/create` — Создать пустой черновик поставки
Создаёт пустой draft, возвращает его ID. Пользователь потом добавляет items.

#### `POST /supplies/add-item/<draft_id>` — Добавить позицию
```json
{
  "poster_ingredient_id": 295,
  "poster_ingredient_name": "Картофель Фри",
  "item_type": "ingredient",
  "poster_account_id": 2,
  "poster_account_name": "Pizzburg-cafe",
  "storage_id": 1,
  "storage_name": "Основной склад",
  "quantity": 10,
  "price_per_unit": 1440
}
```

#### `POST /supplies/update-item/<item_id>` — Обновить позицию
```json
{ "quantity": 5, "price_per_unit": 1500 }
```

#### `POST /supplies/process/<draft_id>` — Создать поставку в Poster
**Критическая логика (мульти-аккаунт):**

1. Группирует items по `poster_account_id`
2. Для каждой группы создаёт отдельный запрос к Poster API соответствующего аккаунта
3. Параллельно загружает справочные данные: поставщики, финансовые счета, склады, ингредиенты, товары
4. Определяет `supplier_id` по fuzzy-matching названия
5. Определяет `account_id` (финансовый счёт) по `source`:
   - `kaspi` → счёт с "kaspi" в названии
   - `halyk` → счёт с "халык"/"halyk"
   - `cash` → счёт с "закуп"/"оставил"
6. **Валидация типов** — раздельные namespace для ingredients и products в Poster:
   - `valid_ingredient_ids` — словарь ingredient_id → (name, type)
   - `valid_product_ids` — словарь product_id → name
   - Автокоррекция типа если ID найден в другом namespace
7. Вызывает `poster_client.create_supply()` с fallback-стратегией

#### Poster API `create_supply` — 3 стратегии формата

**Стратегия 1 — docs format (основная):**
```
supply[date] = "2026-02-26 12:00:00"
supply[supplier_id] = 6
supply[storage_id] = 1
supply[supply_comment] = "Накладная"
supply[account_id] = 5
ingredient[0][id] = 295
ingredient[0][type] = 4          # 4=ingredient, 1=product
ingredient[0][num] = 10
ingredient[0][sum] = 14400       # цена × количество
transactions[0][account_id] = 5  # финансовый счёт
transactions[0][date] = "2026-02-26 12:00:00"
transactions[0][amount] = 14400  # общая сумма
transactions[0][delete] = 0
```
Type mapping docs: `ingredient=4`, `product=1`

**Стратегия 2 — legacy format (fallback при ошибке 32):**
```
date = "2026-02-26 12:00:00"
supplier_id = 6
storage_id = 1
ingredients[0][id] = 295
ingredients[0][type] = 1         # 1=ingredient, 4=product
ingredients[0][num] = 10
ingredients[0][price] = 14400
```
Type mapping legacy: `ingredient=1`, `semi_product=2`, `product=4`

**Стратегия 3 — mixed (последний fallback):** docs формат с legacy type mapping.

#### `GET /api/supplies/last/<supplier_id>` — Последняя поставка поставщика
Возвращает items последней поставки для функции "Повторить".

#### `GET /api/items/price-history/<item_id>` — История цен ингредиента
Последние 5 цен для подсказки.

### 5.3 Закрытие смены — Основной отдел (Owner)

#### `GET /shift-closing` — Страница закрытия смены

#### `GET /api/shift-closing/poster-data?date=YYYYMMDD` — Данные из Poster
Загружает из **основного** (primary) аккаунта:
1. Транзакции за дату через `dash.getTransactions`
2. Фильтрует закрытые заказы (`status == '2'`)
3. Суммирует: `payed_cash`, `payed_card`, `payed_sum`
4. Рассчитывает: `bonus = payed_sum - payed_cash - payed_card`, `trade_total = payed_cash + payed_card`
5. Предыдущая смена: `getCashShifts` за предыдущий день → `amount_end` последнего шифта

**Business day logic:** До 6:00 по Asia/Almaty → используется вчерашняя дата.

**Дополнительно автозаполняет:**
- `cafe_kaspi_pizzburg` — из сохранённого закрытия кафе (сколько Kaspi прошло через основной терминал)
- Данные кассира (`wolt`, `halyk`, `cash_bills`, `cash_coins`, `expenses`) — из `cashier_shift_data`

**Response (суммы в тийинах):**
```json
{
  "success": true,
  "date": "20260226",
  "transactions_count": 145,
  "trade_total": 120000000,
  "bonus": 5000000,
  "poster_card": 80000000,
  "poster_cash": 40000000,
  "poster_prev_shift_left": 1500000,
  "cafe_kaspi_pizzburg": 15000,
  "cashier_wolt": 30000,
  "cashier_halyk": 20000,
  "cashier_cash_bills": 45000,
  "cashier_cash_coins": 5000,
  "cashier_expenses": 8000,
  "cashier_data_submitted": true
}
```

#### `POST /api/shift-closing/calculate` — Расчёт итогов
**Формулы основного отдела:**
```python
# Входные данные (тенге):
wolt, halyk, kaspi, kaspi_cafe, cash_bills, cash_coins,
shift_start, expenses, deposits, cash_to_leave

# Poster данные (тийины → тенге, делим на 100):
poster_trade, poster_bonus, poster_card

# 1. Безнал факт = Wolt + Halyk + (Kaspi - Kaspi от Cafe)
fact_cashless = wolt + halyk + (kaspi - kaspi_cafe)

# 2. Фактический = безнал + наличка
fact_total = fact_cashless + cash_bills + cash_coins

# 3. Итого фактический = Фактический - Смена + Расходы
fact_adjusted = fact_total - shift_start + expenses

# 4. Итого Poster = Торговля - Бонусы
poster_total = poster_trade - poster_bonus

# 5. ИТОГО ДЕНЬ = Итого фактический - Итого Poster
day_result = fact_adjusted - poster_total  # >0 излишек, <0 недостача

# 6. Смена оставили = оставить бумажными + мелочь
shift_left = cash_to_leave + cash_coins

# 7. Разница безнала = факт безнал - Poster карта
cashless_diff = fact_cashless - poster_card

# 8. Инкассация = бумажные - оставить + расходы + разница безнала
collection = cash_bills - cash_to_leave + expenses + cashless_diff
```

**ВАЖНО: Знак `+cashless_diff` в инкассации:**
- `cashless_diff > 0` → факт безнала > Poster → инкассируем БОЛЬШЕ
- `cashless_diff < 0` → факт безнала < Poster → инкассируем МЕНЬШЕ
- Вместе с переводом коррекции безнала → оба счёта выравниваются

#### `POST /api/shift-closing/save` — Сохранить данные закрытия
Сохраняет все введённые и расчётные значения в `shift_closings`.

#### `POST /api/shift-closing/transfers` — Создать автопереводы
**Переводы основного отдела (Pizzburg):**

| Перевод | Откуда (ID) | Куда (ID) | Сумма | Условие |
|---------|-------------|-----------|-------|---------|
| Инкассация → Оставил | 2 (Инкассация) | 4 (Оставил в кассе) | `collection` | `collection > 0` |
| Каспий → Вольт | 1 (Каспий) | Wolt | `wolt` | `wolt > 0` |
| Каспий → Халык | 1 (Каспий) | 2 (Halyk) | `halyk` | `halyk > 0` |
| Корр. безнала: Каспий → Оставил | 1 | 4 | `abs(cashless_diff)` | `cashless_diff < -0.5` |
| Корр. безнала: Оставил → Каспий | 4 | 1 | `cashless_diff` | `cashless_diff > 0.5` |

**MAIN_ACCOUNTS:**
```python
{'kaspi': 1, 'inkassacia': 2, 'cash_left': 4, 'halyk': 2, 'wolt': ?}
```

Защита от дублей: флаг `transfers_created` в `shift_closings`.

#### `GET /api/shift-closing/report` — Генерация текстового отчёта
Возвращает форматированный текст для копирования.

#### `GET /api/shift-closing/history` — История закрытий
#### `GET /api/shift-closing/dates` — Даты с данными (для календаря)

### 5.4 Закрытие смены — Кафе (Admin)

#### `GET /cafe/shift-closing` — Страница кафе

#### `GET /api/cafe/poster-data?date=YYYYMMDD` — Данные из Poster кафе
Аналогично основному, но для кафе-аккаунта. Дополнительно автозаполняет `main_kaspi_cafe`.

#### `POST /api/cafe/calculate` — Расчёт кафе
**Формулы кафе (отличия от основного):**
```python
# Нет Halyk. kaspi_pizzburg ПРИБАВЛЯЕТСЯ (доставки через основной Kaspi)
fact_cashless = wolt + kaspi + kaspi_pizzburg
fact_total = fact_cashless + cash_bills + cash_coins
fact_adjusted = fact_total - shift_start + expenses  # нет deposits
# Остальные формулы идентичны
```

#### `POST /api/cafe/salaries/create` — Создать зарплаты кафе
3 роли: Кассир (cat_id=16), Сушист (cat_id=17), Повар Сандей (auto-detect).
Все транзакции со счёта `CAFE_ACCOUNT_FROM = 5` (Оставил в кассе).

```json
{
  "salaries": [
    {"role": "Кассир", "name": "Асель", "amount": 7000},
    {"role": "Сушист", "name": "Ержан", "amount": 8000},
    {"role": "Повар Сандей", "name": "Айгуль", "amount": 6000}
  ]
}
```

#### `POST /api/cafe/transfers` — Автопереводы кафе
**CAFE_ACCOUNTS:**
```python
{'kaspi': 1, 'inkassacia': 2, 'cash_left': 5, 'wolt': 7}
```

| Перевод | Откуда | Куда | Сумма |
|---------|--------|------|-------|
| Инкассация → Оставил | 2 | 5 | `collection` |
| Каспий → Вольт | 1 | 7 | `wolt` |
| Корр. безнала | 1↔5 | | `cashless_diff` |

**Нет Halyk в кафе!**

### 5.5 Закрытие смены — Кассир (Cashier)

#### `GET /cashier/shift-closing` — Страница кассира
4 шага: ввод данных → авторасчёт зарплат → подтверждение → 5 значений смены.

#### `POST /api/cashier/salaries/calculate` — Расчёт зарплат (без создания)
```json
{
  "cashier_count": 2,
  "assistant_start_time": "12:00"
}
```
**Response:**
```json
{
  "cashier_salary": 8000,
  "doner_salary": 12750,
  "assistant_salary": 8000
}
```

#### `POST /api/cashier/salaries/create` — Создать зарплаты
Создаёт транзакции в Poster для каждого кассира, донерщика и помощника.

**Кассиры:** категория 16 (Кассиры), счёт 4 (Оставил в кассе)
**Донерщик + Помощник:** категория 19 (Донерщик), счёт 4

#### `POST /api/cashier/shift-data/save` — Сохранить 5 значений кассира
```json
{
  "wolt": 30000,
  "halyk": 20000,
  "cash_bills": 45000,
  "cash_coins": 5000,
  "expenses": 8000
}
```
Эти данные автоматически подтягиваются на страницу владельца.

### 5.6 Алиасы ингредиентов

#### `GET /api/aliases` — Список алиасов
#### `POST /api/aliases` — Создать алиас
#### `PUT /api/aliases/<id>` — Обновить
#### `DELETE /api/aliases/<id>` — Удалить

### 5.7 Поиск

#### `GET /api/items/search?q=фри&limit=20` — Поиск ингредиентов/товаров
Поиск по CSV-файлам с fuzzy-matching.

#### `GET /api/suppliers/search?q=смолл` — Поиск поставщиков
Поиск по CSV + алиасам с fuzzy-matching.

#### `GET /api/categories/search?q=повар` — Поиск категорий расходов

---

## 6. Бизнес-логика: Зарплаты

### 6.1 Зарплата кассиров

Зависит от общей суммы продаж за день (`payed_cash + payed_card`, без бонусов) и количества кассиров.

**Таблица норм для 2 кассиров:**

| Продажи (тенге) | Зарплата каждого |
|-----------------|------------------|
| 0 — 599,999 | 6,000₸ |
| 600,000 — 699,999 | 7,000₸ |
| 700,000 — 799,999 | 8,000₸ |
| 800,000 — 899,999 | 9,000₸ |
| 900,000 — 999,999 | 10,000₸ |
| 1,000,000 — 1,099,999 | 11,000₸ |
| 1,100,000 — 1,199,999 | 12,000₸ |
| 1,200,000 — 1,299,999 | 13,000₸ |
| 1,300,000 — 1,399,999 | 14,000₸ |
| 1,400,000 — 1,499,999 | 15,000₸ |
| 1,500,000 — 1,599,999 | 16,000₸ |
| 1,600,000 — 1,699,999 | 17,000₸ |
| 1,700,000 — 1,799,999 | 18,000₸ |
| 1,800,000 — 1,899,999 | 19,000₸ |
| 1,900,000+ | 20,000₸ |

**Таблица норм для 3 кассиров:**

| Продажи (тенге) | Зарплата каждого |
|-----------------|------------------|
| 0 — 799,999 | 6,000₸ |
| 800,000 — 899,999 | 7,000₸ |
| 900,000 — 999,999 | 8,000₸ |
| ...каждые 100к₸ → +1,000₸ | |
| 2,100,000+ | 20,000₸ |

### 6.2 Зарплата донерщика

Зависит от количества проданных донеров за день.

**Подсчёт донеров:**
1. Все товары из категории ID=6 ("Донер") → `category_count`
2. Товары "Комбо Донер" (keywords: "комбо" + "донер") → `combo_count`
3. Товары "Донерная пицца" (keywords: "донер" + "пицц", или точное "Донерная") → `pizza_count`
4. `total_count = category_count + combo_count + pizza_count`

API: `dash.getProductsSales` с фильтром по дате.

**Таблица норм:**

| Количество | Зарплата |
|-----------|----------|
| 0 — 199 | 10,000₸ |
| 200 — 219 | 11,000₸ |
| 220 — 239 | 12,000₸ |
| ...каждые +20 → +1,000₸ | |
| 380+ | 20,000₸ |

### 6.3 Помощник донерщика

Зарплата зависит от времени выхода на работу:

| Время выхода | Зарплата помощника | Бонус донерщику |
|-------------|-------------------|----------------|
| 10:00 | 9,000₸ | 0₸ |
| 12:00 | 8,000₸ | +750₸ |
| 14:00 | 7,000₸ | +1,500₸ |

**Итого донерщик = базовая (по таблице) + бонус (за позднего помощника)**

### 6.4 Категории Poster для зарплат

**Основной отдел (Pizzburg):**
- Кассиры → category_id=16, account_from_id=4
- Донерщик → category_id=19, account_from_id=4

**Кафе (Pizzburg-cafe):**
- Кассир → category_id=16, account_from_id=5
- Сушист → category_id=17, account_from_id=5
- Повар Сандей → auto-detect (`_find_category_id('повар', 'санд')`), account_from_id=5

---

## 7. Бизнес-логика: Ежедневные транзакции (12:00)

Запускаются APScheduler в 12:00 Asia/Almaty. Только для аккаунта **Pizzburg** (зарплаты кафе создаются при закрытии смены).

### Защита от дублей
1. Флаг в БД (`daily_transactions_log`) — предотвращает повторный запуск при рестартах
2. Per-transaction проверка в Poster API — пропускает существующие (по комментарию или category_id)

### Транзакции Pizzburg

**Со счёта "Оставил в кассе" (ID=4):**

| Категория | Cat ID | Сумма | Комментарий |
|-----------|--------|-------|-------------|
| Повара | 17 | 1₸ | "Заготовка" |
| Повара | 17 | 1₸ | "Мадира Т" |
| Повара | 17 | 1₸ | "Нургуль Т" |
| КухРабочая | 18 | 1₸ | (пусто) |
| Курьер | 15 | 1₸ | "Курьеры" |
| Зарплаты | auto | 1₸ | "Мадина админ" |
| Логистика | 24 | 1₸ | "Караганда" |
| Логистика | 24 | 700₸ | "Фарш" |
| Логистика | 24 | 1000₸ | "Кюрдамир" |

**Со счёта "Kaspi Pay" (ID=1):**

| Категория | Cat ID | Сумма | Комментарий |
|-----------|--------|-------|-------------|
| Маркетинг | 7 | 4100₸ | "Реклама" |
| Логистика | 24 | 1₸ | "Астана" |
| Банковские услуги | 5 | 1₸ | "Комиссия" |

**Перевод:**
- Оставил в кассе (4) → Деньги дома (5) — 1₸, "Забрал - Имя"

> Суммы в 1₸ — это плейсхолдеры, реальные суммы вводятся вручную через расходы.

---

## 8. Бизнес-флоу: Закрытие дня (пошагово)

### Шаг 1: Закрытие кафе (~21:30)
1. **Админ** заходит на `/cafe/shift-closing`
2. Вводит зарплаты (Кассир, Сушист, Повар Сандей: имена + суммы)
3. Нажимает "Далее" → транзакции зарплат создаются в Poster
4. Заполняет данные закрытия: Wolt, Kaspi, Kaspi от Pizzburg, наличка, расходы
5. Нажимает "Скопировать отчёт" → автопереводы: инкассация, Wolt, корректировка безнала

### Шаг 2: Зарплаты основного отдела
1. **Кассир** заходит на `/cashier/shift-closing`
2. Выбирает количество кассиров (2/3), вводит имена
3. Выбирает время помощника (10:00/12:00/14:00), вводит имена донерщика и помощника
4. Нажимает "Далее" → авторасчёт зарплат по таблицам норм
5. Подтверждает → транзакции создаются в Poster
6. Вводит 5 значений (Wolt, Halyk, бумажные, мелочь, расходы)
7. Данные автоматически подтягиваются на страницу владельца

### Шаг 3: Закрытие основного отдела
1. **Владелец** заходит на `/shift-closing`
2. Видит автозаполненные данные от кассира + kaspi_cafe от кафе
3. Вводит Kaspi (из банковского приложения) — единственное ручное поле
4. Видит расчёт: day_result > 0 = излишек (зелёный), < 0 = недостача (красный)
5. Нажимает "Скопировать отчёт" → автопереводы: инкассация, Wolt, Halyk, корректировка

### Шаг 4: Сверка
Пересчёт физических денег → сравнение с суммами счетов обоих отделов в Poster.

---

## 9. Poster API — Используемые эндпоинты

Base URL: `https://{account}.joinposter.com/api`
Auth: `?token={poster_token}` в query params

### Транзакции
| Метод | Endpoint | Описание |
|-------|----------|----------|
| GET | `finance.getTransactions` | Транзакции за период (dateFrom, dateTo) |
| POST | `finance.createTransaction` | Создать расход/доход/перевод |
| GET | `finance.getAccounts` | Финансовые счета (Kaspi, Cash, Halyk...) |
| GET | `finance.getCategories` | Категории расходов (Повара, Кассиры...) |

### Поставки
| Метод | Endpoint | Описание |
|-------|----------|----------|
| POST | `storage.createSupply` | Создать поставку (формат form-urlencoded!) |
| GET | `storage.getSupplies` | Список поставок |
| GET | `storage.getStorages` | Склады |

### Меню и ингредиенты
| Метод | Endpoint | Описание |
|-------|----------|----------|
| GET | `menu.getIngredients` | Все ингредиенты |
| GET | `menu.getProducts` | Все товары |

### Поставщики
| Метод | Endpoint | Описание |
|-------|----------|----------|
| GET | `suppliers.getSuppliers` | Список поставщиков |

### Продажи (Dashboard)
| Метод | Endpoint | Описание |
|-------|----------|----------|
| GET | `dash.getTransactions` | Заказы за период (payed_cash, payed_card, payed_sum) |
| GET | `dash.getProductsSales` | Продажи по товарам (для расчёта зарплаты донерщика) |

### Кассовые смены
| Метод | Endpoint | Описание |
|-------|----------|----------|
| GET | `finance.getCashShifts` | Кассовые смены (amount_end = остаток при закрытии) |

### Важные форматы

**createTransaction:**
```json
{
  "type": 0,           // 0=expense, 1=income, 2=transfer
  "category_id": 16,
  "account_id": 4,     // account_from_id
  "account_to_id": 5,  // только для type=2 (перевод)
  "amount_from": 8000, // в тенге (целые)
  "date": "2026-02-26 21:30:00",
  "comment": "Кассир Асель"
}
```

**createSupply** — form-urlencoded (НЕ JSON!), см. раздел 5.2.

### Валюта
Poster API возвращает суммы в **тийинах** (1/100 тенге). При отображении делить на 100. При отправке `createTransaction` — суммы в **тенге**.

---

## 10. ID финансовых счетов Poster

### Основной отдел (Pizzburg)

| ID | Название | Назначение |
|----|----------|-----------|
| 1 | Каспи Пей | Kaspi терминал, переводы Kaspi→Wolt, Kaspi→Halyk |
| 2 | Halyk Bank / Инкассация | Halyk терминал, источник инкассации |
| 4 | Оставил в кассе | Наличка, зарплаты, цель инкассации |
| 5 | Деньги дома | Отложенные деньги |

### Кафе (Pizzburg-cafe)

| ID | Название | Назначение |
|----|----------|-----------|
| 1 | Каспи Пей | Kaspi |
| 2 | Инкассация | Источник инкассации |
| 5 | Оставил в кассе | Наличка, зарплаты, цель инкассации |
| 7 | Wolt | Wolt доставка |

**Особенность:** Общий Kaspi-терминал у обоих заведений (физически в основном отделе). При закрытии основного → вычитается `kaspi_cafe`. При закрытии кафе → прибавляется `kaspi_pizzburg`.

---

## 11. Валидация данных (Pydantic)

Все API-эндпоинты с POST/PUT используют Pydantic-модели через декоратор `@validate_json(ModelClass)`.

### Enums
```python
ExpenseSource = 'cash' | 'kaspi' | 'halyk'
ExpenseType = 'transaction' | 'supply'
CompletionStatus = 'pending' | 'partial' | 'completed'
ItemType = 'ingredient' | 'semi_product' | 'product'
```

### Ключевые модели
- `CreateExpenseRequest(amount, description, expense_type, category, source, account_id, poster_account_id)`
- `UpdateExpenseRequest(amount?, description?, category?, source?, account_id?, completion_status?)`
- `CreateSupplyRequest(supplier_id, supplier_name, items: [SupplyItem], source, date?)`
- `SupplyItem(id, quantity, price, name, unit, item_type?, poster_account_id?, storage_id?)`
- `ShiftClosingCalculateRequest(wolt, halyk, kaspi, kaspi_cafe, cash_bills, cash_coins, shift_start, expenses, deposits, cash_to_leave, poster_trade, poster_bonus, poster_card)`
- `CafeSalariesRequest(salaries: [{role, name, amount}])`
- `CashierSalaryCalcRequest(cashier_count: 2|3, assistant_start_time: "10:00"|"12:00"|"14:00")`

---

## 12. Переменные окружения

| Переменная | Обязательная | Описание |
|-----------|-------------|----------|
| `TELEGRAM_BOT_TOKEN` | Да | Токен Telegram-бота |
| `ALLOWED_USER_IDS` | Да | ID пользователей через запятую |
| `ADMIN_USER_IDS` | Нет | ID администраторов |
| `POSTER_ACCOUNT` | Да | Имя аккаунта Poster (pizz-burg) |
| `POSTER_TOKEN` | Да | API-токен Poster |
| `POSTER_USER_ID` | Нет | ID пользователя Poster (default: 22) |
| `OPENAI_API_KEY` | Да | Whisper + GPT-4 Vision |
| `ANTHROPIC_API_KEY` | Да | Claude для парсинга текста |
| `DATABASE_URL` | Нет | PostgreSQL URL (без него → SQLite) |
| `FLASK_SECRET_KEY` | Да (prod) | Секрет Flask sessions |
| `WEBHOOK_URL` | Нет | URL вебхука Telegram |
| `USE_WEBHOOK` | Нет | true/false |
| `DEFAULT_WAREHOUSE_ID` | Нет | ID склада по умолчанию (1) |
| `DEFAULT_ACCOUNT_FROM_ID` | Нет | ID счёта по умолчанию (4) |
| `DEFAULT_CASH_TO_LEAVE` | Нет | Оставить в кассе (15000) |
| `CURRENCY` | Нет | Валюта (KZT) |
| `TIMEZONE` | Нет | Часовой пояс (Asia/Almaty) |
| `GOOGLE_CLOUD_PROJECT_ID` | Нет | Google Cloud проект (для OCR) |
| `GOOGLE_APPLICATION_CREDENTIALS_JSON` | Нет | Google SA credentials |
| `GOOGLE_DOCAI_OCR_PROCESSOR_ID` | Нет | Document AI Processor ID |

---

## 13. Синхронизация данных

### Ингредиенты (`sync_ingredients.py`)
- Загружает из всех аккаунтов Poster через `menu.getIngredients`
- Сохраняет в CSV: `poster_ingredients.csv` (ingredient_id, ingredient_name, unit, type, poster_account_id, poster_account_name, storage_id, storage_name)
- Поле `type`: '1'=ingredient, '2'=semi_product

### Товары (`sync_products.py`)
- Загружает через `menu.getProducts`
- Сохраняет в CSV: `poster_products.csv` (product_id, product_name, category_name, ...)
- Для поставок используются только товары из категории "Напитки"

### Поставщики (`sync_suppliers.py`)
- Загружает через `suppliers.getSuppliers`
- Сохраняет в CSV: `poster_suppliers.csv`

### Автосинхронизация расходов
APScheduler каждые 5 минут вызывает sync для каждого пользователя.

---

## 14. Страницы и навигация

### Навигация (только для owner)
```
Расходы | Поставки | Смена | Кассир | Кафе
```
- `/expenses` — Черновики расходов
- `/supplies` — Черновики поставок
- `/shift-closing` — Закрытие смены (основной)
- `/cashier/shift-closing` — Страница кассира
- `/cafe/shift-closing` — Страница кафе

### Шаблоны
- `base.html` — общий layout с навигацией
- `expenses.html` — расходы (extends base.html)
- `supplies.html` — поставки (extends base.html)
- `shift_closing.html` — закрытие смены (extends base.html)
- `shift_closing_cafe.html` — кафе (standalone, условная навигация для owner)
- `shift_closing_cashier.html` — кассир (standalone, условная навигация для owner)
- `login.html` — страница входа

---

## 15. Особенности и ограничения

### Бизнес-день
До 6:00 по Asia/Almaty → считается вчерашним днём.

### Poster API `createSupply`
Требует form-urlencoded (не JSON!). Используется fallback из 3 стратегий из-за противоречивой документации Poster.

### Мульти-аккаунт
Разные аккаунты имеют разные наборы ингредиентов с разными ID. Ингредиент "Фри" может иметь id=83 в Pizzburg и id=295 в Pizzburg-cafe. Нужно отправлять на правильный API.

### Кэш
Категории и финансовые счета кэшируются на 5 минут (in-memory). При деплое кэш сбрасывается.

### Защита от дублей
- Зарплаты: `salaries_created` флаг в `shift_closings` и `cashier_shift_data`
- Переводы: `transfers_created` флаг в `shift_closings`
- Ежедневные транзакции: `daily_transactions_log` таблица + per-transaction проверка в Poster API
- Синхронизация расходов: `poster_transaction_id` для связи с Poster

### Часовой пояс
Всё время — Asia/Almaty (UTC+5). Используется `_kz_now()` через `time.time() + 5*3600` для надёжности.
