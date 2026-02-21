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

### 1.3 ~~Добавить проверку владельца при удалении/обновлении~~ ГОТОВО

~~**Проблема:** 11 эндпоинтов (7 delete + 4 update) не проверяли чей это черновик.~~

**Что сделано:**
- [x] Добавлен опциональный `telegram_user_id` параметр в 7 методов database.py: `delete_expense_draft()`, `delete_expense_drafts_bulk()`, `delete_supply_draft()`, `delete_supply_draft_item()`, `update_expense_draft()`, `update_supply_draft()`, `update_supply_draft_item()`
- [x] Когда `telegram_user_id` передан — добавляется `AND telegram_user_id = ?` в WHERE (или подзапрос для supply_draft_items через parent supply_drafts)
- [x] Все 15 пользовательских эндпоинтов в web_app.py передают `g.user_id`
- [x] Внутренние/фоновые вызовы (sync, bot) продолжают работать без user_id (обратная совместимость)
- [x] Удалён дубликат метода `delete_supply_draft_item` (был определён дважды)

**Файлы изменены:** `web_app.py`, `database.py`

### 1.4 ~~Убрать отправку пароля открытым текстом~~ ГОТОВО

~~**Проблема:** Команда `/staff create` отправляет пароль текстом в Telegram-чат.~~

**Что сделано:**
- [x] Команда пользователя (содержит пароль) удаляется из чата
- [x] Ответ бота с паролем автоматически удаляется через 60 секунд
- [x] Предупреждение "Это сообщение будет удалено через 60 секунд. Запишите пароль!"
- [x] Аналогично для `/staff reset` — сообщение удаляется через 60 сек

**Файлы изменены:** `bot.py`

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

### 2.8 ~~Удалить mock данные из API~~ ГОТОВО

**Что сделано:**
- [x] Удалены 3 mock-эндпоинта: `/api/dashboard`, `/api/supplies`, `/api/supplies/<id>`
- [x] Удалены React-страницы: `Dashboard.tsx`, `SupplyHistory.tsx`, `SupplyDetail.tsx`
- [x] Удалён компонент `SupplyCard.tsx`
- [x] Очищены типы (`DashboardData`, `Supply`, `SupplyDetail`, `SuppliesResponse`)
- [x] Обновлены маршруты в `App.tsx` — `/` теперь ведёт на Расходы
- [x] Обновлена навигация в `AppLayout.tsx`
- [x] Удалены методы API-клиента: `getDashboard()`, `getSupplies()`, `getSupply()`

**Файлы изменены:** `web_app.py`, `mini_app/src/App.tsx`, `mini_app/src/api/client.ts`, `mini_app/src/types/index.ts`, `mini_app/src/layouts/AppLayout.tsx`
**Файлы удалены:** 4 файла в `mini_app/src/`

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

## ~~ЭТАП 4: Замена print на logging~~ ГОТОВО

### 4.1 ~~Заменить debug print на logging~~ ГОТОВО

~~**Проблема:** 94 места с `print(...)` в web_app.py. Засоряет логи Railway, нет уровней.~~

**Что сделано:**
- [x] Заменены все 94 `print(...)` в web_app.py на `logger.debug()` / `logger.info()` / `logger.warning()` / `logger.error()`
- [x] bot.py — уже использовал logging, замен не требовалось (0 print-ов)
- [x] Уровень `LOG_LEVEL` настраивается через env-переменную (config.py, default=INFO)
- [x] `bot.py` и `start_server.py` используют `LOG_LEVEL` из config

**Файлы изменены:** `web_app.py`, `bot.py`, `start_server.py`

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

### 6.1 ~~Параллельные запросы к Poster API~~ ГОТОВО

~~**Проблема:** Для каждого аккаунта — 3-5 последовательных HTTP-запросов.~~

**Что сделано (7 мест оптимизировано):**
- [x] `get_poster_data_primary()` — shift closing: 2 запроса параллельно (sales + cash shifts)
- [x] `get_cafe_poster_data()` — cafe shift closing: 2 запроса параллельно
- [x] `load_data()` для /expenses — 3 запроса параллельно на аккаунт (categories + accounts + transactions)
- [x] `load_data()` для /supplies — 3 запроса параллельно на аккаунт
- [x] `sync_from_all_accounts()` — 2 запроса параллельно на аккаунт
- [x] `fetch_and_sync()` — 2 запроса параллельно на аккаунт
- [x] `fetch_all_transactions()` — 2 запроса параллельно на аккаунт
- [x] `create_supplies_in_poster()` — 5 запросов параллельно (suppliers + accounts + storages + ingredients + products)

**Ожидаемое ускорение:** 2-5x на страницах закрытия смены и расходов

**Файлы изменены:** `web_app.py`

### 6.2 ~~Один event loop вместо создания нового на каждый запрос~~ ГОТОВО

~~**Проблема:** `asyncio.new_event_loop()` вызывается 40+ раз — на каждый HTTP-запрос создаётся новый loop.~~

**Что сделано:**
- [x] Создана функция `run_async(coro)` — единая точка создания/закрытия event loop
- [x] Заменены все 23 паттерна `asyncio.new_event_loop()` + `loop.run_until_complete()` + `loop.close()`
- [x] 6 сложных случаев (несколько `run_until_complete` на одном loop) рефакторены в async-функции

**Файлы изменены:** `web_app.py`

### 6.3 ~~Вынести магические числа в конфигурацию~~ ГОТОВО

~~**Проблема:** Захардкоженные числовые константы разбросаны по коду.~~

**Что сделано:**
- [x] `account_id = 4` → `config.DEFAULT_ACCOUNT_FROM_ID` (уже существовал, но 3 места в bot.py использовали хардкод)
- [x] `score < 75` → `config.MIN_MATCH_CONFIDENCE` (bot.py + 4 метода в matchers.py)
- [x] `cash_to_leave = 15000` → `config.DEFAULT_CASH_TO_LEAVE` (web_app.py + shift_closing.html)
- [x] Все 3 новые константы настраиваются через env-переменные

**Файлы изменены:** `config.py`, `bot.py`, `matchers.py`, `web_app.py`, `templates/shift_closing.html`

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
