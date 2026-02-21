# OPTIMIZATION.md — План оптимизации Poster Helper Bot

> Создан: 2026-02-21
> Статус: В работе
> Прогресс: отмечается галочками [x] по мере выполнения

---

## ЭТАП 1: Безопасность (критично)

### 1.1 ~~Убрать захардкоженный TELEGRAM_USER_ID~~ ГОТОВО

~~**Проблема:** `TELEGRAM_USER_ID = 167084307` используется 92 раза в web_app.py.~~

**Что сделано:**
- [x] Удалена константа `TELEGRAM_USER_ID = 167084307`
- [x] Все 92 вхождения заменены на `g.user_id` (из сессии авторизованного пользователя)
- [x] `check_auth()` теперь ставит `g.user_id` для ВСЕХ авторизованных запросов
- [x] `validate_api_request()` — убраны fallback-и на захардкоженный ID
- [x] `get_user_id_from_init_data()` — убран fallback, использует сессию
- [x] Фоновый sync — получает user_id из БД через `get_all_user_ids_with_accounts()`
- [x] Добавлен метод `get_all_user_ids_with_accounts()` в database.py

**Файлы изменены:** `web_app.py`, `database.py`

### 1.2 ~~Убрать обход аутентификации (legacy mode)~~ ГОТОВО

~~**Проблема:** Если в таблице `web_users` нет записей, аутентификация полностью пропускалась.~~

**Что сделано:**
- [x] Убран весь блок legacy mode (проверка `list_web_users` + `return None`)
- [x] Теперь без сессии — всегда redirect на `/login` (для web) или 401 (для API)

**Файлы изменены:** `web_app.py`

### 1.3 Добавить проверку владельца при удалении/обновлении

**Проблема:** 7 из 11 delete-эндпоинтов не проверяют чей это черновик. Любой авторизованный пользователь может удалить чужие данные по ID.

**Уязвимые эндпоинты:**
- [ ] `/expenses/delete/<id>` (строка 1486) — `delete_expense_draft(draft_id)` без user_id
- [ ] `/expenses/delete` bulk (строка 1494) — `delete_expense_drafts_bulk(draft_ids)` без user_id
- [ ] `/api/expenses/<id>` DELETE (строка 2104) — без user_id
- [ ] `/api/supply-drafts/<id>` DELETE (строка 2756) — без user_id
- [ ] `/api/supply-drafts/items/<id>` DELETE (строка 2819) — без user_id
- [ ] `/supplies/delete-item/<id>` (строка 3469) — без user_id
- [ ] `/supplies/delete/<id>` (строка 3700) — без user_id

**Что сделать:**
- [ ] Добавить `telegram_user_id` параметр в методы `delete_expense_draft()`, `delete_expense_drafts_bulk()`, `delete_supply_draft()`, `delete_supply_draft_item()` в database.py
- [ ] Добавить `AND telegram_user_id = ?` в SQL-запросы удаления
- [ ] Передавать user_id из сессии в каждом эндпоинте

**Файлы:** `web_app.py`, `database.py`

### 1.4 Убрать отправку пароля открытым текстом

**Проблема:** Команда `/staff add` (bot.py:1472) отправляет пароль текстом в Telegram-чат. Остаётся в истории навсегда.

**Что сделать:**
- [ ] Вместо показа пароля — предложить пользователю задать свой пароль при первом входе
- [ ] Или: добавить предупреждение "Удалите это сообщение после прочтения" + auto-delete через 60 секунд

**Файлы:** `bot.py` (строки 1464-1474)

---

## ~~ЭТАП 2: Удаление мёртвого кода~~ ГОТОВО

### 2.1-2.5 ~~Удаление файлов и хендлеров~~ ГОТОВО

**Удалённые файлы (8 шт):**
- [x] `invoice_processor.py` (384 строки)
- [x] `invoice_ocr_gpt4_only.py` (197 строк)
- [x] `advanced_supply_parser.py` (325 строк)
- [x] `invoice_ocr.py` (302 строки)
- [x] `receipt_handler.py` (223 строки)
- [x] `receipt_ocr.py` (170 строк)
- [x] `stt_service.py` (54 строки)

**Удалённые хендлеры в bot.py:**
- [x] `handle_voice` — голосовой ввод (Whisper)
- [x] `handle_document` — Excel/Kaspi
- [x] `handle_supply_photo` — OCR фото накладных
- [x] `handle_delete_order_callback` — удаление заказа по чеку
- [x] Callback обработчики `delete_order:`, `cancel_order_delete`, `delete_receipt_mode`, `cancel_receipt_delete`
- [x] Кнопка "Удалить чек" из меню
- [x] Регистрация voice и document handler-ов
- [x] `handle_photo` упрощён — только expense_input фото

**Удалённые скрипты (19 шт):**
- [x] `migrate_aliases_to_db.py`, `import_aliases_to_railway.py`, `add_pizzburg_cafe.py`
- [x] `fix_poster_urls.py`, `get_pizzburg_cafe_ids.py`, `check_aliases.py`
- [x] `check_suppliers.py`, `show_all_products.py`, `fetch_types.py`
- [x] `get_sales_direct.py`, `add_partner.py`, `add_account_command.py`
- [x] `railway_aliases.py`, `add_user_aliases.py`, `check_doner_sales_nov20.py`
- [x] `check_subscriptions.py`, `invoice_manual_selection.py`, `poster_links.py`
- [x] `test_migration.py`

**Удалённая документация (6 шт):**
- [x] `ETAP1_COMPLETE.md`, `PR_DESCRIPTION.md`, `MANUAL_CHECK.md`
- [x] `RAILWAY_GRPC_FIX.md`, `MULTI_ACCOUNT_PLAN.md`, `SALARY_FLOW_IMPLEMENTATION.md`

### 2.8 Удалить mock данные из API

- [ ] `/api/dashboard` (web_app.py:470-560) — возвращает фейковые данные
- [ ] `/api/supplies` (web_app.py) — mock данные
- [ ] `/api/supplies/<id>` (web_app.py) — mock данные
- [ ] Либо удалить эндпоинты, либо реализовать с реальными данными

---

## ~~ЭТАП 3: Утечки соединений БД~~ ГОТОВО

### 3.1 ~~Добавить автоматическое закрытие соединений~~ ГОТОВО

~~**Проблема:** 84 из 85 методов database.py имели утечки соединений при ошибках.~~

**Решение:** `_ManagedConnection` — обёртка над соединением, которая:
- [x] Безопасный повторный close() (без ошибки при двойном вызове)
- [x] `__del__` ловит утечки при garbage collection (CPython — немедленно)
- [x] Поддержка context manager (`with` statement)
- [x] Прозрачный passthrough: cursor(), commit(), rollback(), row_factory

**Как работает:** `_get_connection()` теперь возвращает `_ManagedConnection(conn)`.
Все 85 методов автоматически защищены — НОЛЬ изменений в существующем коде.

**Файлы изменены:** `database.py`

---

## ЭТАП 4: Замена print на logging

### 4.1 Заменить debug print на logging

**Проблема:** 40+ мест с `print(f"[DEBUG] ...")` и `print(f"...")` в web_app.py. Засоряет логи Railway, нет уровней.

- [ ] Заменить все `print(...)` в web_app.py на `logger.debug()` / `logger.info()` / `logger.error()`
- [ ] Аналогично в bot.py — заменить print на logging
- [ ] Настроить уровень логирования через env-переменную `LOG_LEVEL`

**Файлы:** `web_app.py`, `bot.py`, `database.py`

---

## ЭТАП 5: Рефакторинг структуры

### 5.1 Разбить bot.py на модули

**Проблема:** 6,800 строк в одном файле — невозможно ориентироваться.

- [ ] Создать `handlers/` директорию
- [ ] Вынести команды (`/start`, `/help`, `/sync`, `/cancel`) в `handlers/commands.py`
- [ ] Вынести обработку текста в `handlers/text.py`
- [ ] Вынести callback-кнопки в `handlers/callbacks.py`
- [ ] Вынести flow поставок в `handlers/supply_flow.py`
- [ ] Вынести flow расходов в `handlers/expense_flow.py`
- [ ] Обновить импорты в `bot.py` (оставить как точку входа)

### 5.2 Разбить web_app.py на Flask Blueprints

**Проблема:** 6,200 строк в одном файле, 30+ маршрутов.

- [ ] Создать `blueprints/` директорию
- [ ] Вынести расходы в `blueprints/expenses.py`
- [ ] Вынести поставки в `blueprints/supplies.py`
- [ ] Вынести shift closing в `blueprints/shift_closing.py`
- [ ] Вынести алиасы в `blueprints/aliases.py`
- [ ] Вынести аутентификацию в `blueprints/auth.py`
- [ ] Зарегистрировать blueprints в `web_app.py`

### 5.3 Объединить дублирующийся код shift closing

**Проблема:** Три набора эндпоинтов (owner/cafe/cashier) с ~70% одинакового кода.

- [ ] Выделить общую логику расчёта в отдельную функцию
- [ ] Параметризировать по роли вместо дублирования
- [ ] Аналогично для HTML-шаблонов: 4 файла shift_closing → 1 с условной логикой

---

## ЭТАП 6: Оптимизация производительности

### 6.1 Параллельные запросы к Poster API

**Проблема:** Для каждого аккаунта — 3 последовательных HTTP-запроса. При 2 аккаунтах = 6 запросов (~3-6 сек).

- [ ] Заменить последовательные вызовы на `asyncio.gather()` для параллельного выполнения
- [ ] Ожидаемое ускорение: 3-6x на страницах с загрузкой данных из Poster

**Файлы:** `web_app.py` (строки 1238-1312 и аналогичные места)

### 6.2 Один event loop вместо создания нового на каждый запрос

**Проблема:** `asyncio.new_event_loop()` вызывается 40+ раз — на каждый HTTP-запрос создаётся новый loop.

- [ ] Создать вспомогательную функцию `run_async(coro)` с единой точкой создания loop
- [ ] Или перейти на async Flask (Quart) в перспективе

### 6.3 Вынести магические числа в конфигурацию

- [ ] `account_id = 4` → `config.ACCOUNT_CASH`
- [ ] `score < 75` → `config.MIN_MATCH_CONFIDENCE`
- [ ] `items[:15]` → `config.MAX_INLINE_BUTTONS`
- [ ] `cash_to_leave = 15000` → `config.DEFAULT_CASH_TO_LEAVE`
- [ ] И другие числовые константы

**Файлы:** `config.py`, `web_app.py`, `bot.py`, `matchers.py`

---

## ЭТАП 7: Фронтенд (Mini App)

### 7.1 Разбить монолитные компоненты React

- [ ] `Expenses.tsx` (1,734 строки) → 5-6 компонентов (ExpenseList, ExpenseForm, ExpenseFilters, ReconciliationPanel и т.д.)
- [ ] `CreateSupply.tsx` (1,024 строки) → 3-4 компонента (SupplyForm, SupplyItemRow, IngredientAutocomplete и т.д.)

### 7.2 Добавить Error Boundaries

- [ ] Обернуть каждую страницу в Error Boundary
- [ ] При JS-ошибке — показывать "Произошла ошибка, обновите страницу" вместо белого экрана

### 7.3 Извлечь общие утилиты

- [ ] `evaluateExpression()` — дублируется между файлами → вынести в `utils/math.ts`
- [ ] Форматирование дат — вынести в `utils/date.ts`
- [ ] Форматирование денег — вынести в `utils/format.ts`

### 7.4 Постепенный перевод Flask → React

- [ ] Начать с shift_closing (4 HTML-шаблона → 1 React-компонент с параметром роли)
- [ ] По одной странице за раз, без спешки

---

## ЭТАП 8: Тесты и CI

### 8.1 Перевести тесты на pytest

- [ ] Установить pytest
- [ ] Обернуть существующие тестовые скрипты в pytest-формат
- [ ] Добавить тесты на критичные операции (удаление, создание транзакций)

### 8.2 Добавить валидацию входных данных

- [ ] Pydantic-модели для JSON-эндпоинтов (суммы, даты, ID)
- [ ] Rate limiting на `/login` (Flask-Limiter)

---

## Порядок выполнения

| Приоритет | Этап | Описание | Оценка сложности |
|-----------|------|---------|-----------------|
| 1 | 1.1-1.2 | TELEGRAM_USER_ID + обход аутентификации | Средняя |
| 2 | 1.3 | Проверка владельца при удалении | Средняя |
| 3 | 2.1-2.7 | Удаление мёртвого кода и скриптов | Простая |
| 4 | 3.1 | Утечки соединений БД | Средняя |
| 5 | 4.1 | Замена print на logging | Простая |
| 6 | 2.8 | Mock данные | Простая |
| 7 | 5.1-5.3 | Рефакторинг структуры (bot.py, web_app.py) | Сложная |
| 8 | 6.1-6.3 | Оптимизация производительности | Средняя |
| 9 | 7.1-7.4 | Фронтенд оптимизация | Средняя |
| 10 | 8.1-8.2 | Тесты и CI | Средняя |
