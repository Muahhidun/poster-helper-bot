# Страница «Кассир» — Полная спецификация бэкенда

## Обзор

Страница `/cashier/shift-closing` — 4-шаговый визард для кассира основного отдела (Pizzburg). Кассир вводит имена сотрудников, подтверждает зарплаты (авторасчёт по таблицам норм), затем вводит 5 числовых значений (Wolt, Halyk, бумажные, мелочь, расходы), которые автоматически подтягиваются на страницу закрытия смены владельца.

**Роли:**
- `cashier` — проходит визард пошагово, вводит данные
- `owner` — видит все шаги одновременно в readonly-режиме, наблюдает за прогрессом кассира

---

## 1. Аутентификация и доступ

### 1.1 Роли и маршрутизация

```python
# web_app.py
def get_home_for_role(role):
    if role == 'cashier':
        return '/cashier/shift-closing'  # домашняя страница кассира
    # ...

def check_role_access(path, role):
    if role == 'owner':
        return True  # owner видит всё
    if role == 'cashier':
        if path.startswith('/cashier/') or path.startswith('/api/cashier/'):
            return True
        if path == '/logout':
            return True
        return False
    return False
```

### 1.2 Резолв информации о кассире

Каждый API endpoint вызывает `resolve_cashier_info()` для получения контекста:

```python
def resolve_cashier_info():
    db = get_database()
    role = session.get('role')

    if role == 'cashier':
        # Кассир: берём poster_account_id из сессии (привязан при создании пользователя)
        poster_account_id = session.get('poster_account_id')
        if poster_account_id:
            info = db.get_web_user_poster_info(session['web_user_id'])
            if info:
                return info  # {telegram_user_id, poster_account_id, account_name, poster_token, ...}

    elif role == 'owner':
        # Owner: берём primary аккаунт (Pizzburg)
        telegram_user_id = session.get('telegram_user_id')
        accounts = db.get_accounts(telegram_user_id)
        primary_account = next((a for a in accounts if a.get('is_primary')), None)
        if primary_account:
            return {
                'telegram_user_id': telegram_user_id,
                'poster_account_id': primary_account['id'],
                'account_name': primary_account.get('account_name', 'Основной отдел'),
                'poster_token': primary_account.get('poster_token'),
                'poster_user_id': primary_account.get('poster_user_id'),
                'poster_base_url': primary_account.get('poster_base_url'),
            }

    abort(403)
```

**Возвращаемые поля:**

| Поле | Тип | Описание |
|------|-----|----------|
| `telegram_user_id` | int | ID владельца в Telegram |
| `poster_account_id` | int | ID аккаунта Poster в нашей БД |
| `account_name` | str | Название заведения |
| `poster_token` | str | API токен Poster |
| `poster_user_id` | str | User ID в Poster |
| `poster_base_url` | str | Base URL для API |

### 1.3 Сессия (Flask session)

При логине в session записываются:
```python
session['web_user_id'] = user['id']
session['role'] = user['role']          # 'cashier', 'admin', 'owner'
session['telegram_user_id'] = user['telegram_user_id']
session['poster_account_id'] = user.get('poster_account_id')
session['label'] = user.get('label', username)
```

---

## 2. База данных

### 2.1 Таблица `cashier_shift_data`

Хранит все данные кассирской смены за день (зарплаты + 5 значений).

**PostgreSQL:**
```sql
CREATE TABLE IF NOT EXISTS cashier_shift_data (
    id SERIAL PRIMARY KEY,
    telegram_user_id BIGINT NOT NULL,
    date DATE NOT NULL,

    -- Шаг 1: данные о сотрудниках
    cashier_count INTEGER,                -- 2 или 3
    cashier_names TEXT,                    -- JSON: ["Имя1", "Имя2"] или ["Имя1", "Имя2", "Имя3"]
    assistant_start_time TEXT,             -- "10:00", "12:00" или "14:00"
    doner_name TEXT,                       -- имя донерщика
    assistant_name TEXT,                   -- имя помощника

    -- Шаг 2: результат расчёта зарплат
    salaries_data TEXT,                    -- JSON: [{name, role, salary}, ...]
    salaries_created BOOLEAN DEFAULT FALSE,-- флаг: транзакции созданы в Poster

    -- Шаг 3: 5 значений от кассира
    wolt REAL DEFAULT 0,                   -- сумма Wolt терминала (тенге)
    halyk REAL DEFAULT 0,                  -- сумма Halyk терминала (тенге)
    cash_bills REAL DEFAULT 0,             -- наличные бумажные (тенге)
    cash_coins REAL DEFAULT 0,             -- наличные мелочь (тенге)
    expenses REAL DEFAULT 0,               -- расходы с кассы (тенге)
    shift_data_submitted BOOLEAN DEFAULT FALSE, -- флаг: 5 значений отправлены

    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(telegram_user_id, date)  -- один рекорд на пользователя в день
);
```

**SQLite:** аналогично, но `BOOLEAN` → `INTEGER DEFAULT 0`, `BIGINT` → `INTEGER`, `SERIAL` → `AUTOINCREMENT`.

### 2.2 Методы БД

#### `save_cashier_shift_data(telegram_user_id, date, data)` → bool

UPSERT — создаёт или обновляет запись. Ключевые особенности:
- Boolean поля (`salaries_created`, `shift_data_submitted`) конвертируются: PostgreSQL → `bool()`, SQLite → `int(1/0)`
- Текстовые поля (`cashier_names`, `salaries_data` и др.) могут быть `None`
- Числовые поля default `0`
- `ON CONFLICT(telegram_user_id, date) DO UPDATE` — обновляет все поля

```python
fields = [
    'cashier_count', 'cashier_names', 'assistant_start_time',
    'doner_name', 'assistant_name', 'salaries_data', 'salaries_created',
    'wolt', 'halyk', 'cash_bills', 'cash_coins', 'expenses',
    'shift_data_submitted'
]
# Upsert по (telegram_user_id, date)
```

#### `get_cashier_shift_data(telegram_user_id, date)` → dict | None

Возвращает все колонки для конкретной даты. `SELECT *` → `dict(zip(columns, row))`.

#### `get_cashier_last_employees(telegram_user_id)` → dict | None

Возвращает данные о сотрудниках из последней заполненной смены (для авто-заполнения имён):
```sql
SELECT cashier_count, cashier_names, assistant_start_time,
       doner_name, assistant_name
FROM cashier_shift_data
WHERE telegram_user_id = %s
  AND cashier_names IS NOT NULL
ORDER BY date DESC
LIMIT 1
```

---

## 3. Определение даты

Бизнес-день: если сейчас до 6:00 утра — считается вчерашним днём.

```python
from datetime import datetime, timedelta
import pytz

KZ_TZ = pytz.timezone('Asia/Almaty')
kz_now = datetime.now(KZ_TZ)

if kz_now.hour >= 6:
    date_str = kz_now.strftime('%Y-%m-%d')
else:
    date_str = (kz_now - timedelta(days=1)).strftime('%Y-%m-%d')
```

Этот паттерн используется в **каждом** endpoint.

---

## 4. API Endpoints

### 4.1 Страница

**`GET /cashier/shift-closing`**

Отрисовывает HTML-шаблон. Передаёт в шаблон:
- `account_name` — название заведения (из `resolve_cashier_info()`)
- `user_role` — роль из сессии ('cashier' или 'owner')

```python
@app.route('/cashier/shift-closing')
def cashier_shift_closing():
    info = resolve_cashier_info()
    role = session.get('role', 'cashier')
    return render_template('shift_closing_cashier.html',
                           account_name=info.get('account_name', 'Основной отдел'),
                           user_role=role)
```

Legacy redirect: `GET /cashier/<token>/shift-closing` → redirect на `/cashier/shift-closing`

---

### 4.2 `GET /api/cashier/employees/last`

Авто-заполнение имён из последней смены.

**Response (данные найдены):**
```json
{
    "success": true,
    "cashier_count": 2,
    "cashier_names": "[\"Айгерим\", \"Данияр\"]",
    "assistant_start_time": "10:00",
    "doner_name": "Ерлан",
    "assistant_name": "Аслан"
}
```

**Response (нет данных):**
```json
{
    "success": true
}
```

**ВАЖНО:** `cashier_names` — строка с JSON-массивом. На фронте нужен `JSON.parse()`.

---

### 4.3 `POST /api/cashier/salaries/calculate`

Расчёт зарплат **без создания транзакций** (предпросмотр).

**Request:**
```json
{
    "cashier_count": 2,
    "assistant_start_time": "10:00"
}
```

**Логика:**
1. Получает `telegram_user_id` через `resolve_cashier_info()`
2. Создаёт `CashierSalaryCalculator` → вызывает `get_total_sales()` → API `dash.getTransactions`
3. По сумме продаж и количеству кассиров находит зарплату по таблице норм
4. Создаёт `DonerSalaryCalculator` → вызывает `get_doner_sales_count()` → API `dash.getProductsSales`
5. По количеству донеров находит зарплату по таблице
6. Рассчитывает бонус донерщика и зарплату помощника по времени выхода

**Response:**
```json
{
    "success": true,
    "cashier_salary": 8000,
    "doner_salary": 12750,
    "assistant_salary": 8000
}
```

**Poster API вызовы (внутри):**

1. `GET dash.getTransactions?dateFrom=YYYYMMDD&dateTo=YYYYMMDD` — для кассиров
2. `GET dash.getProductsSales?dateFrom=YYYYMMDD&dateTo=YYYYMMDD` — для донерщика

---

### 4.4 `POST /api/cashier/salaries/create`

Создание транзакций зарплат в Poster.

**Request:**
```json
{
    "cashier_count": 2,
    "cashier_names": ["Айгерим", "Данияр"],
    "assistant_start_time": "12:00",
    "doner_name": "Ерлан",
    "assistant_name": "Аслан"
}
```

**Логика:**
1. **Защита от дублей:** проверяет `salaries_created` в `cashier_shift_data` за сегодня
2. Если уже создано → возвращает ошибку 400
3. Создаёт транзакции кассиров через `CashierSalaryCalculator.create_salary_transactions()`
4. Создаёт транзакции донерщика и помощника через `DonerSalaryCalculator.create_salary_transaction()`
5. Сохраняет результат в `cashier_shift_data` с `salaries_created=True`

**Транзакции создаваемые в Poster (основной отдел):**

| Роль | Категория | Category ID | Счёт | Account ID | Тип | Комментарий |
|------|-----------|-------------|------|------------|-----|-------------|
| Кассир 1 | Кассиры | 16 | Оставил в кассе | 4 | expense (0) | Имя кассира |
| Кассир 2 | Кассиры | 16 | Оставил в кассе | 4 | expense (0) | Имя кассира |
| Кассир 3* | Кассиры | 16 | Оставил в кассе | 4 | expense (0) | Имя кассира |
| Донерщик | Донерщик | 19 | Оставил в кассе | 4 | expense (0) | Имя донерщика |
| Помощник | Донерщик | 19 | Оставил в кассе | 4 | expense (0) | "Помощник: {имя}" |

*Кассир 3 создаётся только если `cashier_count=3`

**Poster API вызов для каждой транзакции:**
```
POST finance.createTransaction
  type = 0              # расход
  category = 16 / 19    # Кассиры / Донерщик
  account_from = 4      # Оставил в кассе
  amount = <salary>     # В ТЕНГЕ (не тийинах!)
  date = "YYYY-MM-DD HH:MM:SS"  # 21:30 текущего дня
  comment = <имя>
```

**Response (успех):**
```json
{
    "success": true,
    "salaries": [
        {"name": "Айгерим", "role": "Кассир", "salary": 8000},
        {"name": "Данияр", "role": "Кассир", "salary": 8000},
        {"name": "Ерлан", "role": "Донерщик", "salary": 12750},
        {"name": "Аслан", "role": "Помощник", "salary": 8000}
    ],
    "total": 36750
}
```

**Response (дубль):**
```json
{
    "success": false,
    "error": "Зарплаты за сегодня уже созданы. Повторное создание заблокировано."
}
```

**Сохранение в БД после успеха:**
```python
db.save_cashier_shift_data(telegram_user_id, date_str, {
    'cashier_count': cashier_count,
    'cashier_names': json.dumps(cashier_names, ensure_ascii=False),
    'assistant_start_time': assistant_start_time,
    'doner_name': doner_name,
    'assistant_name': assistant_name,
    'salaries_data': json.dumps(salaries, ensure_ascii=False),  # [{name, role, salary}, ...]
    'salaries_created': True,
})
```

---

### 4.5 `POST /api/cashier/shift-data/save`

Сохранение 5 значений от кассира (шаг 3).

**Request:**
```json
{
    "wolt": 45000,
    "halyk": 32000,
    "cash_bills": 150000,
    "cash_coins": 5000,
    "expenses": 12000
}
```

Все значения в **тенге**.

**Логика:**
1. Определяет бизнес-дату (до 6:00 = вчера)
2. Загружает existing данные за сегодня (чтобы **сохранить** поля зарплат)
3. Копирует поля зарплат из existing: `cashier_count`, `cashier_names`, `assistant_start_time`, `doner_name`, `assistant_name`, `salaries_data`, `salaries_created`
4. Добавляет 5 числовых значений + `shift_data_submitted=True`
5. Upsert в `cashier_shift_data`

**ВАЖНО:** Этот endpoint **сохраняет** ранее записанные зарплатные поля при обновлении. Без этого upsert затёр бы зарплатные данные нулями.

**Response:**
```json
{
    "success": true,
    "date": "2026-02-28"
}
```

---

### 4.6 `GET /api/cashier/shift-data/status`

Проверка статуса смены за сегодня. Поведение зависит от роли.

**Response (для кассира):**
```json
{
    "success": true,
    "date": "2026-02-28",
    "salaries_created": true,
    "shift_data_submitted": false
}
```

**Response (для owner — расширенный):**
```json
{
    "success": true,
    "date": "2026-02-28",
    "salaries_created": true,
    "shift_data_submitted": true,
    "salaries_data": "[{\"name\":\"Айгерим\",\"role\":\"Кассир\",\"salary\":8000}, ...]",
    "shift_data": {
        "wolt": 45000,
        "halyk": 32000,
        "cash_bills": 150000,
        "cash_coins": 5000,
        "expenses": 12000
    }
}
```

**Логика для owner:** если `shift_data_submitted=true`, возвращает также `shift_data` с 5 значениями. Если `salaries_created=true`, возвращает `salaries_data` (JSON-строка).

Эти данные используются на странице закрытия смены владельца (`/shift-closing`) для автоподстановки полей.

---

## 5. Расчёт зарплат

### 5.1 Зарплата кассиров (`cashier_salary.py`)

Зарплата зависит от **общей суммы продаж за день** и **количества кассиров** (2 или 3).

**Получение суммы продаж:**
```python
# Poster API: dash.getTransactions
# Фильтр: только закрытые заказы (status == '2')
# Формула: total_sales = payed_cash + payed_card (БЕЗ payed_bonus)
```

**ВАЖНО:** `payed_sum` включает бонусы лояльности. Для зарплаты используется `payed_cash + payed_card` — реальные деньги.

Все суммы из API — в **тийинах** (×100).

#### Таблица норм для 2 кассиров

Пороги в тийинах, зарплата в тенге:

| Продажи (тийины) | Продажи (тенге) | Зарплата каждого |
|-------------------|------------------|-----------------|
| 0 — 59 999 900 | 0 — 599 999 ₸ | 6 000 ₸ |
| — 69 999 900 | — 699 999 ₸ | 7 000 ₸ |
| — 79 999 900 | — 799 999 ₸ | 8 000 ₸ |
| — 89 999 900 | — 899 999 ₸ | 9 000 ₸ |
| — 99 999 900 | — 999 999 ₸ | 10 000 ₸ |
| — 109 999 900 | — 1 099 999 ₸ | 11 000 ₸ |
| — 119 999 900 | — 1 199 999 ₸ | 12 000 ₸ |
| — 129 999 900 | — 1 299 999 ₸ | 13 000 ₸ |
| — 139 999 900 | — 1 399 999 ₸ | 14 000 ₸ |
| — 149 999 900 | — 1 499 999 ₸ | 15 000 ₸ |
| — 159 999 900 | — 1 599 999 ₸ | 16 000 ₸ |
| — 169 999 900 | — 1 699 999 ₸ | 17 000 ₸ |
| — 179 999 900 | — 1 799 999 ₸ | 18 000 ₸ |
| — 189 999 900 | — 1 899 999 ₸ | 19 000 ₸ |
| — 199 999 900 | — 1 999 999 ₸ | 20 000 ₸ |
| > 199 999 900 | > 2 000 000 ₸ | 20 000 ₸ (макс) |

#### Таблица норм для 3 кассиров

| Продажи (тийины) | Продажи (тенге) | Зарплата каждого |
|-------------------|------------------|-----------------|
| 0 — 79 999 900 | 0 — 799 999 ₸ | 6 000 ₸ |
| — 89 999 900 | — 899 999 ₸ | 7 000 ₸ |
| — 99 999 900 | — 999 999 ₸ | 8 000 ₸ |
| — 109 999 900 | — 1 099 999 ₸ | 9 000 ₸ |
| — 119 999 900 | — 1 199 999 ₸ | 10 000 ₸ |
| — 129 999 900 | — 1 299 999 ₸ | 11 000 ₸ |
| — 139 999 900 | — 1 399 999 ₸ | 12 000 ₸ |
| — 149 999 900 | — 1 499 999 ₸ | 13 000 ₸ |
| — 159 999 900 | — 1 599 999 ₸ | 14 000 ₸ |
| — 169 999 900 | — 1 699 999 ₸ | 15 000 ₸ |
| — 179 999 900 | — 1 799 999 ₸ | 16 000 ₸ |
| — 189 999 900 | — 1 899 999 ₸ | 17 000 ₸ |
| — 199 999 900 | — 1 999 999 ₸ | 18 000 ₸ |
| — 209 999 900 | — 2 099 999 ₸ | 19 000 ₸ |
| — 219 999 900 | — 2 199 999 ₸ | 20 000 ₸ |
| > 219 999 900 | > 2 200 000 ₸ | 20 000 ₸ (макс) |

**Алгоритм:** перебор по порогам `sorted(norms.items())`, первое совпадение `total_sales <= max_sales` → возврат зарплаты. Если превышает все пороги → максимальная зарплата.

### 5.2 Зарплата донерщика (`doner_salary.py`)

Зарплата зависит от **количества проданных донеров**.

**Получение количества:**
```python
# Poster API: dash.getProductsSales
# Подсчёт по 3 источникам (ПРИОРИТЕТ: сначала спецтовары, потом категория)
```

#### Подсчёт донеров — правила приоритета:

1. **Донерная пицца** (проверяется ПЕРВОЙ):
   - Название точно = `"донерная"` (case-insensitive, strip)
   - ИЛИ содержит `"донер"` И `"пицц"` одновременно

2. **Комбо Донер** (проверяется ВТОРОЙ):
   - Содержит `"комбо"` И `"донер"` одновременно

3. **Категория «Донер»** (проверяется ПОСЛЕДНЕЙ):
   - `category_id == "6"` (строка!)
   - Только товары, НЕ попавшие в п.1 и п.2

**Итого:** `total_count = category_count + combo_count + pizza_count`

#### Таблица норм донерщика

| Количество | Зарплата |
|------------|----------|
| 0 — 199 | 10 000 ₸ |
| 200 — 219 | 11 000 ₸ |
| 220 — 239 | 12 000 ₸ |
| 240 — 259 | 13 000 ₸ |
| 260 — 279 | 14 000 ₸ |
| 280 — 299 | 15 000 ₸ |
| 300 — 319 | 16 000 ₸ |
| 320 — 339 | 17 000 ₸ |
| 340 — 359 | 18 000 ₸ |
| 360 — 379 | 19 000 ₸ |
| 380 — 399 | 20 000 ₸ |
| > 399 | 20 000 ₸ (макс) |

### 5.3 Бонус донерщика и зарплата помощника

Зависит от **времени выхода помощника** (`assistant_start_time`):

| Время выхода | Бонус донерщику | Зарплата помощника |
|-------------|-----------------|-------------------|
| 10:00 | 0 ₸ | 9 000 ₸ |
| 12:00 | +750 ₸ | 8 000 ₸ |
| 14:00 | +1 500 ₸ | 7 000 ₸ |

**Итоговая зарплата донерщика** = базовая (по таблице) + бонус

**Логика:** чем позже выходит помощник, тем больше бонус донерщику (работает дольше один) и меньше зарплата помощника (работает меньше часов).

---

## 6. Poster API

### 6.1 `dash.getTransactions` (для кассиров)

```
GET https://{account}.joinposter.com/api/dash.getTransactions
  ?token={token}
  &dateFrom=20260228
  &dateTo=20260228
```

**Response:**
```json
{
    "response": [
        {
            "status": "2",           // "2" = закрытый заказ (ФИЛЬТР!)
            "payed_cash": "15000",   // наличные, тийины (строка!)
            "payed_card": "25000",   // картой, тийины
            "payed_bonus": "500",    // бонусы, тийины (НЕ УЧИТЫВАЕМ)
            "payed_sum": "40500"     // total, тийины
        }
    ]
}
```

**Обработка:**
- Фильтр: `status == '2'` (только закрытые)
- Все значения — **строки**, нужно `int()`
- `total_sales = sum(payed_cash) + sum(payed_card)` — без бонусов

### 6.2 `dash.getProductsSales` (для донерщика)

```
GET https://{account}.joinposter.com/api/dash.getProductsSales
  ?token={token}
  &dateFrom=20260228
  &dateTo=20260228
```

**Response:**
```json
{
    "response": [
        {
            "product_name": "Донер классический",
            "category_id": "6",      // строка!
            "count": "15"            // строка, может быть float
        },
        {
            "product_name": "Комбо Донер",
            "category_id": "3",
            "count": "8"
        }
    ]
}
```

### 6.3 `finance.createTransaction` (создание зарплаты)

```
POST https://{account}.joinposter.com/api/finance.createTransaction
  ?token={token}

Form data:
  type = 0                          # расход
  category = 16                     # Кассиры (или 19 для Донерщик)
  account_from = 4                  # Оставил в кассе
  amount = 8000                     # В ТЕНГЕ (не тийинах!)
  date = "2026-02-28 21:30:00"
  comment = "Айгерим"               # имя сотрудника
```

**ВАЖНО:** `finance.createTransaction` принимает суммы в **тенге**, а `dash.getTransactions` возвращает в **тийинах**.

---

## 7. UI Структура (4 шага)

### Шаг 1: Ввод данных о сотрудниках

**Карточка "Кассиры":**
- Toggle: `2 кассира` / `3 кассира` (по умолчанию 2)
- Input: Кассир 1 (на кассе) — имя
- Input: Кассир 2 — имя
- Input: Кассир 3 — имя (скрыт при 2 кассирах)

**Карточка "Помощник донерщика":**
- Toggle: `10:00` / `12:00` / `14:00` (время выхода, по умолчанию 10:00)

**Карточка "Донерщик и помощник":**
- Input: Донерщик — имя
- Input: Помощник — имя

**Кнопка:** "Далее" → вызывает `/api/cashier/salaries/calculate`

**Валидация:** все имена обязательны (если хоть одно пустое → ошибка "Заполните все имена")

**Авто-заполнение:** при загрузке вызывается `/api/cashier/employees/last` и заполняются все поля из последней смены.

### Шаг 2: Подтверждение зарплат

**Список зарплат:** каждая строка — роль, имя, сумма:
- Кассир — Айгерим — 8 000 ₸
- Кассир — Данияр — 8 000 ₸
- Помощник — Аслан — 8 000 ₸
- Донерщик — Ерлан — 12 750 ₸

**Итого:** сумма всех зарплат

**Кнопки:** "Назад" (→ шаг 1) и "Подтвердить" (→ создание транзакций → шаг 3)

### Шаг 3: Ввод 5 значений смены

**Карточка "Безнал":**
- Input: Wolt (number)
- Input: Halyk (number)

**Карточка "Наличные":**
- Input: Бумажные (number)
- Input: Мелочь (number) — в ряд с бумажными

**Карточка "Расходы":**
- Input: Расходы с кассы (number)

**Кнопка:** "Отправить" → сохраняет и переходит к шагу 4

### Шаг 4: Готово

Заглушка с галочкой: "Данные отправлены. Спасибо! Данные появятся на странице закрытия смены."

### Step Indicator

4 точки сверху (dots): серая = ожидание, синяя = текущий, зелёная = завершён.

### Banner

Единый блок уведомлений сверху страницы. Типы: `info` (синий), `success` (зелёный), `error` (красный), `warning` (оранжевый).

---

## 8. Поведение Owner

Когда страницу открывает owner:
1. **Все 4 шага видны одновременно** (не пошагово)
2. **Step indicator скрыт**
3. **Все input readonly** (нельзя редактировать)
4. **Все кнопки действий скрыты** (Далее, Подтвердить, Отправить)
5. **Шаг 4 (Готово) скрыт** полностью

**Данные подтягиваются через status endpoint:**
- Если `salaries_created=true` и `salaries_data` есть → отображает зарплаты в шаге 2
- Если `salaries_created=false` → шаг 2 скрыт, banner "Зарплаты ещё не созданы"
- Если `shift_data_submitted=true` и `shift_data` есть → заполняет 5 полей шага 3
- Если `shift_data_submitted=false` → заголовок шага 3 получает "(не заполнено)"

Owner использует эту страницу для мониторинга — данные от кассира автоматически подтягиваются на его основную страницу закрытия смены (`/shift-closing`).

---

## 9. Навигация

Навигация отображается **только для owner** (условие в шаблоне `{% if user_role == 'owner' %}`):

Ссылки: Расходы → Поставки → Смена → **Кассир** (active) → Кафе

Кассир видит только заголовок заведения и визард, без навигации.

---

## 10. Poster Account IDs (основной отдел)

| ID | Название | Назначение |
|----|----------|-----------|
| 4 | Оставил в кассе | Списание зарплат |
| 16 | Кассиры (категория) | Категория для транзакций зарплат кассиров |
| 19 | Донерщик (категория) | Категория для транзакций зарплат донерщика и помощника |

---

## 11. Связь с основной страницей закрытия смены

Данные, введённые кассиром на этой странице (5 значений: wolt, halyk, cash_bills, cash_coins, expenses), используются на странице владельца `/shift-closing`:

1. Страница владельца вызывает `GET /api/cashier/shift-data/status`
2. Если `shift_data_submitted=true`, получает `shift_data` объект
3. Автоматически заполняет поля: Wolt, Halyk, Бумажные, Мелочь, Расходы
4. Владельцу остаётся ввести только **Kaspi** (из банковского приложения)

Это ключевая связь — кассир заполняет данные, которые потом подтягиваются к расчёту закрытия смены.

---

## 12. Защита от дублей

- **Зарплаты:** Перед созданием проверяется `salaries_created` в БД. Если `True`/`1` → ошибка 400
- **5 значений:** Нет защиты от перезаписи — кассир может отправить повторно (upsert обновит)
- **Шаг визарда:** При загрузке страницы `checkStatus()` проверяет и перекидывает на нужный шаг:
  - `shift_data_submitted=true` → шаг 4 (всё готово)
  - `salaries_created=true` → шаг 3 (вводи 5 значений)
  - Иначе → шаг 1

---

## 13. Порядок реализации (рекомендация)

1. **Таблица `cashier_shift_data`** — миграция
2. **DB методы** — `save_cashier_shift_data`, `get_cashier_shift_data`, `get_cashier_last_employees`
3. **`resolve_cashier_info()`** — резолв контекста из сессии
4. **`GET /api/cashier/shift-data/status`** — статус (нужен и кассиру и owner)
5. **`GET /api/cashier/employees/last`** — автозаполнение имён
6. **`POST /api/cashier/salaries/calculate`** — расчёт (Poster API: `dash.getTransactions`, `dash.getProductsSales`)
7. **`POST /api/cashier/salaries/create`** — создание транзакций (Poster API: `finance.createTransaction`)
8. **`POST /api/cashier/shift-data/save`** — сохранение 5 значений
9. **`GET /cashier/shift-closing`** — страница
10. **Frontend** — 4-шаговый визард с owner-режимом
