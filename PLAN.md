# PLAN.md — Закрытие дня: Wizard на сайте

> Этот файл описывает план реализации единого flow "Закрытие дня" на сайте.
> Подробное описание текущего бизнес-флоу — см. **CLAUDE.md** → "Бизнес-флоу: Закрытие дня"

## Цель

Перенести весь процесс закрытия дня на сайт, минимизировать ручные действия. Три ключевых участника:
1. **Админ Кафе** — закрывает Кафе через изолированную страницу (`/cafe/{token}/shift-closing`, уже реализовано)
2. **Кассир основного отдела** — вводит зарплаты и 5 значений через изолированную страницу (`/cashier/{token}/shift-closing`, **НОВОЕ**)
3. **Владелец** — получает данные от обоих, закрывает день через Mini App + автоматические переводы

---

## ТЕКУЩАЯ ЗАДАЧА: Страница Кассира + Авто-переводы

### Обзор

Создаём изолированную страницу для Кассира основного отдела по паттерну Кафе (`shift_closing_cafe.html`). Кассир делает две вещи:
1. Вводит данные для расчёта зарплат → подтверждает → транзакции создаются в Poster
2. Вводит 5 значений для закрытия смены → данные появляются у владельца

После закрытия смены обоих отделов — авто-переводы одной кнопкой.

---

### Шаг 1: Страница Кассира — Зарплаты

**URL:** `/cashier/{token}/shift-closing`
**Технология:** Plain HTML + vanilla JS (как `shift_closing_cafe.html`)

**Экран 1 — Ввод данных для зарплат:**

```
┌─────────────────────────────────┐
│   Основной отдел — Закрытие     │
│                                 │
│  ┌── Кассиры ──────────────┐    │
│  │  [2 кассира] [3 кассира]│    │
│  │                         │    │
│  │  Кассир 1: [Меруерт   ]│    │ ← автоподстановка вчерашних
│  │  Кассир 2: [Батима     ]│    │
│  │  (Кассир 3: [        ] )│    │ ← появляется при выборе 3
│  └─────────────────────────┘    │
│                                 │
│  ┌── Помощник донерщика ───┐    │
│  │  Вышел на смену:        │    │
│  │  [10:00] [12:00] [14:00]│    │
│  └─────────────────────────┘    │
│                                 │
│  ┌── Донерщик и помощник ──┐    │
│  │  Донерщик:  [Бека      ]│    │ ← автоподстановка вчерашних
│  │  Помощник:  [Сержан    ]│    │
│  └─────────────────────────┘    │
│                                 │
│         [ Далее → ]             │
└─────────────────────────────────┘
```

**Экран 2 — Подтверждение зарплат (после нажатия "Далее"):**

Бэкенд рассчитывает зарплаты через `cashier_salary.py` и `doner_salary.py`:

```
┌─────────────────────────────────┐
│   Зарплаты за 17.02.2026       │
│                                 │
│  Кассир Меруерт      12 000 ₸  │
│  Кассир Батима        12 000 ₸  │
│  Помощник Сержан       9 000 ₸  │
│  Донерщик Бека        11 000 ₸  │
│  ───────────────────────────    │
│  Итого:               44 000 ₸  │
│                                 │
│  [ ← Назад ]  [ Подтвердить ]  │
└─────────────────────────────────┘
```

**При нажатии "Подтвердить":**
- API вызывает `CashierSalaryCalculator.create_salary_transactions()` и `DonerSalaryCalculator.create_salary_transaction()`
- Создаются 4-5 транзакций в Poster (категория "Кассиры" ID=16, "Донерщик" ID=19, счёт "Оставил в кассе" ID=4)
- Показывается зелёная галочка "Транзакции созданы"
- Автоматический переход к Шагу 2

---

### Шаг 2: Страница Кассира — 5 значений для закрытия

**Экран 3 (после подтверждения зарплат):**

```
┌─────────────────────────────────┐
│   Данные для закрытия смены     │
│                                 │
│  ┌── Безнал ───────────────┐    │
│  │  Wolt:    [          0 ]│    │
│  │  Halyk:   [          0 ]│    │
│  └─────────────────────────┘    │
│                                 │
│  ┌── Наличные ─────────────┐    │
│  │  Бумажные: [         0 ]│    │
│  │  Мелочь:   [         0 ]│    │
│  └─────────────────────────┘    │
│                                 │
│  ┌── Расходы ──────────────┐    │
│  │  Расходы с кассы: [   0 ]│   │
│  └─────────────────────────┘    │
│                                 │
│         [ Отправить ]           │
└─────────────────────────────────┘
```

**При нажатии "Отправить":**
- Данные сохраняются в таблицу `cashier_shift_data`
- Показывается зелёная галочка "Данные отправлены"
- У владельца на странице закрытия смены эти 5 полей автоматически подтягиваются

---

### Шаг 3: Интеграция с владельцем

**На странице владельца (ShiftClosing.tsx / Mini App):**

При загрузке poster-data endpoint проверяет `cashier_shift_data` за сегодня:
- Если данные есть → автозаполняет: `wolt`, `halyk`, `cash_bills`, `cash_coins`, `expenses`
- Показывает индикатор: "Данные от кассира" (зелёный badge)
- `kaspi_cafe` уже подтягивается из закрытия Кафе (существующая логика через `cafe_kaspi_pizzburg`)

**Итого владельцу остаётся ввести только:**
- `kaspi` (Kaspi основного отдела — из банковского приложения)

Все остальные данные уже на месте.

---

### Шаг 4: Авто-переводы после закрытия смены

**Переводы Кафе** (после закрытия Кафе админом):

| Перевод | Со счёта | На счёт | Сумма | Источник |
|---------|----------|---------|-------|----------|
| Инкассация Кафе | Инкассация | Оставил в кассе (Кафе) | `cafe_collection` | Рассчитано в закрытии Кафе |
| Wolt Кафе | Каспий Пей | Вольт (Кафе) | `cafe_wolt` | Введено админом Кафе на сайте |

**Переводы Основного** (после закрытия владельцем):

| Перевод | Со счёта | На счёт | Сумма | Источник |
|---------|----------|---------|-------|----------|
| Инкассация | Инкассация | Оставил в кассе (Основной) | `collection` | Рассчитано на странице владельца |
| Wolt | Каспий Пей | Вольт (Основной) | `wolt` | Введено кассиром (Шаг 2) |
| Halyk | Каспий Пей | Halyk (Основной) | `halyk` | Введено кассиром (Шаг 2) |

**Реализация:**
- Кнопка "Создать переводы" на странице закрытия смены (у владельца и у админа Кафе)
- Список переводов с суммами, каждый с чекбоксом (вкл/выкл) и редактируемой суммой
- При подтверждении → `finance.createTransaction` с `type=2` (transfer) для каждого

> **ВАЖНО:** Нужно уточнить ID счетов "Инкассация" и "Вольт" в Poster. Получить через `finance.getAccounts`.

---

## Техническая реализация

### 1. База данных

#### Таблица `cashier_access_tokens` (новая)
```sql
CREATE TABLE cashier_access_tokens (
    id INTEGER PRIMARY KEY,
    token TEXT UNIQUE NOT NULL,
    telegram_user_id BIGINT NOT NULL,
    poster_account_id INTEGER NOT NULL,  -- ID основного аккаунта (is_primary=true)
    label TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id)
);
```
Паттерн полностью повторяет `cafe_access_tokens`, но `poster_account_id` указывает на **основной** аккаунт (Pizzburg), а не на Кафе.

#### Таблица `cashier_shift_data` (новая)
```sql
CREATE TABLE cashier_shift_data (
    id INTEGER PRIMARY KEY,
    telegram_user_id BIGINT NOT NULL,
    date TEXT NOT NULL,                    -- YYYY-MM-DD
    -- Шаг 1: Зарплаты
    cashier_count INTEGER,                 -- 2 или 3
    cashier_names TEXT,                    -- JSON array: ["Меруерт", "Батима"]
    assistant_start_time TEXT,             -- "10:00" | "12:00" | "14:00"
    doner_name TEXT,
    assistant_name TEXT,
    salaries_data TEXT,                    -- JSON: рассчитанные зарплаты
    salaries_created BOOLEAN DEFAULT FALSE,-- транзакции созданы?
    -- Шаг 2: 5 значений
    wolt REAL DEFAULT 0,
    halyk REAL DEFAULT 0,
    cash_bills REAL DEFAULT 0,
    cash_coins REAL DEFAULT 0,
    expenses REAL DEFAULT 0,
    shift_data_submitted BOOLEAN DEFAULT FALSE,  -- данные отправлены?
    -- Мета
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(telegram_user_id, date)
);
```

### 2. Методы database.py (новые)

```python
# Токены
create_cashier_token(telegram_user_id, poster_account_id, label) -> str
get_cashier_token(token) -> Optional[Dict]  # возвращает user_id + poster credentials
list_cashier_tokens(telegram_user_id) -> list
delete_cashier_token(token_id, telegram_user_id) -> bool

# Данные кассира
save_cashier_shift_data(telegram_user_id, date, data) -> bool  # upsert
get_cashier_shift_data(telegram_user_id, date) -> Optional[Dict]
get_cashier_last_employees(telegram_user_id) -> Optional[Dict]  # вчерашние имена
```

### 3. Команда бота `/cashier_token`

Полностью копирует паттерн `/cafe_token`:
```
/cashier_token list              — список токенов
/cashier_token create [метка]    — создать токен (привязка к PRIMARY аккаунту)
/cashier_token delete <id>       — удалить токен
```

Разница с `/cafe_token`:
- Ищет **primary** аккаунт вместо non-primary
- URL: `/cashier/{token}/shift-closing`

### 4. Flask routes (web_app.py)

#### Страница
```
GET /cashier/<token>/shift-closing
    → render_template('shift_closing_cashier.html', token=token, account_name=...)
```

#### API для зарплат
```
POST /api/cashier/<token>/salaries/calculate
    Body: { cashier_count: 2|3, assistant_start_time: "10:00"|"12:00"|"14:00" }
    → Вызывает CashierSalaryCalculator.get_total_sales() + calculate_salary()
    → Вызывает DonerSalaryCalculator.get_doner_sales_count() + calculate_salary()
    → Возвращает: { cashier_salary: 12000, doner_salary: 11000, assistant_salary: 9000 }
    (НЕ создаёт транзакций, только рассчитывает)

POST /api/cashier/<token>/salaries/create
    Body: {
        cashier_count: 2,
        cashier_names: ["Меруерт", "Батима"],
        assistant_start_time: "12:00",
        doner_name: "Бека",
        assistant_name: "Сержан"
    }
    → Вызывает create_salary_transactions() и create_salary_transaction()
    → Создаёт транзакции в Poster
    → Сохраняет в cashier_shift_data (salaries_created = true)
    → Возвращает: { success: true, salaries: [...] }
```

#### API для данных смены
```
POST /api/cashier/<token>/shift-data/save
    Body: { wolt: 45000, halyk: 120000, cash_bills: 890000, cash_coins: 15000, expenses: 35000 }
    → Сохраняет в cashier_shift_data (shift_data_submitted = true)
    → Возвращает: { success: true }

GET /api/cashier/<token>/employees/last
    → Возвращает вчерашние имена из cashier_shift_data или employees таблицы
    → { cashier_names: ["Меруерт", "Батима"], doner_name: "Бека", assistant_name: "Сержан",
        cashier_count: 2, assistant_start_time: "12:00" }
```

### 5. HTML-шаблон `shift_closing_cashier.html`

**Структура (по аналогии с `shift_closing_cafe.html`):**

```html
<!-- Встроенный JS -->
<script>
const TOKEN = '{{ token }}';
const API_BASE = '/api/cashier/' + TOKEN;

let currentStep = 1;  // 1=зарплаты, 2=подтверждение, 3=данные смены, 4=готово

// Шаг 1: Ввод данных для зарплат
// - Кнопки 2/3 кассира
// - Поля имён с автоподстановкой
// - Кнопки 10:00/12:00/14:00
// - Кнопка "Далее" → POST /salaries/calculate → показ Шага 2

// Шаг 2: Подтверждение зарплат
// - Таблица: имя → сумма
// - Кнопка "Подтвердить" → POST /salaries/create → переход к Шагу 3
// - Кнопка "Назад" → возврат к Шагу 1

// Шаг 3: 5 полей ввода
// - Wolt, Halyk, Бумажные, Мелочь, Расходы
// - Кнопка "Отправить" → POST /shift-data/save → Шаг 4

// Шаг 4: Готово
// - "Данные отправлены. Спасибо!"
</script>
```

**UI стили:** Полностью копируем из `shift_closing_cafe.html` (iOS-style, mobile-optimized).

### 6. Интеграция с владельцем

**В `web_app.py`, endpoint `GET /api/shift-closing/poster-data`:**

```python
# Существующая логика (уже есть):
# - cafe_kaspi_pizzburg из shift_closings Кафе

# Новая логика:
# Проверяем cashier_shift_data за сегодня
cashier_data = db.get_cashier_shift_data(user_id, date_str)
if cashier_data and cashier_data.get('shift_data_submitted'):
    data['cashier_wolt'] = cashier_data.get('wolt', 0)
    data['cashier_halyk'] = cashier_data.get('halyk', 0)
    data['cashier_cash_bills'] = cashier_data.get('cash_bills', 0)
    data['cashier_cash_coins'] = cashier_data.get('cash_coins', 0)
    data['cashier_expenses'] = cashier_data.get('expenses', 0)
    data['cashier_data_submitted'] = True
```

**В `ShiftClosing.tsx` (Mini App):**

```typescript
// При загрузке posterData, если cashier_data_submitted:
if (posterData.cashier_data_submitted) {
    setInputs(prev => ({
        ...prev,
        wolt: String(posterData.cashier_wolt || ''),
        halyk: String(posterData.cashier_halyk || ''),
        cashBills: String(posterData.cashier_cash_bills || ''),
        cashCoins: String(posterData.cashier_cash_coins || ''),
        expenses: String(posterData.cashier_expenses || ''),
    }))
    // Показать badge "Данные от кассира"
}
```

### 7. Авто-переводы (Phase 2, после страницы кассира)

#### Endpoint для переводов
```
POST /api/shift-closing/transfers
    Body: {
        transfers: [
            { account_from_id: X, account_to_id: Y, amount: Z, comment: "Инкассация" },
            { account_from_id: 1, account_to_id: W, amount: Z, comment: "Kaspi → Wolt" },
            ...
        ],
        poster_account_id: null  // null=основной, число=Кафе
    }
    → Для каждого перевода: poster_client.create_transaction(type=2, ...)
    → Возвращает: { success: true, created: [...] }
```

#### UI на странице владельца (после расчёта дня)
```
┌── Переводы ────────────────────────┐
│ ☑ Инкассация → Оставил    250 000 ₸│
│ ☑ Каспий → Вольт           45 000 ₸│
│ ☑ Каспий → Халык          120 000 ₸│
│                                     │
│ Будет создано 3 перевода            │
│                                     │
│      [ Создать переводы ]           │
└─────────────────────────────────────┘
```

#### UI на странице Кафе (после расчёта дня)
```
┌── Переводы ────────────────────────┐
│ ☑ Инкассация → Оставил    180 000 ₸│
│ ☑ Каспий → Вольт           30 000 ₸│
│                                     │
│ Будет создано 2 перевода            │
│                                     │
│      [ Создать переводы ]           │
└─────────────────────────────────────┘
```

---

## Порядок реализации

### Фаза 1A: Страница Кассира (ТЕКУЩАЯ)
1. Таблицы `cashier_access_tokens` + `cashier_shift_data` в database.py
2. CRUD-методы для токенов и данных кассира в database.py
3. Команда `/cashier_token` в bot.py
4. Flask routes в web_app.py (6 endpoints)
5. HTML-шаблон `shift_closing_cashier.html`
6. Интеграция: данные кассира → poster-data endpoint → ShiftClosing.tsx

### Фаза 1B: Авто-переводы
1. Endpoint `POST /api/shift-closing/transfers` + `POST /api/cafe/<token>/transfers`
2. Блок переводов на странице владельца (ShiftClosing.tsx)
3. Блок переводов на странице Кафе (shift_closing_cafe.html)

### Фаза 2: Дополнительные улучшения
- Помощник по разнице безнала
- Авто-сверка балансов
- Надбавка основного кассира за доставку (когда появится API)

---

## Открытые вопросы

1. **ID счетов:** Какие ID у "Инкассация" и "Вольт" в Poster? → получить через `finance.getAccounts`
2. **Доставка:** Как определить заказы на доставку в Poster API для надбавки кассира?
3. **Зарплаты Кафе:** Есть ли таблица норм для сушиста/кассира Кафе?

---

## Предыдущие фазы (архив)

<details>
<summary>Фаза 3-5: Кафе wizard, помощник безнала, сверка (будущее)</summary>

### Фаза 3: Кафе как шаг wizard'а владельца
- `CafeClosingStep.tsx` — форма закрытия Кафе в Mini App
- Связь Кафе → основной (автоподстановка kaspi_cafe)
- Зарплаты Кафе в том же шаге

### Фаза 4: Помощник по безналу
- Endpoint для получения чеков за день
- `ReceiptHelper.tsx` — список чеков с подсветкой
- Быстрый поиск чека для коррекции метода оплаты

### Фаза 5: Авто-сверка
- Endpoint для получения балансов счетов
- `ReconciliationStep.tsx` — сравнение факт vs Poster
- Мгновенная проверка, не забыта ли транзакция

</details>
