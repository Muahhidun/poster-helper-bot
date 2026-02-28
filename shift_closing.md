# Страница Закрытие смены — Полная спецификация backend

> Этот документ описывает **всю** backend-логику 3 страниц закрытия смены:
> 1. **Основной отдел** (`/shift-closing`) — для owner
> 2. **Кафе** (`/cafe/shift-closing`) — для admin
> 3. **Кассир** (`/cashier/shift-closing`) — для cashier
>
> Предназначен для воссоздания на новом сервере с нуля (aiohttp + PostgreSQL).

---

## 1. Общая концепция

Каждый вечер (~21:30–22:30) происходит закрытие дня в три шага:

```
Шаг 1: Админ Кафе              Шаг 2: Кассир Основного        Шаг 3: Владелец
  /cafe/shift-closing             /cashier/shift-closing          /shift-closing
         │                                │                              │
  Вводит зарплаты кафе           Вводит имена кассиров          Видит данные кассира
  → транзакции в Poster          → авторасчёт зарплат           Видит kaspi_cafe от кафе
         │                       → транзакции в Poster           Вводит Kaspi (единственное)
  Вводит данные смены            Вводит 5 значений смены                │
  → автопереводы в Poster        → данные у владельца            Расчёт ИТОГО ДЕНЬ
                                                                 → автопереводы в Poster
```

**Все три страницы разделяют:**
- Одну БД-таблицу `shift_closings` (разделённую по `poster_account_id`)
- Одинаковые формулы расчёта (с небольшими отличиями кафе)
- Одинаковую логику автопереводов (разные ID счетов)
- Общий Poster API для транзакций и данных продаж

---

## 2. Таблицы БД

### 2.1 `shift_closings` — Данные закрытия смены

```sql
CREATE TABLE shift_closings (
    id                  SERIAL PRIMARY KEY,
    telegram_user_id    BIGINT NOT NULL,
    date                DATE NOT NULL,
    poster_account_id   INTEGER,          -- NULL = основной, NOT NULL = кафе

    -- Введённые значения (тенге):
    wolt                REAL DEFAULT 0,
    halyk               REAL DEFAULT 0,
    kaspi               REAL DEFAULT 0,
    kaspi_cafe          REAL DEFAULT 0,   -- Минус от кафе (основной)
    kaspi_pizzburg      REAL DEFAULT 0,   -- Плюс от основного (кафе)
    cash_bills          REAL DEFAULT 0,
    cash_coins          REAL DEFAULT 0,
    shift_start         REAL DEFAULT 0,
    deposits            REAL DEFAULT 0,
    expenses            REAL DEFAULT 0,
    cash_to_leave       REAL DEFAULT 15000,

    -- Данные Poster (тенге, уже поделены на 100):
    poster_trade        REAL DEFAULT 0,
    poster_bonus        REAL DEFAULT 0,
    poster_card         REAL DEFAULT 0,
    poster_cash         REAL DEFAULT 0,
    transactions_count  INTEGER DEFAULT 0,

    -- Расчётные значения (тенге):
    fact_cashless       REAL DEFAULT 0,
    fact_total          REAL DEFAULT 0,
    fact_adjusted       REAL DEFAULT 0,
    poster_total        REAL DEFAULT 0,
    day_result          REAL DEFAULT 0,
    shift_left          REAL DEFAULT 0,
    collection          REAL DEFAULT 0,
    cashless_diff       REAL DEFAULT 0,

    -- Флаги:
    salaries_created    BOOLEAN DEFAULT FALSE,
    salaries_data       TEXT,              -- JSON: [{"role": "Кассир", "name": "Асель", "amount": 7000}]
    transfers_created   BOOLEAN DEFAULT FALSE,

    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP
);
```

**Уникальность (два partial unique index):**
```sql
-- Один основной на дату:
CREATE UNIQUE INDEX idx_shift_main ON shift_closings(telegram_user_id, date)
    WHERE poster_account_id IS NULL;
-- Один кафе на дату:
CREATE UNIQUE INDEX idx_shift_cafe ON shift_closings(telegram_user_id, date, poster_account_id)
    WHERE poster_account_id IS NOT NULL;
```

### 2.2 `cashier_shift_data` — Данные кассира

```sql
CREATE TABLE cashier_shift_data (
    id                    SERIAL PRIMARY KEY,
    telegram_user_id      BIGINT NOT NULL,
    date                  DATE NOT NULL,

    -- Шаг 1: Зарплаты
    cashier_count         INTEGER,            -- 2 или 3
    cashier_names         TEXT,               -- JSON: ["Асель", "Болат"]
    assistant_start_time  TEXT,               -- "10:00", "12:00", "14:00"
    doner_name            TEXT,
    assistant_name        TEXT,
    salaries_data         TEXT,               -- JSON: [{"role": "Кассир", "name": "Асель", "salary": 8000}]
    salaries_created      BOOLEAN DEFAULT FALSE,

    -- Шаг 2: 5 значений
    wolt                  REAL DEFAULT 0,
    halyk                 REAL DEFAULT 0,
    cash_bills            REAL DEFAULT 0,
    cash_coins            REAL DEFAULT 0,
    expenses              REAL DEFAULT 0,
    shift_data_submitted  BOOLEAN DEFAULT FALSE,

    created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at            TIMESTAMP,
    UNIQUE(telegram_user_id, date)
);
```

---

## 3. ID финансовых счетов (КРИТИЧНО)

### Основной отдел (Pizzburg)

```python
MAIN_ACCOUNTS = {
    'kaspi': 1,        # Каспи Пей
    'inkassacia': 2,   # Инкассация
    'cash_left': 4,    # Оставил в кассе
    'halyk': 10,       # Халык банк
    'wolt': 8,         # Wolt доставка
}

MAIN_SALARY_ACCOUNT = 4       # Зарплаты со счёта "Оставил в кассе"
MAIN_CASHIER_CATEGORY = 16    # Категория "Кассиры"
MAIN_DONER_CATEGORY = 19      # Категория "Донерщик"
```

### Кафе (Pizzburg-cafe)

```python
CAFE_ACCOUNTS = {
    'kaspi': 1,        # Каспи Пей
    'inkassacia': 2,   # Инкассация
    'cash_left': 5,    # Оставил в кассе (на закупы)
    'wolt': 7,         # Wolt доставка
}

CAFE_SALARY_ACCOUNT = 5       # Зарплаты со счёта "Оставил в кассе"
CAFE_CASHIER_CATEGORY = 16    # Кассир
CAFE_SUSHIST_CATEGORY = 17    # Сушист
CAFE_COOK_CATEGORY = 'auto'   # Повар Сандей → auto-detect через _find_category_id('повар', 'санд')
```

**Нет Halyk в кафе!**

---

## 4. Poster API — Получение данных продаж

### 4.1 `dash.getTransactions` — Заказы за день

```
GET /api/dash.getTransactions?token={token}&dateFrom=YYYYMMDD&dateTo=YYYYMMDD
```

**Response (массив заказов):**
```json
[
  {
    "transaction_id": "12345",
    "status": "2",             // 2 = закрытый заказ (ФИЛЬТРУЕМ ТОЛЬКО ЭТИ)
    "payed_cash": "50000",     // наличные, тийины
    "payed_card": "80000",     // картой, тийины
    "payed_bonus": "5000",     // бонусы, тийины
    "payed_sum": "135000"      // общая сумма, тийины
  }
]
```

**Расчёт из raw данных:**
```python
closed_orders = [tx for tx in transactions if tx['status'] == '2']

total_cash = sum(int(tx['payed_cash']) for tx in closed_orders)   # тийины
total_card = sum(int(tx['payed_card']) for tx in closed_orders)   # тийины
total_sum = sum(int(tx['payed_sum']) for tx in closed_orders)     # тийины

# Ключевые значения:
trade_total = total_cash + total_card           # Торговля (без бонусов)
bonus = total_sum - trade_total                  # Бонусы = payed_sum - (cash + card)
poster_card = total_card                         # Безнал Poster
poster_cash = total_cash                         # Наличные Poster
```

> **ВАЖНО:** `payed_sum` включает бонусы лояльности. Торговля = `payed_cash + payed_card` (без бонусов).

### 4.2 `finance.getCashShifts` — Остаток предыдущей смены

```
GET /api/finance.getCashShifts?token={token}&dateFrom=YYYYMMDD&dateTo=YYYYMMDD
```

**Response:**
```json
[
  {
    "cash_shift_id": "42",
    "amount_start": "1500000",    // тийины
    "amount_end": "2500000",      // тийины — ОСТАТОК ПРИ ЗАКРЫТИИ
    "date_start": "2026-02-26 10:00:00",
    "date_end": "2026-02-26 22:00:00"
  }
]
```

**Логика `shift_start`:**
```python
# Ищем кассовые смены за ВЧЕРА
yesterday = (today - timedelta(days=1)).strftime('%Y%m%d')
shifts = await client.get_cash_shifts(yesterday, yesterday)

if shifts:
    # Берём ПОСЛЕДНЮЮ смену (по date_end)
    last_shift = sorted(shifts, key=lambda s: s.get('date_end', ''))[-1]
    shift_start = int(last_shift['amount_end']) / 100  # тийины → тенге
else:
    shift_start = 0
```

### 4.3 `dash.getProductsSales` — Для зарплаты донерщика

```
GET /api/dash.getProductsSales?token={token}&dateFrom=YYYYMMDD&dateTo=YYYYMMDD
```

**Response:**
```json
[
  {
    "product_name": "Донер Классик",
    "category_id": "6",
    "count": "45"
  },
  {
    "product_name": "Комбо Донер",
    "category_id": "12",
    "count": "15"
  }
]
```

---

## 5. API Endpoints — Основной отдел (Owner)

### 5.1 `GET /api/shift-closing/poster-data` — Данные из Poster

**Query params:** `?date=YYYYMMDD` (default: сегодня по бизнес-дню)

**Бизнес-день:** До 6:00 Asia/Almaty → вчера.

**Логика:**
1. Получить **primary** аккаунт пользователя
2. Загрузить `dash.getTransactions` за дату → `trade_total`, `bonus`, `poster_card`, `poster_cash`
3. Загрузить `finance.getCashShifts` за предыдущий день → `shift_start` (amount_end последней смены)
4. Загрузить данные кассира из `cashier_shift_data` за эту дату
5. Загрузить `kaspi_cafe` из сохранённого закрытия кафе (`shift_closings WHERE poster_account_id IS NOT NULL`)

**Response (суммы в тийинах, кроме cashier_ и cafe_ полей — в тенге):**
```json
{
  "success": true,
  "date": "20260227",
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

> `trade_total`, `bonus`, `poster_card`, `poster_cash`, `poster_prev_shift_left` — в **тийинах** (фронтенд делит на 100).
> `cafe_kaspi_pizzburg`, `cashier_*` — уже в **тенге**.

---

### 5.2 `POST /api/shift-closing/calculate` — Расчёт формул

**Body (все суммы в тенге):**
```json
{
  "wolt": 30000,
  "halyk": 20000,
  "kaspi": 150000,
  "kaspi_cafe": 15000,
  "cash_bills": 45000,
  "cash_coins": 5000,
  "shift_start": 15000,
  "expenses": 8000,
  "deposits": 0,
  "cash_to_leave": 15000,
  "poster_trade": 1200000,
  "poster_bonus": 50000,
  "poster_card": 800000
}
```

> `poster_trade`, `poster_bonus`, `poster_card` — уже в **тенге** (фронтенд поделил при получении poster-data).

**Формулы основного отдела:**

```python
# 1. Безнал факт = Wolt + Halyk + (Kaspi - Kaspi от Cafe)
fact_cashless = wolt + halyk + (kaspi - kaspi_cafe)

# 2. Фактический = безнал + наличка
fact_total = fact_cashless + cash_bills + cash_coins

# 3. Итого фактический = Фактический - Смена - Внесения + Расходы
fact_adjusted = fact_total - shift_start - deposits + expenses

# 4. Итого Poster = Торговля - Бонусы
poster_total = poster_trade - poster_bonus

# 5. ИТОГО ДЕНЬ = Итого фактический - Итого Poster
day_result = fact_adjusted - poster_total
# day_result > 0 → излишек (зелёный)
# day_result < 0 → недостача (красный)
# day_result == 0 → всё сходится (синий)

# 6. Смена оставили = оставить бумажными + мелочь
shift_left = cash_to_leave + cash_coins

# 7. Разница безнала = факт безнал - Poster карта
cashless_diff = fact_cashless - poster_card

# 8. Инкассация = бумажные - оставить + расходы + разница безнала
collection = cash_bills - cash_to_leave + expenses + cashless_diff
```

**КРИТИЧНО — Знак `+cashless_diff` в инкассации (НЕ минус!):**

Инкассация — это **скорректированная** сумма для ввода в Poster POS. Она учитывает расхождение безнала:
- `cashless_diff > 0` (факт > Poster): Poster думает что наличных больше → инкассируем БОЛЬШЕ
- `cashless_diff < 0` (факт < Poster): Poster думает что наличных меньше → инкассируем МЕНЬШЕ

Вместе с переводом коррекции безнала → оба счёта (Безнал и Наличка) выравниваются.

**Response:**
```json
{
  "success": true,
  "calculations": {
    "fact_cashless": 185000,
    "fact_total": 235000,
    "fact_adjusted": 228000,
    "poster_total": 1150000,
    "day_result": -922000,
    "shift_left": 20000,
    "collection": 43000,
    "cashless_diff": -15000
  }
}
```

---

### 5.3 `POST /api/shift-closing/save` — Сохранить данные

**Body:** Все введённые + расчётные поля.

**Логика:** UPSERT в `shift_closings` по `(telegram_user_id, date) WHERE poster_account_id IS NULL`.

---

### 5.4 `POST /api/shift-closing/transfers` — Создать автопереводы

**Предусловие:** `transfers_created == FALSE` (защита от дублей).

**Переводы основного отдела:**

```python
MAIN_ACCOUNTS = {'kaspi': 1, 'inkassacia': 2, 'cash_left': 4, 'halyk': 10, 'wolt': 8}
transfer_date = f"{date} 22:00:00"
```

| # | Перевод | Откуда (ID) | Куда (ID) | Сумма | Условие |
|---|---------|-------------|-----------|-------|---------|
| 1 | Инкассация → Оставил | 2 (inkassacia) | 4 (cash_left) | `collection` | `collection > 0` |
| 2 | Каспий → Вольт | 1 (kaspi) | 8 (wolt) | `wolt` | `wolt > 0` |
| 3 | Каспий → Халык | 1 (kaspi) | 10 (halyk) | `halyk` | `halyk > 0` |
| 4a | Корр. безнала: Оставил → Каспий | 4 | 1 | `cashless_diff` | `cashless_diff > 0.5` |
| 4b | Корр. безнала: Каспий → Оставил | 1 | 4 | `abs(cashless_diff)` | `cashless_diff < -0.5` |

**Для каждого перевода:**
```python
await poster_client.create_transaction(
    transaction_type=2,       # перевод
    category_id=0,            # не нужна для переводов
    account_from_id=from_id,
    amount=int(amount),       # в тенге
    date=transfer_date,
    comment=comment,
    account_to_id=to_id
)
```

**Комментарии переводов:**
- Инкассация: `"Инкассация"`
- Wolt: `"Wolt"`
- Halyk: `"Halyk"`
- Коррекция: `"Корректировка безнала"`

**После всех переводов:** `transfers_created = TRUE` в `shift_closings`.

**Response:**
```json
{
  "success": true,
  "transfers": [
    {"type": "Инкассация → Оставил", "amount": 43000, "id": 56789},
    {"type": "Каспий → Wolt", "amount": 30000, "id": 56790},
    {"type": "Корректировка безнала", "amount": 15000, "id": 56791}
  ]
}
```

---

### 5.5 `GET /api/shift-closing/report` — Текстовый отчёт

**Формат:**
```
📊 Отчёт смены 27.02

💳 Безнал
  Wolt: 30 000₸
  Halyk: 20 000₸
  Kaspi: 150 000₸
  Kaspi Cafe: -15 000₸
  Итого безнал: 185 000₸

💵 Наличные
  Бумажные: 45 000₸
  Мелочь: 5 000₸

📋 Фактический: 235 000₸

  Смена: 15 000₸
  Расходы: 8 000₸
  Итого факт: 228 000₸

📊 Poster
  Торговля: 1 200 000₸
  Бонусы: -50 000₸
  Итого Poster: 1 150 000₸

🔢 ИТОГО ДЕНЬ: -922 000₸ ❌

💰 Инкассация: 43 000₸
  Оставили: 20 000₸
  Разница безнала: -15 000₸
```

---

### 5.6 `GET /api/shift-closing/history` — История

**Query params:** `?limit=30`

**Response:**
```json
{
  "history": [
    {
      "date": "2026-02-27",
      "day_result": -922000,
      "collection": 43000,
      "transactions_count": 145,
      "transfers_created": true
    }
  ]
}
```

### 5.7 `GET /api/shift-closing/dates` — Даты с данными

Для отображения доступных дат в навигаторе.

**Response:**
```json
{"dates": ["2026-02-27", "2026-02-26", "2026-02-25"]}
```

---

## 6. API Endpoints — Кафе (Admin)

### 6.1 `GET /api/cafe/poster-data` — Данные кафе из Poster

**Аналогично основному**, но для кафе-аккаунта. Дополнительно:
- `main_kaspi_cafe` — из сохранённого закрытия основного (сколько Kaspi от кафе)

**Response:** такой же формат как у основного, но `halyk = 0` всегда.

### 6.2 `POST /api/cafe/calculate` — Расчёт формул кафе

**Отличия формул кафе от основного:**

```python
# НЕТ Halyk. kaspi_pizzburg ПРИБАВЛЯЕТСЯ (доставки через основной Kaspi)
fact_cashless = wolt + kaspi + kaspi_pizzburg   # ← плюс вместо минуса!

fact_total = fact_cashless + cash_bills + cash_coins

# НЕТ deposits
fact_adjusted = fact_total - shift_start + expenses

# Остальные формулы ИДЕНТИЧНЫ основному
poster_total = poster_trade - poster_bonus
day_result = fact_adjusted - poster_total
shift_left = cash_to_leave + cash_coins
cashless_diff = fact_cashless - poster_card
collection = cash_bills - cash_to_leave + expenses + cashless_diff
```

### 6.3 `POST /api/cafe/salaries/create` — Зарплаты кафе

**Body:**
```json
{
  "salaries": [
    {"role": "Кассир", "name": "Асель", "amount": 7000},
    {"role": "Сушист", "name": "Ержан", "amount": 8000},
    {"role": "Повар Сандей", "name": "Айгуль", "amount": 6000}
  ]
}
```

**Логика:**
1. Найти кафе-аккаунт
2. Для каждой зарплаты:
   - Определить `category_id` по роли:
     - "Кассир" → 16
     - "Сушист" → 17
     - "Повар Сандей" → auto-detect: `_find_category_id(categories, 'повар', 'санд')`
   - Создать транзакцию:
     ```python
     await client.create_transaction(
         transaction_type=0,
         category_id=cat_id,
         account_from_id=5,      # CAFE: Оставил в кассе
         amount=int(salary.amount),
         date=f"{date} 22:00:00",
         comment=salary.name
     )
     ```
3. Сохранить `salaries_created=TRUE`, `salaries_data=json(salaries)` в `shift_closings`

**`_find_category_id(categories, *keywords):`**
```python
# Ищет категорию, в названии которой есть ВСЕ keywords (case-insensitive)
for cat in categories:
    name = cat['category_name'].lower()
    if all(kw.lower() in name for kw in keywords):
        return int(cat['category_id'])
return None
```

**Предусловие:** `salaries_created == FALSE`.

### 6.4 `POST /api/cafe/save` — Сохранить данные кафе

UPSERT в `shift_closings` по `(telegram_user_id, date, poster_account_id)`.

### 6.5 `POST /api/cafe/transfers` — Автопереводы кафе

```python
CAFE_ACCOUNTS = {'kaspi': 1, 'inkassacia': 2, 'cash_left': 5, 'wolt': 7}
```

| # | Перевод | Откуда | Куда | Сумма | Условие |
|---|---------|--------|------|-------|---------|
| 1 | Инкассация → Оставил | 2 | 5 | `collection` | `collection > 0` |
| 2 | Каспий → Вольт | 1 | 7 | `wolt` | `wolt > 0` |
| 3a | Корр: Оставил → Каспий | 5 | 1 | `cashless_diff` | `cashless_diff > 0.5` |
| 3b | Корр: Каспий → Оставил | 1 | 5 | `abs(cashless_diff)` | `cashless_diff < -0.5` |

**НЕТ Halyk-перевода в кафе!**

---

## 7. API Endpoints — Кассир (Cashier)

### 7.1 `POST /api/cashier/salaries/calculate` — Расчёт зарплат (без создания)

**Body:**
```json
{
  "cashier_count": 2,
  "assistant_start_time": "12:00"
}
```

**Логика:**
1. Загрузить продажи за сегодня: `dash.getTransactions` → `total_sales = payed_cash + payed_card` (в тийинах)
2. Рассчитать зарплату кассиров по таблице норм (раздел 8)
3. Загрузить продажи донеров: `dash.getProductsSales` → подсчёт
4. Рассчитать зарплату донерщика по таблице (раздел 9)
5. Рассчитать зарплату помощника по времени выхода (раздел 9)

**Response:**
```json
{
  "success": true,
  "cashier_salary": 8000,
  "doner_salary": 12750,
  "doner_base_salary": 12000,
  "doner_bonus": 750,
  "assistant_salary": 8000,
  "total_sales": 750000,
  "doner_count": 230,
  "transactions_count": 145
}
```

### 7.2 `POST /api/cashier/salaries/create` — Создать зарплаты в Poster

**Body:**
```json
{
  "cashier_count": 2,
  "cashier_names": ["Асель", "Болат"],
  "assistant_start_time": "12:00",
  "doner_name": "Серик",
  "assistant_name": "Нурлан"
}
```

**Логика:**
1. Рассчитать все зарплаты (как в 7.1)
2. Создать транзакции в Poster:

**Кассиры:**
```python
for name in cashier_names:
    await client.create_transaction(
        transaction_type=0,
        category_id=16,       # Кассиры
        account_from_id=4,    # Оставил в кассе
        amount=cashier_salary,
        date=f"{date} 21:30:00",
        comment=name
    )
```

**Донерщик:**
```python
await client.create_transaction(
    transaction_type=0,
    category_id=19,           # Донерщик
    account_from_id=4,
    amount=doner_salary,      # base + bonus
    date=f"{date} 21:30:00",
    comment=doner_name
)
```

**Помощник:**
```python
await client.create_transaction(
    transaction_type=0,
    category_id=19,           # Донерщик (та же категория!)
    account_from_id=4,
    amount=assistant_salary,
    date=f"{date} 21:30:00",
    comment=f"Помощник: {assistant_name}"
)
```

3. Сохранить в `cashier_shift_data`: `salaries_created=TRUE`, `salaries_data=json(salaries)`, имена, настройки
4. Также сохранить в `shift_closings` (основной): `salaries_created=TRUE`, `salaries_data=json(salaries)`

**Response:**
```json
{
  "success": true,
  "salaries": [
    {"role": "Кассир", "name": "Асель", "salary": 8000},
    {"role": "Кассир", "name": "Болат", "salary": 8000},
    {"role": "Донерщик", "name": "Серик", "salary": 12750},
    {"role": "Помощник", "name": "Нурлан", "salary": 8000}
  ],
  "total": 36750
}
```

### 7.3 `POST /api/cashier/shift-data/save` — Сохранить 5 значений

**Body:**
```json
{
  "wolt": 30000,
  "halyk": 20000,
  "cash_bills": 45000,
  "cash_coins": 5000,
  "expenses": 8000
}
```

**Логика:** Сохранить в `cashier_shift_data` с `shift_data_submitted=TRUE`. Эти значения автоматически подтягиваются на страницу владельца (endpoint 5.1).

### 7.4 `GET /api/cashier/shift-data` — Получить текущие данные

**Response:**
```json
{
  "success": true,
  "step": 2,
  "salaries_created": true,
  "salaries_data": [...],
  "shift_data_submitted": false,
  "wolt": 0,
  "halyk": 0,
  "cash_bills": 0,
  "cash_coins": 0,
  "expenses": 0
}
```

`step` определяется автоматически:
- 1 → зарплаты не созданы
- 2 → зарплаты созданы, 5 значений не введены
- 3 → всё готово

---

## 8. Зарплата кассиров — Таблица норм

Зависит от `total_sales` = `payed_cash + payed_card` (в **тийинах**, без бонусов).

### Для 2 кассиров

| Продажи (тенге) | Порог (тийины) | Зарплата каждого |
|-----------------|----------------|------------------|
| 0 – 599 999 | ≤ 59 999 900 | 6 000₸ |
| 600 000 – 699 999 | ≤ 69 999 900 | 7 000₸ |
| 700 000 – 799 999 | ≤ 79 999 900 | 8 000₸ |
| 800 000 – 899 999 | ≤ 89 999 900 | 9 000₸ |
| 900 000 – 999 999 | ≤ 99 999 900 | 10 000₸ |
| 1 000 000 – 1 099 999 | ≤ 109 999 900 | 11 000₸ |
| 1 100 000 – 1 199 999 | ≤ 119 999 900 | 12 000₸ |
| 1 200 000 – 1 299 999 | ≤ 129 999 900 | 13 000₸ |
| 1 300 000 – 1 399 999 | ≤ 139 999 900 | 14 000₸ |
| 1 400 000 – 1 499 999 | ≤ 149 999 900 | 15 000₸ |
| 1 500 000 – 1 599 999 | ≤ 159 999 900 | 16 000₸ |
| 1 600 000 – 1 699 999 | ≤ 169 999 900 | 17 000₸ |
| 1 700 000 – 1 799 999 | ≤ 179 999 900 | 18 000₸ |
| 1 800 000 – 1 899 999 | ≤ 189 999 900 | 19 000₸ |
| 1 900 000+ | > 189 999 900 | 20 000₸ |

### Для 3 кассиров

| Продажи (тенге) | Порог (тийины) | Зарплата каждого |
|-----------------|----------------|------------------|
| 0 – 799 999 | ≤ 79 999 900 | 6 000₸ |
| 800 000 – 899 999 | ≤ 89 999 900 | 7 000₸ |
| 900 000 – 999 999 | ≤ 99 999 900 | 8 000₸ |
| 1 000 000 – 1 099 999 | ≤ 109 999 900 | 9 000₸ |
| 1 100 000 – 1 199 999 | ≤ 119 999 900 | 10 000₸ |
| 1 200 000 – 1 299 999 | ≤ 129 999 900 | 11 000₸ |
| 1 300 000 – 1 399 999 | ≤ 139 999 900 | 12 000₸ |
| 1 400 000 – 1 499 999 | ≤ 149 999 900 | 13 000₸ |
| 1 500 000 – 1 599 999 | ≤ 159 999 900 | 14 000₸ |
| 1 600 000 – 1 699 999 | ≤ 169 999 900 | 15 000₸ |
| 1 700 000 – 1 799 999 | ≤ 179 999 900 | 16 000₸ |
| 1 800 000 – 1 899 999 | ≤ 189 999 900 | 17 000₸ |
| 1 900 000 – 1 999 999 | ≤ 199 999 900 | 18 000₸ |
| 2 000 000 – 2 099 999 | ≤ 209 999 900 | 19 000₸ |
| 2 100 000+ | > 209 999 900 | 20 000₸ |

**Алгоритм:**
```python
for max_sales, salary in sorted(norms.items()):
    if total_sales <= max_sales:
        return salary
# Если больше максимума → последняя норма (20 000₸)
```

---

## 9. Зарплата донерщика — Таблица норм

### Подсчёт донеров

Из `dash.getProductsSales`:

```python
for product in products_sales:
    name = product['product_name'].lower()
    category_id = product['category_id']
    count = float(product['count'])

    # Приоритет проверки:
    # 1. Донерная пицца (name == 'донерная' ИЛИ ('донер' in name AND 'пицц' in name))
    if name.strip() == 'донерная' or ('донер' in name and 'пицц' in name):
        pizza_count += count

    # 2. Комбо Донер ('комбо' in name AND 'донер' in name)
    elif 'комбо' in name and 'донер' in name:
        combo_count += count

    # 3. Категория Донер (category_id == '6')
    elif category_id == '6':
        category_count += count

total_count = category_count + combo_count + pizza_count
```

> Порядок важен! "Донерная пицца" проверяется ДО категории, иначе будет посчитана дважды.

### Таблица норм

| Донеров | Зарплата |
|---------|----------|
| 0 – 199 | 10 000₸ |
| 200 – 219 | 11 000₸ |
| 220 – 239 | 12 000₸ |
| 240 – 259 | 13 000₸ |
| 260 – 279 | 14 000₸ |
| 280 – 299 | 15 000₸ |
| 300 – 319 | 16 000₸ |
| 320 – 339 | 17 000₸ |
| 340 – 359 | 18 000₸ |
| 360 – 379 | 19 000₸ |
| 380+ | 20 000₸ |

### Помощник донерщика

| Время выхода | Зарплата помощника | Бонус донерщику |
|-------------|-------------------|----------------|
| 10:00 | 9 000₸ | 0₸ |
| 12:00 | 8 000₸ | +750₸ |
| 14:00 | 7 000₸ | +1 500₸ |

**Итого донерщик = базовая (по таблице) + бонус (за позднего помощника)**

---

## 10. Структура UI

### 10.1 Основной (`/shift-closing`) — для Owner

**Шапка:**
- Статус-бар: badges "Кассир" (ожидание/готов) и "Кафе" (ожидание/готов)
- Навигатор дат: горизонтальный скролл кнопок (сегодня + 7 дней назад)
- Текущая дата, количество заказов

**Блоки (сверху вниз):**

1. **Безнал** — карточка:
   - Wolt (input), Halyk (input) — два столбца
   - Kaspi (input)
   - Kaspi Cafe (input, со знаком минус, красный акцент)

2. **Наличные** — карточка:
   - Бумажные (input), Мелочь (input)

3. **Подитог "Фактический: X₸"**

4. **Корректировки** — карточка:
   - Смена (начало) (input, автозаполнение из Poster), Расходы с кассы (input)

5. **Подитог "Итого факт.: X₸"** (синий)

6. **Poster** — карточка (readonly):
   - Безнал факт / Безнал Poster / Разница безнала (цвет: зелёный если 0, красный если ≠0)
   - Торговля / Бонусы / Итого Poster

7. **ИТОГО ДЕНЬ** — крупная цифра:
   - Зелёный = излишек, Красный = недостача

8. **Инкассация** — карточка:
   - Input "Оставить на смену" (default 15000)
   - "Смена оставили" (readonly)
   - Итоговая сумма инкассации (крупный шрифт)

9. **Кнопки:** "Обновить Poster", "Отчёт смены"

10. **Блок отчёта:** Текст + "Скопировать" (создаёт переводы)

**Автозаполнение:** Данные кассира (wolt, halyk, cash_bills, cash_coins, expenses) и kaspi_cafe (от кафе) подставляются автоматически из API.

**Debounce 300ms** на всех расчётах.

### 10.2 Кассир (`/cashier/shift-closing`) — 4 шага

**Для cashier:** видит только текущий шаг.
**Для owner:** видит все шаги одновременно (readonly, кроме кнопок).

**Шаг 1: Данные для зарплат**
- Toggle "2 кассира / 3 кассира"
- Inputs: имена кассиров (2 или 3 поля)
- Toggle: время помощника (10:00 / 12:00 / 14:00)
- Inputs: имя донерщика, имя помощника
- Кнопка "Далее"

**Шаг 2: Подтверждение зарплат**
- Таблица: роль | имя | сумма
- Итого
- Кнопки: "Назад" / "Подтвердить"

**Шаг 3: 5 значений смены**
- Wolt (input), Halyk (input)
- Бумажные (input), Мелочь (input)
- Расходы с кассы (input)
- Кнопка "Отправить"

**Шаг 4: Готово** — сообщение успеха

### 10.3 Кафе (`/cafe/shift-closing`) — 2 шага

**Для admin:** видит текущий шаг.
**Для owner:** видит оба шага readonly.

**Шаг 1: Зарплаты**
- 3 карточки ролей:
  - Кассир: имя (input) + сумма (input)
  - Сушист: имя (input) + сумма (input)
  - Повар Сандей: имя (input) + сумма (input)
- Итого
- Кнопка "Далее"

**Шаг 2: Закрытие смены**
- Аналогично основному, но:
  - Нет поля Halyk
  - Вместо "Kaspi Cafe" (минус) → "Kaspi Pizzburg" (плюс, зелёный акцент)
  - Нет поля Deposits
- Кнопки: "Обновить Poster", "Отчёт смены", "Скопировать"

---

## 11. Общий Kaspi-терминал

**Физически Kaspi-терминал один** — в основном отделе. Но через него проходят оплаты обоих заведений.

При закрытии **основного**: владелец вводит `kaspi` (общая сумма из банковского приложения) и `kaspi_cafe` (сколько из этой суммы — от кафе). Формула: `fact_cashless = wolt + halyk + (kaspi - kaspi_cafe)`.

При закрытии **кафе**: админ вводит `kaspi` (сумму кафе) и `kaspi_pizzburg` (сколько прошло через основной терминал и относится к кафе). Формула: `fact_cashless = wolt + kaspi + kaspi_pizzburg`.

**Автозаполнение:**
- На странице основного: `kaspi_cafe` подтягивается из сохранённого закрытия кафе (`shift_closings WHERE poster_account_id IS NOT NULL`)
- На странице кафе: `kaspi_pizzburg` подтягивается из сохранённого закрытия основного

---

## 12. Защита от дублей

| Что | Флаг | Таблица |
|-----|------|---------|
| Зарплаты основного | `salaries_created` | `shift_closings` (poster_account_id IS NULL) |
| Зарплаты кафе | `salaries_created` | `shift_closings` (poster_account_id IS NOT NULL) |
| Зарплаты кассира | `salaries_created` | `cashier_shift_data` |
| Переводы основного | `transfers_created` | `shift_closings` (poster_account_id IS NULL) |
| Переводы кафе | `transfers_created` | `shift_closings` (poster_account_id IS NOT NULL) |

Перед созданием транзакций проверяем флаг → если `TRUE` → ошибка "Уже создано".

---

## 13. DB-операции

```
# shift_closings
save_shift_closing(telegram_user_id, date, poster_account_id, **data) → id (UPSERT)
get_shift_closing(telegram_user_id, date, poster_account_id=None) → dict
get_shift_closings_history(telegram_user_id, limit=30) → list
get_shift_closing_dates(telegram_user_id) → list[str]
update_shift_closing(id, **kwargs) → bool

# cashier_shift_data
save_cashier_shift_data(telegram_user_id, date, **data) → id (UPSERT)
get_cashier_shift_data(telegram_user_id, date) → dict
update_cashier_shift_data(id, **kwargs) → bool
```

---

## 14. Порядок реализации (рекомендация)

1. **БД:** `shift_closings` + `cashier_shift_data` с partial unique indexes
2. **Poster Client:** `dash.getTransactions`, `dash.getProductsSales`, `finance.getCashShifts`, `create_transaction(type=2)` для переводов
3. **Расчёт зарплат:** Таблицы норм кассиров (2/3) + донерщик + помощник
4. **Poster Data API:** `/api/shift-closing/poster-data` и `/api/cafe/poster-data`
5. **Calculate API:** Формулы основного и кафе (отличаются!)
6. **Save API:** UPSERT в shift_closings
7. **Salaries:** Кассир и кафе — создание транзакций в Poster
8. **Transfers:** Автопереводы (основной 5 штук, кафе 3 штуки)
9. **Report:** Генерация текста
10. **Cashier flow:** 4-шаговый wizard с сохранением промежуточного состояния
11. **UI:** 3 страницы с role-based видимостью
