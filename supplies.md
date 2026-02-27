# Страница Поставки — Полная спецификация backend

> Этот документ описывает **всю** backend-логику страницы "Поставки" (`/supplies`).
> Предназначен для воссоздания на новом сервере с нуля (aiohttp + PostgreSQL).

---

## 1. Общая концепция

Страница "Поставки" позволяет:

1. Создавать **черновики поставок** (supply_drafts) с позициями (supply_draft_items)
2. Искать ингредиенты/товары через **autocomplete** (данные из Poster API)
3. Искать поставщиков с **fuzzy-matching** (из CSV + алиасов)
4. Повторять предыдущие поставки ("Повторить последнюю")
5. Видеть подсказки по **последней цене** каждого ингредиента
6. **Отправлять** поставку в Poster POS-систему (самый сложный endpoint)

### Основной flow

```
Autocomplete (Poster API)
         │
         ▼
supply_draft_items ─► supply_draft ─► process ─► Poster API (storage.createSupply)
         ▲                                              │
         │                                              ▼
  price_history                                  ingredient_price_history (БД)
  (подсказки)                                    + linked expense_draft обновлён
```

### Связь с расходами

Поставка может быть **привязана к расходу** через `supply_draft.linked_expense_draft_id`. При создании поставки в Poster:
- Расход получает `poster_transaction_id = "supply_{supply_id}"`
- Расход помечается как `completion_status='completed'`
- Синхронизация расходов пропускает транзакции с паттерном "Поставка N#XXXXX"

---

## 2. Таблицы БД

### 2.1 `supply_drafts` — Черновики поставок

```sql
CREATE TABLE supply_drafts (
    id                       SERIAL PRIMARY KEY,
    telegram_user_id         BIGINT NOT NULL,
    supplier_name            TEXT,
    supplier_id              INTEGER,
    invoice_date             DATE,
    total_sum                DECIMAL(12,2) DEFAULT 0,
    source                   TEXT DEFAULT 'cash',        -- 'cash' | 'kaspi' | 'halyk'
    status                   TEXT DEFAULT 'pending',     -- 'pending' | 'processed'
    linked_expense_draft_id  INTEGER REFERENCES expense_drafts(id) ON DELETE SET NULL,
    account_id               INTEGER,                    -- ID финансового счёта (опционально)
    ocr_text                 TEXT,
    created_at               TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    processed_at             TIMESTAMP
);
```

### 2.2 `supply_draft_items` — Позиции поставки

```sql
CREATE TABLE supply_draft_items (
    id                     SERIAL PRIMARY KEY,
    supply_draft_id        INTEGER NOT NULL REFERENCES supply_drafts(id) ON DELETE CASCADE,
    item_name              TEXT NOT NULL,
    quantity               DECIMAL(10,3) DEFAULT 1,
    unit                   TEXT DEFAULT 'шт',
    price_per_unit         DECIMAL(12,2) DEFAULT 0,
    total                  DECIMAL(12,2) DEFAULT 0,        -- quantity × price_per_unit (auto)
    poster_ingredient_id   INTEGER,                        -- ID ингредиента/товара в Poster
    poster_ingredient_name TEXT,                            -- Название в Poster
    item_type              TEXT,                            -- 'ingredient' | 'semi_product' | 'product'
    poster_account_id      INTEGER,                        -- К какому Poster-аккаунту относится
    poster_account_name    TEXT,                            -- "Pizzburg" или "Pizzburg-cafe"
    storage_id             INTEGER,                        -- ID склада
    storage_name           TEXT                             -- Название склада
);
```

### 2.3 `ingredient_price_history` — История цен

```sql
CREATE TABLE ingredient_price_history (
    id                SERIAL PRIMARY KEY,
    telegram_user_id  BIGINT NOT NULL,
    ingredient_id     INTEGER NOT NULL,
    ingredient_name   TEXT,
    supplier_id       INTEGER,
    supplier_name     TEXT,
    date              DATE NOT NULL,
    price             DECIMAL(10,2) NOT NULL,
    quantity          DECIMAL(10,3),
    unit              TEXT,
    supply_id         INTEGER,                   -- ID поставки в Poster (после создания)
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_price_history_ingredient ON ingredient_price_history(telegram_user_id, ingredient_id);
CREATE INDEX idx_price_history_supplier ON ingredient_price_history(telegram_user_id, supplier_id);
```

---

## 3. API Endpoints

### 3.1 `GET /api/items/search` — Поиск ингредиентов/товаров (autocomplete)

**Query params:**
- `q` (string, optional) — поисковый запрос
- `source` (string, optional, default `'all'`) — `'ingredient'`, `'product'`, `'all'`

**Логика:**

1. Для каждого бизнес-аккаунта **параллельно** (asyncio.gather):
   - Загрузить `get_ingredients()` из Poster API
   - Загрузить `get_products()` из Poster API
2. **Ингредиенты:** Пропустить удалённые (`delete == '1'`). Тип Poster `'2'` → `'semi_product'`, иначе `'ingredient'`. Добавить `poster_account_id` и `poster_account_name`.
3. **Товары:** Пропустить удалённые. **Только категория "Напитки"** (`category_name.startswith('Напитки')`). Тип = `'product'`. Добавить `poster_account_id`, `poster_account_name`.
4. Объединить все items из всех аккаунтов.
5. Если `q` задан → substring match (case-insensitive), лимит 50.
6. Если `q` пуст → вернуть **ВСЕ** items, сортированные по `(poster_account_name, name)`, **без лимита** — для предзагрузки на клиенте.

**Response:**
```json
[
  {
    "id": 83,
    "name": "Фри",
    "type": "ingredient",
    "poster_account_id": 1,
    "poster_account_name": "Pizzburg",
    "storage_id": 1,
    "storage_name": "Продукты"
  },
  {
    "id": 42,
    "name": "Кока-Кола 1л",
    "type": "product",
    "poster_account_id": 2,
    "poster_account_name": "Pizzburg-cafe"
  }
]
```

**Fallback:** При ошибке API → загрузить из CSV (`poster_ingredients.csv` + `poster_products.csv`). В CSV нет `poster_account_id` и `storage_id`.

> **Важно для UI:** При входе на страницу поставок выполняется один запрос без `q` — это подгружает ВСЕ ингредиенты. После этого autocomplete работает мгновенно на клиенте (фильтрация в JS).

---

### 3.2 `GET /api/suppliers` — Список поставщиков

**Без параметров.**

**Логика:** Читает `poster_suppliers.csv`. Возвращает все записи.

**Response:**
```json
{
  "suppliers": [
    {"id": 6, "name": "Смолл", "aliases": ["Смол", "Small"]},
    {"id": 7, "name": "Кус Вкус", "aliases": ["кусвкус", "иртышинтерфуд"]}
  ]
}
```

---

### 3.3 `GET /api/suppliers/search` — Поиск поставщиков

**Query params:** `q` (string)

**Логика:** Загружает из CSV, substring match по имени (case-insensitive), лимит 20.

**Response:**
```json
[{"id": 6, "name": "Смолл"}, {"id": 7, "name": "Кус Вкус"}]
```

---

### 3.4 `GET /api/supplies/last/<supplier_id>` — Последняя поставка от поставщика

**Для кнопки "Повторить".**

**Логика:**
1. Загрузить `ingredient_price_history` по `supplier_id`
2. Сгруппировать по `ingredient_id`, оставить только последнюю запись каждого
3. Лимит 50 уникальных позиций

**Response:**
```json
{
  "supplier_id": 6,
  "items": [
    {"id": 83, "name": "Фри", "price": 1440.0, "quantity": 1.0, "unit": "шт", "date": "2026-02-25"},
    {"id": 231, "name": "Брынза сыр", "price": 3200.0, "quantity": 2.0, "unit": "кг", "date": "2026-02-25"}
  ]
}
```

---

### 3.5 `GET /api/items/price-history/<item_id>` — История цен ингредиента

**Query params:** `supplier_id` (int, optional) — фильтр по поставщику

**Логика:** Последние 10 записей из `ingredient_price_history`.

**Response:**
```json
{
  "item_id": 83,
  "history": [
    {"price": 1440.0, "quantity": 5.0, "date": "2026-02-25", "supplier_name": "Смолл"},
    {"price": 1380.0, "quantity": 10.0, "date": "2026-02-20", "supplier_name": "Смолл"}
  ]
}
```

---

### 3.6 CRUD черновиков

#### `POST /api/supply-drafts` — Создать черновик

**Body:**
```json
{
  "supplier_name": "Смолл",
  "invoice_date": "2026-02-27",
  "linked_expense_draft_id": 42,
  "source": "cash"
}
```
**Response:** `{"success": true, "id": 15}`

#### `PUT /api/supply-drafts/<draft_id>` — Обновить черновик

**Body (все опциональны):**
```json
{
  "supplier_name": "Смолл",
  "supplier_id": 6,
  "source": "kaspi",
  "linked_expense_draft_id": 42,
  "invoice_date": "2026-02-27"
}
```

**Важно:** При изменении `source` — автоматически обновить `source` у связанного `expense_draft` (если есть `linked_expense_draft_id`).

#### `DELETE /api/supply-drafts/<draft_id>` — Удалить черновик
Каскадно удаляет все `supply_draft_items` (через FK ON DELETE CASCADE).

---

### 3.7 CRUD позиций

#### `POST /api/supply-drafts/<draft_id>/items` — Добавить позицию

**Body:**
```json
{
  "ingredient_id": 83,
  "ingredient_name": "Фри",
  "quantity": 5,
  "price": 1440,
  "unit": "шт",
  "item_type": "ingredient",
  "poster_account_id": 1,
  "poster_account_name": "Pizzburg",
  "storage_id": 1,
  "storage_name": "Продукты"
}
```

**Маппинг полей (frontend → DB):**
- `ingredient_id` → `poster_ingredient_id`
- `ingredient_name` → `poster_ingredient_name` + `item_name`
- `price` → `price_per_unit`
- `total` = `quantity × price` (рассчитывается автоматически)

**Response:** `{"success": true, "id": 42}`

#### `PUT /api/supply-drafts/items/<item_id>` — Обновить позицию

**Body (все опциональны):**
```json
{
  "ingredient_id": 83,
  "ingredient_name": "Фри",
  "price": 1500,
  "quantity": 3,
  "unit": "кг",
  "poster_account_id": 1,
  "poster_account_name": "Pizzburg"
}
```

**Логика:** При изменении `price` или `quantity` → пересчитать `total = quantity × price`.

#### `DELETE /api/supply-drafts/items/<item_id>` — Удалить позицию

---

### 3.8 `GET /api/supply-drafts` — Список черновиков с позициями

**Логика:**
1. Загрузить pending черновики из БД
2. Отфильтровать только за **сегодня** (Kazakhstan time)
3. Для каждого — загрузить items
4. Если есть `linked_expense_draft_id` — подгрузить сумму и source связанного расхода
5. Загрузить `pending_supplies` — expense_drafts с `expense_type='supply'` без привязанного supply_draft (доступные для привязки)
6. Загрузить список бизнес-аккаунтов

**Response:**
```json
{
  "drafts": [
    {
      "id": 15,
      "supplier_name": "Смолл",
      "supplier_id": 6,
      "source": "cash",
      "invoice_date": "2026-02-27",
      "total_sum": 8640.0,
      "linked_expense_draft_id": 42,
      "linked_expense_amount": 8640.0,
      "linked_expense_source": "cash",
      "items": [
        {
          "id": 100,
          "ingredient_id": 83,
          "ingredient_name": "Фри",
          "quantity": 5.0,
          "price": 1440.0,
          "unit": "шт",
          "total": 7200.0,
          "item_type": "ingredient",
          "poster_account_id": 1,
          "poster_account_name": "Pizzburg",
          "storage_id": 1,
          "storage_name": "Продукты"
        }
      ]
    }
  ],
  "pending_supplies": [
    {"id": 43, "amount": 5000, "description": "Поставка молоко", "source": "cash"}
  ],
  "poster_accounts": [
    {"id": 1, "name": "Pizzburg", "is_primary": true},
    {"id": 2, "name": "Pizzburg-cafe", "is_primary": false}
  ]
}
```

---

### 3.9 `POST /api/supply-drafts/<draft_id>/process` — Создать поставку в Poster (ГЛАВНЫЙ ENDPOINT)

Самый сложный endpoint. Создаёт поставку(и) в Poster API с полной валидацией типов.

#### Шаг 1: Загрузка и валидация

```python
draft = db.get_supply_draft_with_items(draft_id)
items = draft['items']

# Проверки:
assert items, "Нет позиций в поставке"
assert all(item.poster_ingredient_id for item in items), "Не все товары привязаны к ингредиентам Poster"
```

#### Шаг 2: Группировка по аккаунту

```python
items_by_account = defaultdict(list)
for item in items:
    acc_id = item.poster_account_id or primary_account.id
    items_by_account[acc_id].append(item)
```

Если ингредиенты из разных аккаунтов → создаются **отдельные поставки** для каждого аккаунта.

#### Шаг 3: Для каждого аккаунта

##### 3a. Параллельная загрузка данных (5 запросов одновременно):

```python
suppliers, finance_accounts, storages, account_ingredients, account_products = await asyncio.gather(
    client.get_suppliers(),
    client.get_accounts(),
    client.get_storages(),
    client.get_ingredients(),
    client.get_products()
)
```

##### 3b. Определение `supplier_id`:

```python
# 1. Partial match: supplier_name.lower() in poster_supplier_name.lower()
for s in suppliers:
    if draft.supplier_name.lower() in s['supplier_name'].lower():
        supplier_id = int(s['supplier_id'])
        break

# 2. Fallback: первый поставщик из списка
if not supplier_id:
    supplier_id = int(suppliers[0]['supplier_id'])
```

##### 3c. Определение `account_id` (финансовый счёт) по `source`:

```python
if draft.account_id:
    # Если уже задан явно — использовать его
    account_id = draft.account_id
else:
    name = finance_account['name'].lower()  # или account_name
    if source == 'kaspi':
        # Ищем счёт с 'kaspi' в названии
    elif source == 'halyk':
        # 'халык' или 'halyk'
    else:  # cash
        # 'закуп' или 'оставил'
    # Fallback: первый счёт
```

##### 3d. Определение `storage_id`:

```python
# Из API Poster: используем первый склад как дефолт
api_default_storage_id = int(storages[0]['storage_id']) if storages else 1

# Из позиций: если у первого item есть storage_id — используем его
for item in account_items:
    if item.storage_id:
        supply_storage_id = int(item.storage_id)
        break
else:
    supply_storage_id = api_default_storage_id
```

##### 3e. Валидация типов (ingredient vs product namespace) — КРИТИЧНО:

Poster имеет **два отдельных пространства ID**: ингредиенты и товары. Один и тот же ID может существовать в обоих.

```python
# Строим два словаря из данных API:
valid_ingredient_ids = {}   # {id: (name, type_str)}
valid_product_ids = {}      # {id: name}
ingredient_name_to_id = {}  # {lowercase_name: (id, type)}

# Из get_ingredients(): пропускаем delete=='1'
for ing in account_ingredients:
    if ing.get('delete') == '1': continue
    type_str = 'semi_product' if ing.get('type') == '2' else 'ingredient'
    valid_ingredient_ids[int(ing['ingredient_id'])] = (ing['ingredient_name'], type_str)

# Из get_products(): пропускаем delete=='1'
for prod in account_products:
    if prod.get('delete') == '1': continue
    valid_product_ids[int(prod['product_id'])] = prod['product_name']
```

Для каждого item:

```
1. Если item_type == 'ingredient'/'semi_product' И id ∈ valid_ingredient_ids:
   → ВАЛИДЕН. Скорректировать type из API данных (может быть semi_product вместо ingredient).

2. Если item_type == 'product' И id ∈ valid_product_ids:
   → ВАЛИДЕН.

3. Если id ∈ valid_ingredient_ids НО тип указан как 'product':
   → Автокоррекция: сменить тип на реальный (ingredient/semi_product). ЛОГ: "Type correction".

4. Если id ∈ valid_product_ids НО тип указан как 'ingredient':
   → Автокоррекция: сменить тип на 'product'. ЛОГ: "Type correction".

5. Если id не найден ни в одном namespace:
   → Попробовать найти по ИМЕНИ (case-insensitive exact match):
     ingredient_name_to_id[item_name.lower()]
   → Если найден → использовать правильный ID и тип. ЛОГ: "ID correction".

6. Если ничего не помогло:
   → Добавить в missing_items. ПРОПУСТИТЬ.
```

Если есть `missing_items` → **ошибка**:
```
"В аккаунте {account_name} не найдены ингредиенты: {names}. Проверьте, что ингредиенты существуют в этом заведении."
```

##### 3f. Формирование данных для API:

```python
ingredients_for_api = []
for item in valid_items:
    ingredients_for_api.append({
        'id': item_id,
        'num': float(quantity),
        'price': float(price_per_unit),
        'type': item_type  # 'ingredient', 'semi_product', 'product'
    })
```

##### 3g. Вызов Poster API:

```python
supply_id = await client.create_supply(
    supplier_id=supplier_id,
    storage_id=supply_storage_id,
    date=f"{supply_date} 12:00:00",
    ingredients=ingredients_for_api,
    account_id=account_id,
    comment=f"Накладная от {supplier_name}"
)
```

> Метод `create_supply()` внутри реализует **3 fallback-стратегии** (см. раздел 4).

#### Шаг 4: Постобработка

1. **Пометить draft как processed:**
```python
db.mark_supply_draft_processed(draft_id)
```

2. **Обновить связанный expense_draft:**
```python
if draft.linked_expense_draft_id:
    supply_ids_str = ','.join(str(s['supply_id']) for s in created_supplies)
    poster_txn_id = f"supply_{supply_ids_str}"

    db.update_expense_draft(
        draft.linked_expense_draft_id,
        source=draft.source,
        poster_transaction_id=poster_txn_id
    )
    db.mark_drafts_in_poster([draft.linked_expense_draft_id])
```

Это ставит `poster_transaction_id = "supply_1234"` на расходе → синхронизация расходов будет пропускать этот расход (не создаст дубль).

3. **Сохранить историю цен** (для подсказок):
```python
for item in all_created_items:
    db.save_price_history(
        telegram_user_id, ingredient_id, ingredient_name,
        supplier_id, supplier_name,
        price, quantity, unit, supply_id, date
    )
```

**Response:**
```json
{
  "success": true,
  "supply_id": 1234,
  "supplies": [
    {"supply_id": 1234, "account_name": "Pizzburg", "items_count": 3, "total": 7200}
  ]
}
```

**Ошибка:**
```json
{
  "success": false,
  "error": "В аккаунте Pizzburg-cafe не найдены ингредиенты: Фри, Брынза. Проверьте, что ингредиенты существуют в этом заведении."
}
```

---

## 4. Poster API — `storage.createSupply` (3 стратегии)

Poster API для создания поставки использует **form-urlencoded** (НЕ JSON!).

Документация Poster противоречива — разные аккаунты принимают разные форматы. Поэтому используется fallback: **docs → legacy → mixed**.

### Стратегия 1: Docs format (основная)

```
POST /api/storage.createSupply?token={token}
Content-Type: application/x-www-form-urlencoded

supply[date]=2026-02-27 12:00:00
supply[supplier_id]=6
supply[storage_id]=1
supply[supply_comment]=Накладная от Смолл
supply[account_id]=5

ingredient[0][id]=83
ingredient[0][type]=4
ingredient[0][num]=5
ingredient[0][sum]=1440

ingredient[1][id]=42
ingredient[1][type]=1
ingredient[1][num]=10
ingredient[1][sum]=200

transactions[0][account_id]=5
transactions[0][date]=2026-02-27 12:00:00
transactions[0][amount]=9200
transactions[0][delete]=0
```

**Type mapping docs:** `ingredient=4, semi_product=4, product=1`

> `sum` — это цена за **единицу** (не общая сумма позиции!)
> `transactions[0][amount]` — общая сумма поставки = sum(num × price)

### Стратегия 2: Legacy format (при ошибке 32)

```
date=2026-02-27 12:00:00
supplier_id=6
storage_id=1
supply_comment=Накладная от Смолл
source=manage
type=1

ingredients[0][id]=83
ingredients[0][type]=1
ingredients[0][num]=5
ingredients[0][price]=1440
ingredients[0][ingredient_sum]=7200
ingredients[0][tax_id]=0
ingredients[0][packing]=1

transactions[0][account_id]=5
transactions[0][date]=2026-02-27 12:00:00
transactions[0][amount]=9200
transactions[0][delete]=0
```

**Type mapping legacy:** `ingredient=1, semi_product=2, product=4`

Отличия:
- Плоские поля (без `supply[]` обёртки)
- `ingredients[]` (множ. число) вместо `ingredient[]` (ед. число)
- `price` вместо `sum`
- Дополнительные поля: `ingredient_sum`, `tax_id`, `packing`
- Корневые `source=manage` и `type=1`

### Стратегия 3: Mixed (docs формат + legacy types)

Обёртка `supply[]` + `ingredient[]` как в docs, но type mapping из legacy:
`ingredient=1, semi_product=2, product=4`

### Логика fallback:

```
try docs_format(docs_type_map):
    → Успех → return supply_id
except Poster error (обычно code 32):
    try legacy_format(legacy_type_map):
        → Успех → return supply_id
    except:
        try docs_format(legacy_type_map):
            → Успех → return supply_id
        except:
            → Raise с деталями всех 3 ошибок
```

**Response при успехе:**
```json
{"response": 1234}  // supply_id
```

**Ошибка 32** обычно означает: ID ингредиента не существует в этом аккаунте, или неправильный type mapping.

### Обработка дробных количеств

```python
num = item['num']  # float
if isinstance(num, float):
    if num.is_integer():
        num_for_api = int(num)     # 5.0 → 5
    else:
        num_for_api = str(num)     # 2.5 → "2.5"
```

---

## 5. Poster API — Другие эндпоинты для поставок

### 5.1 `menu.getIngredients`

```
GET /api/menu.getIngredients?token={token}
```

**Response:**
```json
[
  {
    "ingredient_id": "83",
    "ingredient_name": "Фри",
    "ingredient_unit": "кг",
    "type": "1",              // 1=ingredient, 2=semi_product
    "delete": "0"             // "1" = удалён
  }
]
```

### 5.2 `menu.getProducts`

```
GET /api/menu.getProducts?token={token}
```

**Response:**
```json
[
  {
    "product_id": "42",
    "product_name": "Кока-Кола 1л",
    "category_name": "Напитки",
    "delete": "0"
  }
]
```

> **Фильтрация:** Для поставок используются только товары с `category_name` начинающимся с `"Напитки"`. Остальные — это техкарты (рецепты блюд).

### 5.3 `suppliers.getSuppliers`

```
GET /api/suppliers.getSuppliers?token={token}
```

**Response:**
```json
[
  {
    "supplier_id": "6",
    "supplier_name": "Смолл",
    "supplier_phone": "+7...",
    "supplier_address": "..."
  }
]
```

### 5.4 `storage.getStorages`

```
GET /api/storage.getStorages?token={token}
```

**Response:**
```json
[
  {
    "storage_id": "1",
    "storage_name": "Продукты"
  }
]
```

### 5.5 `finance.getAccounts`

Тот же endpoint что и для расходов. Используется для определения финансового счёта по `source` (см. раздел 3.9, шаг 3c).

---

## 6. CSV-файлы для поставок

### 6.1 `poster_ingredients.csv`

| Колонка | Описание |
|---------|----------|
| `ingredient_id` | ID в Poster |
| `ingredient_name` | Название |
| `unit` | Единица измерения |
| `account_name` | Poster-аккаунт |
| `type` | "1"=ingredient, "2"=semi_product |

Используется как **fallback** при недоступности API.

### 6.2 `poster_products.csv`

| Колонка | Описание |
|---------|----------|
| `product_id` | ID в Poster |
| `product_name` | Название |
| `category_name` | Категория |
| `account_name` | Poster-аккаунт |

Фильтр: только `category_name` начинающийся с `"Напитки"`.

### 6.3 `poster_suppliers.csv`

| Колонка | Описание |
|---------|----------|
| `supplier_id` | ID в Poster |
| `name` | Название |
| `aliases` | Алиасы через `\|` |

---

## 7. DB-операции (полный список)

### Supply Drafts

```
create_empty_supply_draft(telegram_user_id, supplier_name, invoice_date, source, linked_expense_draft_id) → id
get_supply_drafts(telegram_user_id, status="pending") → list[dict]
get_supply_draft_with_items(draft_id) → dict с вложенным items[]
update_supply_draft(draft_id, telegram_user_id, **kwargs) → bool
delete_supply_draft(draft_id, telegram_user_id) → bool
mark_supply_draft_processed(draft_id) → sets status='processed', processed_at=now
```

### Supply Draft Items

```
add_supply_draft_item(supply_draft_id, item_name, quantity, unit, price_per_unit,
                      poster_ingredient_id, poster_ingredient_name, item_type,
                      poster_account_id, poster_account_name, storage_id, storage_name) → id
get_supply_draft_items(supply_draft_id) → list[dict]
update_supply_draft_item(item_id, telegram_user_id, **kwargs) → bool
delete_supply_draft_item(item_id, telegram_user_id) → bool
```

**Важно для `update_supply_draft_item`:** При изменении `price_per_unit` или `quantity` → пересчитать `total = quantity × price_per_unit`.

### Price History

```
save_price_history(telegram_user_id, ingredient_id, ingredient_name,
                   supplier_id, supplier_name, price, quantity, unit, supply_id, date) → id
get_price_history(telegram_user_id, ingredient_id=None, supplier_id=None, limit=10) → list[dict]
bulk_add_price_history(telegram_user_id, records[]) → count
```

---

## 8. Мульти-аккаунт: ключевые правила

1. **Разные аккаунты имеют разные ID ингредиентов.** "Фри" = id=83 в Pizzburg, id=295 в Pizzburg-cafe.
2. **Каждый item хранит `poster_account_id`** — привязку к конкретному аккаунту.
3. При создании поставки items **группируются по `poster_account_id`** → для каждой группы создаётся **отдельная поставка** через API соответствующего аккаунта.
4. Если у item нет `poster_account_id` → он идёт в **primary account** (is_primary=true).
5. **Валидация ID** происходит строго в контексте целевого аккаунта — нельзя послать id=83 в API Pizzburg-cafe.

---

## 9. Структура UI (элементы)

> Только описание элементов, без стилей.

### Шапка
- Заголовок "Поставки"
- Кнопка "+ Добавить поставку" → `POST /api/supply-drafts`

### Карточки поставок (по одной на черновик)

Каждая карточка:

**Шапка карточки:**
- Поиск поставщика (autocomplete). При выборе → `PUT /api/supply-drafts/<id>` с `supplier_name`, `supplier_id`
- Кнопка "Повторить" → `GET /api/supplies/last/<supplier_id>` → добавляет все items из ответа
- Итого сумма (readonly, автоподсчёт)
- Дата поставки (input date)
- Селектор источника оплаты: Наличка / Kaspi / Halyk (dropdown) → `PUT /api/supply-drafts/<id>` с `source`
- Badge связанного расхода: если есть `linked_expense_draft_id` — показать сумму и source расхода
- Кнопка "Создать" (зелёная) → `POST /api/supply-drafts/<id>/process`
- Кнопка удалить → `DELETE /api/supply-drafts/<id>`

**Таблица позиций:**
- Строка добавления: autocomplete ингредиента → `POST /api/supply-drafts/<id>/items`
- Для каждой позиции:

| Элемент | Тип | Описание |
|---------|-----|----------|
| Ингредиент | readonly text | Название (+ тип badge: ingredient/product) |
| Заведение | badge | poster_account_name (Pizzburg / Cafe) |
| Кол-во | input number | quantity |
| Цена | input number | price_per_unit |
| Подсказка цены | кликабельная | Последняя цена из price_history. Клик → подставить в поле цены |
| Сумма | readonly | quantity × price (автоподсчёт) |
| Удалить | кнопка | `DELETE /api/supply-drafts/items/<id>` |

**Итого по поставке:** Сумма всех `total` позиций

### Общий итог
- Количество черновиков
- Общая сумма всех поставок

### Предзагрузка данных при входе на страницу

При монтировании страницы делается **один запрос** `GET /api/items/search` (без `q`) → загрузить ВСЕ ингредиенты. После этого autocomplete работает мгновенно (фильтрация на клиенте по substring match).

---

## 10. Порядок реализации (рекомендация)

1. **БД:** Создать таблицы `supply_drafts`, `supply_draft_items`, `ingredient_price_history`
2. **Poster Client:** `get_ingredients()`, `get_products()`, `get_suppliers()`, `get_storages()`, `get_accounts()`, `create_supply()` с 3 стратегиями
3. **Search API:** `GET /api/items/search` с предзагрузкой и fallback на CSV
4. **CRUD Draft API:** создание, обновление, удаление черновиков и позиций
5. **Last supply + Price history:** `GET /api/supplies/last/<id>`, `GET /api/items/price-history/<id>`
6. **Process (главный):** `POST /api/supply-drafts/<id>/process` с валидацией типов и fallback
7. **Связь с расходами:** обновление `linked_expense_draft` после создания
8. **UI:** Подключить к API
