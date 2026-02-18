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

**Триггер:** Нажатие кнопки "Скопировать отчёт" (уже существующая кнопка на страницах закрытия смены Кафе и Основного отдела).

**Логика:**
1. Пользователь нажимает "Скопировать отчёт"
2. Отчёт копируется в буфер (как сейчас)
3. **Плюс** автоматически создаются переводы в Poster
4. Кнопка меняет текст: "Скопировать и создать переводы" → "Скопировать отчёт (переводы созданы)"

**Защита от дублей:**
- В таблице `shift_closings` новая колонка: `transfers_created BOOLEAN DEFAULT FALSE`
- При создании переводов → `transfers_created = true`
- При повторном нажатии → проверяем флаг: если `true` → только копируем отчёт, переводы НЕ создаём
- В отчёте добавляется строка "Переводы созданы" или "Переводы: уже созданы ранее"

**Переводы Кафе** (при нажатии "Скопировать" на странице Кафе):

| Перевод | Со счёта | На счёт | Сумма | Источник |
|---------|----------|---------|-------|----------|
| Инкассация Кафе | Инкассация | Оставил в кассе (Кафе) | `cafe_collection` | Рассчитано в закрытии Кафе |
| Wolt Кафе | Каспий Пей | Вольт (Кафе) | `cafe_wolt` | Введено админом Кафе на сайте |

**Переводы Основного** (при нажатии "Скопировать" на странице владельца):

| Перевод | Со счёта | На счёт | Сумма | Источник |
|---------|----------|---------|-------|----------|
| Инкассация | Инкассация | Оставил в кассе (Основной) | `collection` | Рассчитано на странице владельца |
| Wolt | Каспий Пей | Вольт (Основной) | `wolt` | Введено кассиром (Шаг 2) |
| Halyk | Каспий Пей | Halyk (Основной) | `halyk` | Введено кассиром (Шаг 2) |

**Реализация:**
- При нажатии "Скопировать отчёт" → `POST /api/.../transfers` (если `transfers_created == false`)
- Каждый перевод → `finance.createTransaction` с `type=2` (transfer)
- Ответ API содержит ID созданных транзакций → сохраняем в `shift_closings`
- UI показывает краткое уведомление: "Отчёт скопирован, 3 перевода созданы"

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

### 7. Авто-переводы (привязаны к кнопке "Скопировать отчёт")

#### Endpoint для переводов
```
POST /api/shift-closing/transfers
    → Читает данные из shift_closings за дату
    → Проверяет transfers_created: если true → возвращает { already_created: true }
    → Генерирует список переводов на основе сохранённых данных (collection, wolt, halyk)
    → Для каждого перевода: poster_client.create_transaction(type=2, ...)
    → Ставит transfers_created = true
    → Возвращает: { success: true, created_count: 3 }

POST /api/cafe/<token>/transfers
    → Аналогично, но для Кафе (2 перевода: инкассация + wolt)
```

#### Миграция БД (shift_closings)
```sql
ALTER TABLE shift_closings ADD COLUMN transfers_created BOOLEAN DEFAULT FALSE;
```

#### Поведение кнопки "Скопировать отчёт"
```
Первое нажатие (transfers_created = false):
  1. Копирует отчёт в буфер
  2. POST /transfers → создаёт переводы в Poster
  3. Уведомление: "Отчёт скопирован, 3 перевода созданы"
  4. Текст кнопки: "Скопировать отчёт (переводы созданы ✓)"

Повторное нажатие (transfers_created = true):
  1. Копирует отчёт в буфер
  2. НЕ создаёт переводы
  3. Уведомление: "Отчёт скопирован"
```

---

## Порядок реализации

### Фаза 1A: Страница Кассира — ГОТОВО ✅
1. ~~Таблицы `cashier_access_tokens` + `cashier_shift_data` в database.py~~
2. ~~CRUD-методы для токенов и данных кассира в database.py~~
3. ~~Команда `/cashier_token` в bot.py~~
4. ~~Flask routes в web_app.py (6 endpoints)~~
5. ~~HTML-шаблон `shift_closing_cashier.html`~~
6. ~~Интеграция: данные кассира → poster-data endpoint → ShiftClosing.tsx~~

### Фаза 1.5: Система аутентификации с ролями — ГОТОВО ✅

<details>
<summary>Детали реализации (свернуто)</summary>

**Реализовано:**
- Таблица `web_users` с bcrypt хешированием (`database.py:980-1009`)
- CRUD-методы: create/verify/list/delete/reset_password (`database.py:3499-3699`)
- Страница `/login` с Flask session (`web_app.py:78-109`, `templates/login.html`)
- Middleware `check_auth()` с ролями owner/admin/cashier (`web_app.py:119-163`)
- URL без токенов: `/cafe/shift-closing`, `/cashier/shift-closing` (старые URL с токенами → редирект)
- Команда бота `/staff` (list/create/delete/reset) (`bot.py:1390-1515`)
- `bcrypt==4.2.0` в requirements.txt
- ⚠️ Rate limit на `/login` — не реализован (некритично, можно добавить позже через Flask-Limiter)

</details>

### Фаза 1B: Авто-переводы — ГОТОВО ✅

<details>
<summary>Детали реализации (свернуто)</summary>

**Реализовано:**
- `POST /api/shift-closing/transfers` — переводы основного отдела (инкассация, Wolt, Halyk)
- `POST /api/cafe/transfers` — переводы Кафе (инкассация, Wolt)
- Защита от дублей: `transfers_created` флаг в `shift_closings`
- Защита от нулевых сумм: `amount > 0` проверка
- UI на страницах владельца и Кафе

</details>

### Дополнительные улучшения (вне фаз) — ГОТОВО ✅

<details>
<summary>Детали (свернуто)</summary>

- **Fix UNIQUE constraint** — cafe + main shift closings на одну дату (partial unique indexes)
- **Автозаполнение данных кассира** на странице владельца (5 полей из `cashier_shift_data`)
- **Автозаполнение `kaspi_cafe`** из закрытия Кафе
- **Status badges** (Кассир/Кафе) на странице владельца
- **Navbar links** (Кассир/Кафе) в `base.html`
- **Фоновая синхронизация расходов** (BackgroundScheduler, каждые 5 мин, независимо от открытых страниц)

</details>

---

### Фаза 2: Корректировка безнала — ГОТОВО ✅

<details>
<summary>Детали реализации (свернуто)</summary>

> **Проблема:** При закрытии смены возникает разница безнала (факт vs Poster). Например, Poster думает что безнала 100 000₸, а по факту 95 000₸. Это значит что по счёту Каспий в Poster на 5 000₸ больше, а по Наличным — на 5 000₸ меньше. Раньше приходилось вручную искать чек и менять метод оплаты.
>
> **Решение:** Автоматический корректировочный перевод между счетами Каспий и Наличные при закрытии смены. Эффект тот же что при редактировании чека — счета выравниваются.

**Логика:**
| Разница безнала | Poster vs факт | Перевод |
|---|---|---|
| `-4 693₸` | Poster безнала больше | Каспий Пей → Оставил в кассе (4 693₸) |
| `+3 000₸` | Poster безнала меньше | Оставил в кассе → Каспий Пей (3 000₸) |

**Реализация:**
- Перевод добавлен в оба endpoint: `/api/shift-closing/transfers` (основной) и `/api/cafe/transfers` (кафе)
- Создаётся вместе с остальными переводами (инкассация, Wolt, Halyk)
- Защита от дублей: тот же `transfers_created` флаг
- Порог: `|cashless_diff| > 0.5₸` (игнорируем копеечные разницы)

**Счета:**
- Основной: Каспий(1) ↔ Оставил в кассе(4)
- Кафе: Каспий(1) ↔ Оставил в кассе(5)

</details>

---

## Открытые вопросы

1. **Доставка:** Как определить заказы на доставку в Poster API для надбавки кассира?
2. **Зарплаты Кафе:** Есть ли таблица норм для сушиста/кассира Кафе?

---

## Предыдущие фазы (архив)

<details>
<summary>Будущие идеи</summary>

### Кафе как шаг wizard'а владельца
- `CafeClosingStep.tsx` — форма закрытия Кафе в Mini App
- Связь Кафе → основной (автоподстановка kaspi_cafe)
- Зарплаты Кафе в том же шаге

### Надбавка кассира за доставку
- 100₸ × количество заказов на доставку
- Зависит от возможности определить доставки через Poster API

</details>
