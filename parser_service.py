"""Parser service using Claude API for structured data extraction"""
import logging
import json
import base64
from typing import Dict, Optional, List
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
import aiohttp
from config import ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY, GEMINI_MODEL

logger = logging.getLogger(__name__)


TRANSACTION_PARSER_PROMPT = """Ты — помощник для парсинга финансовых транзакций из голосовых сообщений на русском языке.

Твоя задача: извлечь структурированные данные из текста и вернуть JSON.

Примеры входных фраз:
- "Оставил в кассе 7500 тенге донерщик Максат"
- "Транзакция. Повара 12000. Комментарий Ислам"
- "Кассиры 5000 Меруерт"
- "Курьер 8000 Нурлан со счёта Касипай"

Структура JSON ответа:
{{
  "amount": <число в тенге>,
  "category": "<ключевое слово категории: донерщик|повара|кассиры|курьер|кухрабочая|официанты>",
  "comment": "<комментарий, обычно имя сотрудника>",
  "account_from": "<счёт-источник, если указан: касса|закуп|касипай|wolt|форте|халык>",
  "type": "expense"
}}

Правила:
1. amount — всегда число в тенге (без валюты)
2. category — одно слово из списка (без "зарплата", только должность)
3. comment — обычно имя человека или короткое описание
4. account_from — если не указано явно, по умолчанию "закуп" (оставил в кассе на закупы)
5. type — всегда "expense" (расход)

Если что-то не удалось распознать, верни null для этого поля.

ВАЖНО: отвечай ТОЛЬКО валидным JSON, без дополнительного текста!

Текст для парсинга:
{text}"""


MULTIPLE_TRANSACTIONS_PARSER_PROMPT = """Ты — помощник для парсинга НЕСКОЛЬКИХ финансовых транзакций из одного голосового сообщения.

Твоя задача: извлечь структурированные данные о нескольких транзакциях и вернуть JSON.

ФОРМАТ СООБЩЕНИЯ:
"Транзакция, со счёта <Счёт>, <Категория1> <Сумма1> <Комментарий1>, <Категория2> <Сумма2> <Комментарий2>, ..."

ПРИМЕРЫ:

Пример 1:
"Транзакция, со счёта оставил в кассе, кассир 10500 Мадина, кассир 9000 Вика, донерщик 9000 Ислам, донерщик 9000 Ансар помощник донерщика, кухрабочая 8000 Люда, логистика 7600 Караганда"

Результат:
{{
  "type": "multiple_expenses",
  "account": "оставил в кассе",
  "transactions": [
    {{"category": "кассир", "amount": 10500, "comment": "Мадина"}},
    {{"category": "кассир", "amount": 9000, "comment": "Вика"}},
    {{"category": "донерщик", "amount": 9000, "comment": "Ислам"}},
    {{"category": "донерщик", "amount": 9000, "comment": "Ансар помощник донерщика"}},
    {{"category": "кухрабочая", "amount": 8000, "comment": "Люда"}},
    {{"category": "логистика", "amount": 7600, "comment": "Караганда"}}
  ]
}}

Пример 2:
"Транзакция, со счёта каспий, логистика 8000 Астана, маркетинг 4100 реклама, банковские услуги 1 комиссия"

Результат:
{{
  "type": "multiple_expenses",
  "account": "каспий",
  "transactions": [
    {{"category": "логистика", "amount": 8000, "comment": "Астана"}},
    {{"category": "маркетинг", "amount": 4100, "comment": "реклама"}},
    {{"category": "банковские услуги", "amount": 1, "comment": "комиссия"}}
  ]
}}

Пример 3:
"Транзакция со счетом Каспий, единовременный расход 1000 тенге запчасти вода"

Результат:
{{
  "type": "multiple_expenses",
  "account": "каспий",
  "transactions": [
    {{"category": "единовременный расход", "amount": 1000, "comment": "запчасти вода"}}
  ]
}}

ПРАВИЛА ПАРСИНГА:

1. **Счёт (account)**: Указывается ОДИН РАЗ в начале ("со счёта..."), применяется ко всем транзакциям
   - Варианты: "оставил в кассе", "закуп", "касса", "касипай", "каспий", "wolt", "халык", "форте"
   - Если не указан, используй "оставил в кассе"

2. **Транзакции (transactions)**: Каждая состоит из:
   - category: ключевое слово категории (кассир, донерщик, повара, кухрабочая, курьер, логистика, маркетинг, банковские услуги, и т.д.)
   - amount: число в тенге
   - comment: всё что идёт после суммы до следующей категории

3. **Разделение транзакций**: По ключевым словам категорий
   - Новая категория = новая транзакция
   - Пример: "кассир 10500 Мадина, кассир 9000 Вика" = 2 транзакции

4. **Комментарии**:
   - Обычно имя сотрудника
   - Может быть описание ("Заготовка Асия", "помощник донерщика", "реклама", "Караганда")
   - Всё что между суммой и следующей категорией

ВАЖНЫЕ КАТЕГОРИИ:
- кассир, кассиры → "кассир"
- донер, донерщик → "донерщик"
- повар, повара → "повара"
- курьер, курьеры → "курьер"
- кухрабочая, мойщица → "кухрабочая"
- логистика, доставка → "логистика"
- маркетинг, реклама → "маркетинг"
- банковские услуги, комиссия, банк → "банковские услуги"
- единовременный расход, разовый расход → "единовременный расход"
- мыломойка, мойка → "мыломойка"
- упаковки, расходники → "упаковки"
- аренда → "аренда"
- коммунальные, коммуналка, платежи → "коммунальные"
- улучшения, апгрейд, ремонт → "улучшения"

ГИБКОСТЬ ФОРМАТА:
- Игнорируй служебные слова: "тенге", "комментарий", "комментарии"
- Если видишь "1000 тенге" - бери сумму 1000
- Комментарий - это всё что после суммы (кроме новой категории)

ВАЖНО: отвечай ТОЛЬКО валидным JSON, без дополнительного текста!

Текст для парсинга:
{text}"""


SUPPLY_PARSER_PROMPT = """Ты — помощник для парсинга поставок товаров из голосовых сообщений на русском языке.

Твоя задача: извлечь структурированные данные о поставке и вернуть JSON.

ФОРМАТ СООБЩЕНИЯ:
"Поставка <Поставщик> [счёт <Счёт>] <Ингредиент1> <Количество1> <по/за> <Цена/Сумма> <Ингредиент2> <Количество2> <по/за> <Цена/Сумма> ..."

ПРИМЕРЫ:
1. "Поставка Кюрдамир айсберг 4.4 по 1450"
   → {{"name": "айсберг", "qty": 4.4, "price": 1450}}

2. "Поставка Метро фри 10 упаковок по 2.5 кг, цена 3350 за упаковку"
   → {{"name": "фри", "qty": 25.0, "price": 1340}}
   (qty = 10 × 2.5 = 25, price = 3350 ÷ 2.5 = 1340)

3. "Поставка Янс огурцы 25 кг, сумма 33500"
   → {{"name": "огурцы", "qty": 25.0, "price": 1340}}
   (price = 33500 ÷ 25 = 1340)

4. "Поставка Адам помидоры 10.5 минус 0.5 по 1800"
   → {{"name": "помидоры", "qty": 10.0, "price": 1800}}
   (qty = 10.5 - 0.5 = 10.0)

АРИФМЕТИКА В КОЛИЧЕСТВЕ:
- "10.5 минус 0.5" = 10.0
- "10.8 плюс 10.2" = 21.0
- "15 упаковок по 2.5" = 15 * 2.5 = 37.5
- "10 на 2.5" = 10 * 2.5 = 25.0

КЛЮЧЕВЫЕ СЛОВА:
- "по", "цена", "стоимость" → ЦЕНА ЗА ЕДИНИЦУ (не сумма!)
- "за", "сумма", "общее", "всего", "за все", "итого" → ОБЩАЯ СУММА

ВАЖНО! ЛОГИКА РАСЧЁТОВ:

1. "N шт/кг по Y" - формат "ПО" означает ЦЕНА ЗА ЕДИНИЦУ:
   - qty = N (количество)
   - price = Y (цена за ОДНУ единицу, НЕ за все!)
   - Примеры:
     * "4шт по 3520" → qty=4, price=3520 (цена за 1 шт = 3520₸)
     * "25 кг по 1340" → qty=25, price=1340 (цена за 1 кг = 1340₸)
     * "10 упак по 5000" → qty=10, price=5000 (цена за 1 упак = 5000₸)
   - ЗАПОМНИ: "X по Y" = X единиц, каждая стоит Y (НЕ все вместе!)

2. "N кг, сумма Y" или "N кг за Y" - формат "ЗА/СУММА" означает ОБЩАЯ СУММА:
   - qty = N (количество)
   - price = Y ÷ N (цена за единицу)
   - Пример: "25 кг, сумма 33500"
     → qty = 25 кг
     → price = 33500 ÷ 25 = 1340 тенге за кг

3. "N упаковок по X кг, цена Y за упаковку":
   - qty = N × X (общий вес)
   - price = Y ÷ X (цена за 1 кг)
   - Пример: "10 упаковок по 2.5 кг, цена 3350 за упаковку"
     → qty = 10 × 2.5 = 25 кг
     → price = 3350 ÷ 2.5 = 1340 тенге за кг

ВСЕГДА ВОЗВРАЩАЙ price = ЦЕНА ЗА 1 ЕДИНИЦУ (кг/шт), а не за упаковку!

СТРУКТУРА JSON ОТВЕТА:
{{
  "type": "supply",
  "supplier": "<название поставщика>",
  "account": "<счёт: касипай|оставил в кассе|null для дефолтного>",
  "items": [
    {{
      "name": "<название ингредиента>",
      "qty": <число кг/штук после всех вычислений>,
      "price": <цена за единицу в тенге>
    }}
  ]
}}

ПРАВИЛА:
1. supplier - название поставщика после слова "Поставка"
2. account - счёт после "счёт/счёт", null если не указан
3. items - массив ингредиентов
4. qty - ВСЕГДА финальное количество после всех арифметических операций
5. price - ВСЕГДА цена за 1 единицу (не сумма!)
6. Обрабатывай числа с пробелами: "18 500" = 18500, "10.5" = 10.5
7. Вычисляй арифметику: минус, плюс, умножить, на, по (в контексте упаковок)

ВАЖНО: отвечай ТОЛЬКО валидным JSON, без дополнительного текста!

Текст для парсинга:
{text}"""


INVOICE_PARSER_PROMPT = """Ты — эксперт по распознаванию накладных поставок товаров с фотографий и PDF.

Твоя задача: ТЩАТЕЛЬНО извлечь ВСЕ структурированные данные из накладной и вернуть JSON.

ВАЖНО ДЛЯ ТОЧНОСТИ OCR:
- Внимательно смотри на каждую строку таблицы
- Распознавай ВСЕ позиции, даже если текст нечёткий
- Если какие-то цифры неразборчивы, попробуй определить их по контексту
- Если не уверен в символе, выбери наиболее вероятный вариант
- НИКОГДА не пропускай позиции из-за нечёткости - лучше попробовать распознать

ЧТО НУЖНО НАЙТИ НА НАКЛАДНОЙ:

1. **Поставщик**: Название компании-поставщика (обычно в верхней части накладной)
   - Может быть написано как "ТОО", "ИП", "Фирма", или просто название
   - Примеры: "Фирма ИртышИнтерФуд", "ТОО Метро", "Кюрдамир"

2. **Таблица с позициями**: Ищи таблицу со столбцами (названия могут отличаться):
   - Наименование товара / Товар / Название
   - Количество / Кол-во / Вес
   - Цена / Цена за ед. / Стоимость
   - Сумма / Итого (извлеки как "sum". КРИТИЧЕСКИ ВАЖНО: Поле 'sum' должно содержать ИМЕННО то число, которое фактически напечатано в колонке 'Сумма' (или 'Итого') на изображении накладной. Ни в коем случае НЕ вычисляй его самостоятельно как qty * price! Если на картинке написано Кол-во: 8, Цена: 1820, Сумма: 14560, а ты ошибочно распознал Кол-во как 1, то ты всё равно должен вернуть 'qty': 1, 'price': 1820, 'sum': 14560. НЕ пиши 'sum': 1820!)

3. **ЕСЛИ ЭТО РУКОПИСНЫЙ ТЕКСТ (НАПИСАНО РУЧКОЙ)**:
   - Часто накладная написана от руки.
   - Формат чаще всего такой: "[Название] [Вес/Кол-во] х [Цена за единицу] = [Сумма]"
   - Например: "помидор 18,1 х 1100 = 19910" или "огурцы 7,6 х 900 = 6840" или "айсберг 5,2 х 1300 = 6760"
   - Правила для рукописных строк:
     1. name: слова до первой цифры ("помидор", "огурцы", "айсберг")
     2. qty: первая группа цифр до знака "х" (например 18.1, 7.6)
     3. price: группа цифр после знака "х" до знака "=" (например 1100, 900)
     4. Извлеки сумму после знака "=" как "sum".

4. **Единицы измерения**: кг, шт, л, упак

ВАЖНЫЕ ПРАВИЛА:

1. **АВТОМАТИЧЕСКАЯ КОНВЕРТАЦИЯ ФАСОВКИ (ОЧЕНЬ ВАЖНО)**:
   - В Poster учет ведется в базовых единицах (кг, литры). Часто в накладных пишут штуки/упаковки, а вес указан в названии.
   - Если в названии товара явно указан вес или объем одной упаковки (например, "2,5 кг", "4.1 кг", "1.2кг", "5 л", "2 кг/я"), а в колонке количества указаны штуки/упаковки (например 1 шт, 5 шт), ты **ОБЯЗАН** пересчитать количество и цену!
   - Правило: `Новое кол-во = (Количество из накладной) * (Вес/Объем одной упаковки)`. 
   - Правило: `Новая цена = (Общая сумма за позицию) / (Новое кол-во)`.
   - ПРИМЕР 1: "Картофельные дольки 2,5 кг", кол-во из накладной: 1, цена: 4000 (сумма 4000). 
     Твой JSON: "qty": 2.5, "price": 1600.
   - ПРИМЕР 2: "Пицца соус CHEF GOURMET 4,1 кг", кол-во: 2, цена: 6600 (сумма 13200). 
     Твой JSON: "qty": 8.2 (т.к. 2 * 4.1), "price": 1609.76.
   - ПРИМЕР 3: "Наггетсы 2 кг/я 12 кг*6шт", кол-во: 5, цена 4900 (сумма 24500). Вес одной пачки — 2 кг. 
     Твой JSON: "qty": 10.0 (т.к. 5 * 2), "price": 2450.
   - Если товар уже указан в кг или литрах в самой колонке количества (например "вес: 2760.00"), или не имеет привязки к весу, оставляй как есть.

2. **Точность и форматирование**:
   - Убери пробелы из чисел: "2 760.00" → 2760.00
   - Округляй цену максимум до 2 знаков после запятой.

3. **Название товара**: Бери полное название из накладной как есть
   - Примеры: "Филе ЦБ, групп, охл", "Крыло ЦБ, групп, охл"
   - НЕ пытайся сокращать или переименовывать

4. **Игнорируй лишние столбцы**:
   - Артикулы, коды товаров
   - НДС, налоги
   - Сумма без НДС, с НДС
   - Нас интересуют только: название, количество, цена за единицу, и общая сумма по строке ("sum")

5. **Если текст нечёткий**:
   - ВСЁ РАВНО попытайся распознать (сделай лучшее предположение)
   - Добавь в "notes" информацию о проблемных позициях
   - Только если СОВСЕМ невозможно прочитать - добавь в unrecognized_items
   - Цель: распознать МАКСИМУМ позиций, даже если не на 100% уверен

СТРУКТУРА JSON ОТВЕТА:

{{
  "type": "supply",
  "supplier": "<название поставщика из накладной>",
  "account": null,
  "items": [
    {{
      "name": "<полное название товара из накладной>",
      "qty": <количество числом>,
      "price": <цена за единицу числом>,
      "sum": <общая сумма по строке (qty * price) из накладной>
    }}
  ],
  "unrecognized_items": [
    "<названия позиций, которые не смог распознать>"
  ],
  "total": <общая сумма накладной, если есть>,
  "notes": "<любые важные замечания о качестве распознавания>"
}}

ПРИМЕРЫ:

Пример 1 (накладная от "Фирма ИртышИнтерФуд"):
- Товар 1: Филе ЦБ, групп, охл | 2760.00 кг | 2160.00 тг/кг | Сумма: 5961600.00
- Товар 2: Крыло ЦБ, групп, охл | 567.30 кг | 502.30 тг/кг | Сумма: 284954.79

Результат:
{{
  "type": "supply",
  "supplier": "Фирма ИртышИнтерФуд",
  "account": null,
  "items": [
    {{"name": "Филе ЦБ, групп, охл", "qty": 2760.00, "price": 2160.00, "sum": 5961600.00}},
    {{"name": "Крыло ЦБ, групп, охл", "qty": 567.30, "price": 502.30, "sum": 284954.79}}
  ],
  "unrecognized_items": [],
  "total": null,
  "notes": "Накладная распознана полностью"
}}

ВАЖНО: отвечай ТОЛЬКО валидным JSON, без дополнительного текста!"""


UNIFIED_BATCH_PARSER_PROMPT = """Ты — интеллектуальный помощник по автоматизации бухгалтерии сети ресторанов PizzBurg.
Перед тобой изображение документа: это может быть рукописный лист кассира (список различных расходов), скриншот перевода, или печатная накладная от поставщика (перечень закупаемых продуктов).

Твоя задача — проанализировать изображение, классифицировать тип документа и извлечь все данные в структурированный JSON.

ШАГ 1. Определи тип документа ("document_type"):
1. "cashier_sheet" — если это список расходов кассира за смену (рукописный лист с разными тратами: зарплаты курьерам/поварам/кассирам, такси, хозтовары, разовые мелкие закупы продуктов).
2. "printed_invoice" — если это накладная на поставку (печатная таблица или рукописный список от одного поставщика), содержащая перечень товаров от одного конкретного контрагента (например: ТОО Метро, Кюрдамир, ТОО Алель, фарш и т.д.).

ШАГ 2. Выполни извлечение данных в зависимости от типа:

А. Если это "cashier_sheet":
Извлеки все строки расходов в массив "expenses". Для каждого расхода заполни:
- amount: сумма в тенге (число, всегда положительное)
- description: оригинальное описание расхода (имя сотрудника или суть покупки)
- type: тип расхода ("transaction" для зарплат, такси, хозтоваров, аренды; "supply" для покупки продуктов питания, например: фарш, сыр, помидоры, мясо, молоко, кюрдамир, хлеб, овощи, small, смолл)
- category: примерная категория ("Зарплаты", "Хозтовары", "Транспорт", "Прочее")
- is_income: (булево) true если перед суммой явно стоит знак "+" (например "+16470 видринк") или это явно приход/возврат денег в кассу. По умолчанию false.
- items: если в строке расхода прямо указаны детали количества и цены (например "Фарш 12кг по 2800"), выдели их в массив:
  [{"name": "<название>", "qty": <кол-во>, "price": <цена за единицу>, "sum": <сумма>}]

*ОЧЕНЬ ВАЖНО ДЛЯ cashier_sheet:*
1. Лист кассира может быть разделён вертикальной линией или организован в несколько колонок (например, левая колонка — расходы из оставленных денег, правая колонка — расходы из выручки, или наоборот). ТЫ ДОЛЖЕН прочитать и извлечь строки расходов из ВСЕХ колонок! Не пропускай ни одну колонку.
2. Игнорируй промежуточные итоговые суммы колонок (число без описания, написанное сверху/снизу колонки, например "111040"), но обязательно извлекай каждую детальную строку (например "46000 - фарш", "10000 - обед бандели").
3. Имена вроде "Макс курьер", "Заготовщица", "Алёна", "Батима", "Бека" — это зарплаты (type="transaction", category="Зарплаты").
4. Названия вроде "Мерей", "Сарыарка", "Кюрдемир", "Мыламойка", "Small/Смолл", "фарш", "овощи" — это закупки продуктов (type="supply").

Б. Если это "printed_invoice":
Извлеки данные накладной в объект "invoice":
- supplier: название поставщика (ТОО, ИП или бренд, например: Метро, Алель, Кюрдамир)
- total_sum: общая сумма накладной (число, если есть)
- items: массив всех позиций товаров:
  - name: полное наименование товара
  - qty: количество (число)
  - price: цена за единицу (число)
  - sum: общая сумма по строке (число). КРИТИЧЕСКИ ВАЖНО: извлекай ИМЕННО то число, которое напечатано/написано в колонке 'Сумма' на документе. Ни в коем случае НЕ вычисляй его самостоятельно как qty * price!
  *ВАЖНО:* Пересчитывай фасовки! Если в названии указан вес упаковки (например "Фри 2.5кг"), а в количестве штуки (например 2 шт) по цене 4000 за шт, пересчитай в базовые единицы: qty=5.0, price=1600.0.

ФОРМАТ JSON ОТВЕТА:
{
  "document_type": "cashier_sheet" | "printed_invoice",
  
  // Заполняется только для document_type = "cashier_sheet"
  "expenses": [
    {
      "amount": 12000,
      "description": "Мадина кассир",
      "type": "transaction",
      "category": "Зарплаты",
      "is_income": false
    },
    {
      "amount": 16470,
      "description": "видринк",
      "type": "transaction",
      "category": "Прочее",
      "is_income": true
    },
    {
      "amount": 46000,
      "description": "Фарш 10 кг",
      "type": "supply",
      "category": "Прочее",
      "is_income": false,
      "items": [{"name": "Фарш", "qty": 10.0, "price": 4600.0, "sum": 46000.0}]
    }
  ],
  
  // Заполняется только для document_type = "printed_invoice"
  "invoice": {
    "supplier": "Название поставщика",
    "total_sum": 25400,
    "items": [
      {"name": "Фри дольки", "qty": 10.0, "price": 1200.0, "sum": 12000.0},
      {"name": "Сыр Моцарелла", "qty": 5.0, "price": 2680.0, "sum": 13400.0}
    ]
  }
}

ВАЖНО:
- Распознавай ВСЕ строки, даже нечёткие.
- Возвращай исключительно валидный JSON без постороннего текста и без markdown разметки.
"""


ASSISTANT_SYSTEM_PROMPT = """Ты — интеллектуальный помощник-бухгалтер сети ресторанов PizzBurg.
Ты ведешь переписку с владельцем в чате. Твоя цель — помогать фиксировать расходы и поставки за день.

У тебя есть доступ к:
1. Текущему списку черновиков (расходов и поставок) за выбранный день.
2. Профилям ингредиентов поставщиков (история за последние 2 месяца). Используй их, чтобы автоматически определять поставщика по упоминаемым ингредиентам:
   - Если упоминаются коробки пиццы, фри, наггетсы, нори, угорь -> это Япоша.
   - Если упоминаются огурцы, помидоры, салат айсберг -> это Алимжан.
   - Если лаваш -> Лаваш Астана.
   - Если айран -> Айран Астана.
   - Если фарш -> Фарш Market.
   - Если донерное мясо или мясо -> Денер Караганда.
   - Если грибы, зелень, перец -> Кюрдамир.
   Используй и другие профили из переданного списка.
3. Возможности находить и удалять чеки (заказы) из Poster POS за текущий бизнес-день.
4. Блокноту памяти (правила и нюансы работы с поставщиками/расходами, которые ты должен помнить всегда). Всегда строго следуй правилам из этого блокнота!

Твои действия:
1. Анализируй сообщения пользователя и загруженные изображения.
2. Если пользователь прислал накладную/чек или фото весов, и написал комментарий (например, "огурцы 8.4 кг" под фото весов) или если на картинке все очевидно:
   - Попробуй найти существующий черновик расхода типа 'supply' с такой же суммой. Если нашел, ты можешь добавить товары прямо в эту поставку (действие add_supply_items).
   - Если подходящего расхода нет, создай новый черновик расхода с типом 'supply' (действие create_supply).
   - Если это простая трата (зарплата, такси), создай черновик расхода с типом 'transaction' (действие create_expense).
3. Если пользователь уточняет значение (например, "Мадина вчера 15000" вместо 1), найди нужный черновик и обнови его (действие update_expense).
4. Если информации недостаточно (например, прислано фото весов, но нет цены или непонятно чей расход), НЕ выполняй поспешных действий. Напиши вежливый уточняющий вопрос в поле response_text, спросив нужные детали.
5. Если пользователь просит найти или удалить чек из кассы/Poster (например, "удали чек на 5000", "найди чек", или присылает фото чека с просьбой удалить), сгенерируй действие `find_pos_receipt`. При извлечении параметров из фото чека:
    - **Расчет суммы (`amount`)**: Обязательно учитывай оплату бонусами/скидками. Если на чеке есть строка «Личная интеграция» (или другие бонусные/скидочные списания), суммируй её с суммой «К оплате», чтобы получить полную итоговую сумму заказа (равную сумме цен всех позиций чека, например, К оплате 6500 + Личная интеграция 2550 = 9050 тг). Именно эту полную сумму нужно передавать в `amount` для поиска в Poster.
    - **Определение заведения**: 
      * Если на чеке написано `PizzBurg` (или логотип PizzBurg) — этот чек относится к аккаунту `Pizzburg`.
      * Если написано `SunDay` (или логотип SunDay) — этот чек относится к аккаунту `Pizzburg-cafe` (SunDay).
      * Использовать эту информацию для отсеивания неподходящих кандидатов из результатов поиска.
    - **Сверка состава и времени**: При наличии нескольких кандидатов сравнивай их состав (позиции и количество) с позициями на фото чека (если состав совпадает — чек верный на 100%), сверяй время печати на чеке с временем закрытия транзакции в Poster, а для доставки — имя, телефон и адрес клиента. Предложи пользователю единственный верный чек и объясни свой выбор.
6. Если в истории переписки ты спросил: "Точно он? Удаляем?" для чека, содержащего в метаданных ID чека и ID аккаунта (например, `[Метаданные: ID чека: 12345, ID аккаунта: 987654]`), и пользователь ответил утвердительно (например, "Да", "Удаляй", "Точно он"), ты должен сгенерировать действие `delete_pos_receipt` с этим `transaction_id` (12345) и `poster_account_id` (987654).
7. Если пользователь просит тебя запомнить какое-то правило, нюанс или привычку, либо если ты сам обнаружил важную закономерность, ДОБАВЬ правило в блокнот памяти (действие `add_to_memory`). В поле `rule_text` укажи ТОЛЬКО НОВОЕ правило (одно или несколько пунктов в формате Markdown). Бэкенд сам добавит его к существующей памяти — тебе НЕ нужно копировать старые правила. Ты НЕ МОЖЕШЬ удалять или изменять существующие правила — только добавлять новые. Удалением правил управляет только пользователь через интерфейс.

=== РУКОПИСНЫЕ ЛИСТЫ РАСХОДОВ (ФОТО БЛОКНОТА / ЛИСТА КАССИРА) ===
Если на фото рукописный список расходов (несколько строк вида «название/имя — сумма»):
1. Извлеки КАЖДУЮ строку, ничего не пропускай. Лист может быть разделён на колонки вертикальной чертой — читай ВСЕ колонки.
2. Для КАЖДОЙ строки сгенерируй отдельное действие create_expense:
   - amount: сумма в тенге (число, положительное)
   - description: оригинальный текст строки (имя сотрудника или суть покупки)
   - expense_type: "transaction" для зарплат, такси, хозтоваров, аренды, услуг; "supply" для закупа продуктов питания (фарш, овощи, сыр, хлеб, мясо, лаваш, кюрдамир, смолл и т.п.)
   - category: подбери осмысленную категорию ("Зарплаты", "Хозтовары", "Транспорт", "Прочее" и т.д.)
   - source: "cash", если из текста/контекста не следует иное (Kaspi-переводы → "kaspi")
   - is_income: true только если перед суммой стоит знак «+» или это явный возврат/приход денег
   - items: (только для expense_type="supply") если в строке указаны количество и цена за единицу (например «Фарш 12кг по 2800» или «фарш 12 х 2800 = 33600»), добавь массив items: [{"name": "Фарш", "qty": 12.0, "price": 2800.0, "sum": 33600.0}]. Это позволит автоматически привязать ингредиенты к базе Poster.
3. Имена людей (например «Алёна», «Бека», «Макс курьер», «Заготовщица») — это зарплаты: expense_type="transaction", category="Зарплаты".
4. Игнорируй промежуточные итоги колонок (число без описания сверху/снизу) — извлекай только детальные строки.
5. Если строка совсем нечитаема — упомяни её в response_text, но не выдумывай сумму.

=== PDF-НАКЛАДНЫЕ ===
Если в сообщении есть блок «Извлечённый текст из PDF» — это ТОЧНЫЙ текстовый слой электронной накладной. Доверяй этим цифрам на 100%, бери количества/цены/суммы ИМЕННО оттуда, а не с изображения. Извлеки все табличные позиции до единой.

=== ОБЯЗАТЕЛЬНАЯ ПРОВЕРКА МАТЕМАТИКИ ===
Перед генерацией create_supply или add_supply_items проверь КАЖДУЮ позицию: qty * price должно равняться sum (допуск ±1₸).
- Если не сходится — сначала проверь, не потеряна ли запятая в количестве (OCR часто читает 8,4 как 84) или в цене.
- Ориентируйся на сумму строки (sum) как на истину: qty = sum / price.
- Используй профили цен поставщиков и блокнот памяти: если цена позиции в 10 раз отличается от привычной — это OCR-ошибка, исправь её.
- total_sum поставки должен равняться сумме всех sum позиций.

Формат твоего ответа должен быть строго в JSON:
{
  "response_text": "Ответ пользователю на русском языке. Опиши кратко, что ты сделал или задай вопрос.",
  "actions": [
     {
       "action": "create_expense",
       "amount": 15000,
       "description": "Мадина аванс",
       "expense_type": "transaction",
       "category": "Зарплаты",
       "source": "cash",
       "is_income": false
     },
     {
       "action": "create_expense",
       "amount": 46000,
       "description": "Фарш 10 кг по 4600",
       "expense_type": "supply",
       "category": "Прочее",
       "source": "cash",
       "is_income": false,
       "items": [{"name": "Фарш", "qty": 10.0, "price": 4600.0, "sum": 46000.0}]
     },
     {
       "action": "update_expense",
       "id": 123,
       "amount": 15000,
       "description": "Новое описание",
       "category": "Новая категория"
     },
     {
       "action": "add_supply_items",
       "expense_draft_id": 456,
       "items": [
          {"name": "Мясо", "qty": 8.4, "price": 3200.0, "sum": 26880.0}
       ]
     },
     {
       "action": "create_supply",
       "supplier_name": "Денер Караганда",
       "total_sum": 26880.0,
       "source": "cash",
       "items": [
          {"name": "Мясо", "qty": 8.4, "price": 3200.0, "sum": 26880.0}
       ]
     },
     {
       "action": "find_pos_receipt",
       "amount": 5000.0,
       "order_number": "12345"
     },
     {
       "action": "delete_pos_receipt",
       "transaction_id": 12345,
       "poster_account_id": 987654
     },
     {
       "action": "add_to_memory",
       "rule_text": "* **Наггетсы (поставщик Япоша):** Всегда переводить упаковки в килограммы (например: 4 кг по 2450 ₸ вместо 2 упаковок по 4900 ₸)."
     }
  ]
}
"""

class ParserService:
    """Service for parsing text using Claude API and OpenAI Vision"""

    def __init__(self):
        self.client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None
        self.openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

    async def _call_gemini_api(
        self,
        parts: List[Dict],
        response_mime_type: str = "application/json",
        timeout_seconds: int = 120
    ) -> str:
        """
        Helper method to call Gemini API with retries (up to 10 attempts, 60s delay).
        """
        if not GEMINI_API_KEY:
            raise Exception("GEMINI_API_KEY is not configured!")

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
        headers = {"Content-Type": "application/json"}
        
        payload = {
            "contents": [{"parts": parts}],
            "generationConfig": {
                "responseMimeType": response_mime_type
            }
        }

        import asyncio
        import re
        max_attempts = 10

        for attempt in range(1, max_attempts + 1):
            logger.info(f"🤖 [Gemini API] Request attempt {attempt}/{max_attempts} using model {GEMINI_MODEL}...")
            delay_seconds = 10  # default fallback delay for this attempt
            try:
                timeout = aiohttp.ClientTimeout(total=timeout_seconds)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(url, json=payload, headers=headers) as resp:
                        if resp.status == 200:
                            result = await resp.json()
                            response_text = result['candidates'][0]['content']['parts'][0]['text'].strip()
                            return response_text
                        elif resp.status == 429 or resp.status >= 500:
                            error_text = await resp.text()
                            logger.warning(f"Gemini API returned retryable status {resp.status} (attempt {attempt}/{max_attempts}): {error_text[:500]}")
                            if resp.status == 429:
                                match = re.search(r"Please retry in (\d+(?:\.\d+)?)s", error_text)
                                if match:
                                    try:
                                        delay_seconds = max(1.0, min(float(match.group(1)) + 1.0, 60.0))
                                    except Exception:
                                        pass
                            else:
                                delay_seconds = 15  # wait 15s for 5xx errors
                        else:
                            error_text = await resp.text()
                            logger.error(f"Gemini API returned non-retryable status {resp.status}: {error_text[:500]}")
                            raise Exception(f"Gemini API error status {resp.status}: {error_text[:200]}")
            except Exception as e:
                if "Gemini API error status" in str(e):
                    raise e
                logger.warning(f"Gemini API call failed on attempt {attempt}/{max_attempts} ({type(e).__name__}): {e}")
                delay_seconds = 5
            
            if attempt < max_attempts:
                logger.info(f"Waiting {delay_seconds:.2f} seconds before next Gemini API attempt...")
                await asyncio.sleep(delay_seconds)

        raise Exception(f"Не удалось выполнить запрос к ИИ. Попробовали {max_attempts} раз с моделью {GEMINI_MODEL}, но ИИ временно недоступен.")

    async def parse_transaction(self, text: str) -> Optional[Dict]:
        """
        Parse transaction data from text using Gemini

        Args:
            text: Input text (from voice or manual input)

        Returns:
            Parsed transaction dict or None if parsing failed
        """
        try:
            # Check if it's a supply first
            if 'поставк' in text.lower():
                logger.info("Detected supply keyword, using supply parser")
                return await self.parse_supply(text)

            # Check if it's multiple transactions (has comma-separated pattern)
            # Indicators: mentions account + multiple categories with numbers
            text_lower = text.lower()
            has_account_mention = any(word in text_lower for word in ['со счёта', 'со счета', 'счет', 'счёт'])
            has_multiple_commas = text.count(',') >= 2

            if has_account_mention and has_multiple_commas:
                logger.info("Detected multiple transactions pattern, using multiple parser")
                return await self.parse_multiple_transactions(text)

            logger.info(f"Parsing single transaction text: '{text}'")

            prompt = TRANSACTION_PARSER_PROMPT.format(text=text)

            parts = [{"text": prompt}]
            response_text = await self._call_gemini_api(parts)

            # Try to extract JSON from response
            json_text = self._extract_json(response_text)
            parsed = json.loads(json_text)
            logger.info(f"✅ Transaction parsed successfully: {parsed}")

            # Validate required fields
            if not parsed.get("amount") or not parsed.get("category"):
                logger.warning("Parsed data missing required fields")
                return None

            return parsed

        except Exception as e:
            logger.error(f"Transaction parsing failed: {e}")
            return None

    async def parse_supply(self, text: str) -> Optional[Dict]:
        """
        Parse supply data from text using Gemini

        Args:
            text: Input text (from voice or manual input)

        Returns:
            Parsed supply dict or None if parsing failed
        """
        try:
            logger.info(f"Parsing supply text: '{text}'")

            prompt = SUPPLY_PARSER_PROMPT.format(text=text)

            parts = [{"text": prompt}]
            response_text = await self._call_gemini_api(parts)

            # Extract JSON from response
            json_text = self._extract_json(response_text)
            parsed = json.loads(json_text)
            logger.info(f"✅ Supply parsed successfully: {parsed}")

            # Validate required fields
            if not parsed.get("supplier") or not parsed.get("items"):
                logger.warning("Parsed supply missing required fields")
                return None

            return parsed

        except Exception as e:
            logger.error(f"Supply parsing failed: {e}")
            return None

    async def parse_multiple_transactions(self, text: str) -> Optional[Dict]:
        """
        Parse multiple transactions from text using Gemini

        Args:
            text: Input text with multiple transactions

        Returns:
            Parsed dict with type='multiple_expenses' and list of transactions
        """
        try:
            logger.info(f"Parsing multiple transactions: '{text}'")

            prompt = MULTIPLE_TRANSACTIONS_PARSER_PROMPT.format(text=text)

            parts = [{"text": prompt}]
            response_text = await self._call_gemini_api(parts)

            # Try to extract JSON from response
            json_text = self._extract_json(response_text)
            parsed = json.loads(json_text)
            logger.info(f"✅ Multiple transactions parsed successfully: {parsed}")

            # Validate required fields
            if not parsed.get("transactions") or not isinstance(parsed["transactions"], list):
                logger.warning("Parsed data missing transactions list")
                return None

            return parsed

        except Exception as e:
            logger.error(f"Multiple transactions parsing failed: {e}")
            return None

    async def parse_invoice_image(self, image_data: bytes, media_type: str = "image/jpeg") -> Optional[Dict]:
        """
        Parse supply invoice from image or PDF using Claude Vision API

        Args:
            image_data: Image or PDF bytes
            media_type: MIME type (image/jpeg, image/png, or application/pdf)

        Returns:
            Parsed supply dict or None if parsing failed
        """
        return await self._parse_invoice_with_vision(image_data, media_type)

    async def _parse_invoice_with_vision(self, file_data: bytes, media_type: str) -> Optional[Dict]:
        """
        Parse supply invoice from image using Gemini Vision API
        
        Args:
            file_data: Image bytes
            media_type: MIME type (image/jpeg, image/png, etc)
            
        Returns:
            Parsed supply dict or None if parsing failed
        """
        try:
            file_base64 = base64.standard_b64encode(file_data).decode("utf-8")
            if media_type not in ["image/jpeg", "image/png", "image/gif", "image/webp"]:
                media_type = "image/jpeg"
                
            parts = [
                {"text": INVOICE_PARSER_PROMPT},
                {"inlineData": {"mimeType": media_type, "data": file_base64}}
            ]
            
            response_text = await self._call_gemini_api(parts)
            json_text = self._extract_json(response_text)
            parsed = json.loads(json_text)
            logger.info(f"✅ Invoice parsed successfully with Gemini. Items: {len(parsed.get('items', []))}")
            return self._reconcile_invoice_items(parsed)

        except Exception as e:
            logger.error(f"Gemini Vision invoice parsing failed: {e}")
            raise Exception(f"Gemini API Error: {str(e)}")

    async def parse_batch_image(self, file_data: bytes, media_type: str = "image/jpeg") -> Optional[Dict]:
        """
        Parse image using Gemini API to classify it (cashier_sheet or printed_invoice) 
        and extract structured expenses / supply items.
        """
        try:
            file_base64 = base64.standard_b64encode(file_data).decode("utf-8")
            if media_type not in ["image/jpeg", "image/png", "image/gif", "image/webp"]:
                media_type = "image/jpeg"

            parts = [
                {
                    "text": UNIFIED_BATCH_PARSER_PROMPT
                },
                {
                    "inlineData": {
                        "mimeType": media_type,
                        "data": file_base64
                    }
                }
            ]

            response_text = await self._call_gemini_api(parts)
            json_text = self._extract_json(response_text)
            parsed = json.loads(json_text)
            logger.info(f"✅ Batch image parsed successfully with Gemini. Type: {parsed.get('document_type')}")
            return self._reconcile_invoice_items(parsed)

        except Exception as e:
            logger.error(f"Failed to parse batch image: {e}")
            raise Exception(f"Vision parsing error: {str(e)}")

    async def parse_batch_text(self, text: str) -> Optional[Dict]:
        """
        Parse text using Gemini API to classify it (cashier_sheet or printed_invoice) 
        and extract structured expenses / supply items.
        """
        prompt = f"""Ты — интеллектуальный помощник по автоматизации бухгалтерии сети ресторанов PizzBurg.
Перед тобой текстовые данные: это может быть список расходов кассира за смену (рукописный лист с разными тратами: зарплаты курьерам/поварам/кассирам, такси, хозтовары, разовые мелкие закупы продуктов), скопированный текст выписки банка (например Kaspi) или печатная накладная от поставщика.

Твоя задача — проанализировать текст, классифицировать тип документа и извлечь все данные в структурированный JSON.

ШАГ 1. Определи тип документа ("document_type"):
1. "cashier_sheet" — если это список расходов кассира за смену (различные трат: зарплаты, такси, мелкие закупки продуктов).
2. "printed_invoice" — если это перечень товаров от одного конкретного контрагента/поставщика.

ШАГ 2. Выполни извлечение данных в зависимости от типа:

А. Если это "cashier_sheet":
Извлеки все строки расходов в массив "expenses". Для каждого расхода заполни:
- amount: сумма в тенге (число)
- description: оригинальное описание расхода (имя сотрудника или суть покупки)
- type: тип расхода ("transaction" для зарплат, такси, хозтоваров, аренды; "supply" для покупки продуктов питания, например: фарш, сыр, помидоры, мясо)
- category: примерная категория ("Зарплаты", "Хозтовары", "Транспорт", "Прочее")
- items: если в строке с расходом прямо указаны детали (например, "Фарш 12кг по 2800" или "сыр 5кг х 2600" или "молоко 10л х 450"), выдели их в массив:
  [{{"name": "<название>", "qty": <кол-во>, "price": <цена за единицу>, "sum": <сумма по строке (qty * price)>}}]

Б. Если это "printed_invoice":
Извлеки данные накладной в объект "invoice":
- supplier: название поставщика (ТОО, ИП или бренд, например: Метро, Алель, Кюрдамир)
- total_sum: общая сумма накладной (число, если есть)
- items: массив всех позиций товаров:
  - name: полное наименование товара
  - qty: количество (число)
  - price: цена за единицу (число)
  - sum: общая сумма по строке (число). КРИТИЧЕСКИ ВАЖНО: извлекай ИМЕННО то число, которое написано в колонке 'Сумма' на документе. Ни в коем случае НЕ вычисляй его самостоятельно как qty * price!

ФОРМАТ JSON ОТВЕТА:
{{
  "document_type": "cashier_sheet" | "printed_invoice",
  
  // Заполняется только для document_type = "cashier_sheet"
  "expenses": [
    {{
      "amount": 12000,
      "description": "Мадина кассир",
      "type": "transaction",
      "category": "Зарплаты"
    }},
    {{
      "amount": 46000,
      "description": "Фарш 10 кг",
      "type": "supply",
      "category": "Прочее",
      "items": [{{"name": "Фарш", "qty": 10.0, "price": 4600.0, "sum": 46000.0}}]
    }}
  ],
  
  // Заполняется только для document_type = "printed_invoice"
  "invoice": {{
    "supplier": "Название поставщика",
    "total_sum": 25400,
    "items": [
      {{"name": "Фри дольки", "qty": 10.0, "price": 1200.0, "sum": 12000.0}},
      {{"name": "Сыр Моцарелла", "qty": 5.0, "price": 2680.0, "sum": 13400.0}}
    ]
  }}
}}

ВАЖНО:
- Распознавай ВСЕ строки.
- Возвращай исключительно валидный JSON без постороннего текста и без markdown разметки.

ВХОДНЫЕ ТЕКСТОВЫЕ ДАННЫЕ:
{text}
"""
        try:
            parts = [{"text": prompt}]
            response_text = await self._call_gemini_api(parts)
            json_text = self._extract_json(response_text)
            parsed = json.loads(json_text)
            logger.info(f"✅ Batch text parsed successfully with Gemini. Type: {parsed.get('document_type')}")
            return self._reconcile_invoice_items(parsed)

        except Exception as e:
            logger.error(f"Failed to parse batch text: {e}")
            raise Exception(f"Text parsing error: {str(e)}")

    def _reconcile_invoice_items(self, parsed: Optional[Dict]) -> Optional[Dict]:
        """
        Reconcile invoice items: verify and adjust qty, price, and total sum based on the row sums.
        """
        if not parsed:
            return parsed
            
        doc_type = parsed.get('document_type')
        if doc_type == 'printed_invoice' and 'invoice' in parsed:
            parsed['invoice'] = self._reconcile_invoice_data(parsed['invoice'])
        elif parsed.get('type') == 'supply':
            # This is from parse_invoice_image which returns {type: 'supply', items: [...]}
            parsed = self._reconcile_invoice_data(parsed)
            
        return parsed

    def reconcile_items(self, items: List[Dict]) -> List[Dict]:
        """Public helper: reconcile a bare list of invoice items (qty*price==sum).

        Used by the assistant action handlers before items are written to
        supply drafts, so OCR errors are fixed before the user ever sees them.
        """
        wrapper = self._reconcile_invoice_data({'items': items, 'total_sum': 0})
        return wrapper.get('items', items)

    def _reconcile_invoice_data(self, invoice: Dict) -> Dict:
        if not invoice or 'items' not in invoice:
            return invoice

        total_sum_calculated = 0.0

        for item in invoice.get('items', []):
            name = item.get('name', '')
            qty = item.get('qty')
            price = item.get('price')
            row_sum = item.get('sum')

            def _to_float(value):
                try:
                    return float(value) if value is not None else None
                except (ValueError, TypeError):
                    return None

            qty = _to_float(qty)
            price = _to_float(price)
            row_sum = _to_float(row_sum)

            if row_sum is not None and row_sum > 0:
                if qty is not None and price is not None and qty > 0 and price > 0:
                    expected_sum = qty * price
                    # Math check (with 1.0 margin of error)
                    if abs(expected_sum - row_sum) > 1.0:
                        logger.warning(f"Reconciliation mismatch for '{name}': {qty} * {price} = {expected_sum} vs actual sum {row_sum}")
                        corrected = False

                        # 0. Lost decimal point in qty or price (OCR reads 8.4 as 84):
                        # expected/actual ratio is then exactly ~10 or ~100
                        if row_sum > 0:
                            ratio = expected_sum / row_sum
                            for factor in (10.0, 100.0):
                                if abs(ratio - factor) / factor < 0.005:
                                    # one of qty/price is inflated by `factor`
                                    if qty / factor >= 0.01 and abs(qty * price / factor - row_sum) <= max(1.0, row_sum * 0.005):
                                        logger.info(f"Reconciliation: '{name}' qty {qty} → {qty / factor} (lost decimal point, x{factor:g})")
                                        qty = qty / factor
                                        corrected = True
                                        break
                                elif abs(1 / ratio - factor) / factor < 0.005:
                                    # qty or price is deflated by `factor`; prefer fixing qty
                                    logger.info(f"Reconciliation: '{name}' qty {qty} → {qty * factor} (extra decimal point, /{factor:g})")
                                    qty = qty * factor
                                    corrected = True
                                    break

                        # 0b. qty and price swapped by OCR (price*qty same product — skip;
                        # detect when sum matches price alone => qty misread as 1..N)
                        if not corrected:
                            # 1. Try to correct quantity (if price is correct)
                            # e.g., sum=14560, price=1820 -> new_qty=8
                            new_qty = row_sum / price
                            if abs(new_qty - round(new_qty)) < 0.01:
                                logger.info(f"Reconciliation: corrected qty of '{name}' from {qty} to {round(new_qty)} based on price {price} and sum {row_sum}")
                                qty = float(round(new_qty))
                            elif abs(new_qty * 10 - round(new_qty * 10)) < 0.05 and 0.05 <= new_qty <= 5000:
                                # qty with one decimal place (weights like 8.4 kg)
                                logger.info(f"Reconciliation: corrected qty of '{name}' from {qty} to {round(new_qty, 2)} based on price {price} and sum {row_sum}")
                                qty = round(new_qty, 2)
                            else:
                                # 2. Try to correct price
                                new_price = row_sum / qty
                                logger.info(f"Reconciliation: corrected price of '{name}' from {price} to {new_price} based on qty {qty} and sum {row_sum}")
                                price = new_price
                elif (qty is None or qty == 0) and price is not None and price > 0:
                    qty = row_sum / price
                    logger.info(f"Reconciliation: reconstructed qty of '{name}' as {qty} from sum {row_sum} and price {price}")
                elif (price is None or price == 0) and qty is not None and qty > 0:
                    price = row_sum / qty
                    logger.info(f"Reconciliation: reconstructed price of '{name}' as {price} from sum {row_sum} and qty {qty}")
            else:
                # Fallback: if sum is missing, compute it
                if qty is not None and price is not None:
                    row_sum = qty * price

            item['qty'] = qty
            item['price'] = price
            item['sum'] = row_sum

            if row_sum is not None:
                total_sum_calculated += row_sum
                
        # Reconcile total sum
        total_key = 'total_sum' if 'total_sum' in invoice else ('total' if 'total' in invoice else None)
        if not total_key:
            # If both are missing, determine which to add
            total_key = 'total' if 'type' in invoice else 'total_sum'
            
        try:
            total_sum = float(invoice.get(total_key, 0.0))
            if total_sum <= 0 and total_sum_calculated > 0:
                invoice[total_key] = total_sum_calculated
            elif abs(total_sum - total_sum_calculated) > 5.0 and total_sum_calculated > 0:
                logger.warning(f"Reconciliation: replacing {total_key} {total_sum} with calculated sum {total_sum_calculated}")
                invoice[total_key] = total_sum_calculated
        except (ValueError, TypeError):
            if total_sum_calculated > 0:
                invoice[total_key] = total_sum_calculated
                    
        return invoice

    async def call_gemini_assistant_agent(
        self,
        user_message: str,
        chat_history: List[Dict],
        active_drafts: List[Dict],
        supplier_profiles: Dict,
        media_files: Optional[List[Dict]] = None,
        assistant_memory: str = ""
    ) -> Optional[Dict]:
        """
        Call Gemini API to act as an assistant and return structured actions + text response.
        """
        if not GEMINI_API_KEY:
            logger.error("GEMINI_API_KEY not set!")
            return {"response_text": "Ошибка: Ключ API Gemini не настроен на сервере.", "actions": []}

        # Format history, drafts, and profiles for the prompt context
        history_formatted = []
        for msg in chat_history:
            sender_name = "Пользователь" if msg['sender'] == 'user' else "Ассистент"
            history_formatted.append(f"{sender_name}: {msg['message_text']}")
        history_str = "\n".join(history_formatted)

        drafts_formatted = []
        for d in active_drafts:
            drafts_formatted.append(
                f"- [ID: {d['id']}] {d.get('description', '')} на сумму {d.get('amount', 0)}₸ "
                f"(Тип: {d.get('expense_type', 'transaction')}, Списание: {d.get('source', 'cash')}, Категория: {d.get('category', 'Прочее')})"
            )
        drafts_str = "\n".join(drafts_formatted) if drafts_formatted else "Нет активных черновиков."

        profiles_str = json.dumps(supplier_profiles, ensure_ascii=False, indent=2)

        prompt_context = f"""
=== ИНФОРМАЦИЯ О СИСТЕМЕ ===
Активная модель ИИ: {GEMINI_MODEL} (если пользователь спросит, какую модель ты используешь или какая модель отвечает, назови именно её)

=== БЛОКНОТ ПАМЯТИ АССИСТЕНТА (ПРАВИЛА И НЮАНСЫ) ===
{assistant_memory if assistant_memory else "Блокнот памяти пуст. Здесь ты можешь хранить важные правила, особенности работы с поставщиками (например, перевод упаковок в кг, особенности веса, скидки) и любые другие нюансы, которые пользователь просит запомнить."}

=== ИСТОРИЯ ПЕРЕПИСКИ ===
{history_str}

=== АКТИВНЫЕ ЧЕРНОВИКИ ДНЯ ===
{drafts_str}

=== ИСТОРИЯ ПОСТАВОК (ПРОФИЛИ ИНГРЕДИЕНТОВ) ===
{profiles_str}

=== НОВОЕ СООБЩЕНИЕ ПОЛЬЗОВАТЕЛЯ ===
{user_message}
"""

        # Build payload with media if provided
        parts = [
            {"text": ASSISTANT_SYSTEM_PROMPT},
            {"text": prompt_context}
        ]

        if media_files:
            for media in media_files:
                parts.append({
                    "inlineData": {
                        "mimeType": media['mime_type'],
                        "data": base64.standard_b64encode(media['data']).decode("utf-8")
                    }
                })

        logger.info(f"🤖 [Gemini Assistant] Sending request to model {GEMINI_MODEL}...")
        try:
            response_text = await self._call_gemini_api(parts)
            json_text = self._extract_json(response_text)
            parsed = json.loads(json_text)
            logger.info("✅ Gemini assistant agent returned structured JSON successfully.")
            if isinstance(parsed, dict):
                parsed.setdefault('_model_used', GEMINI_MODEL)
            return parsed
        except Exception as e:
            logger.error(f"Failed calling Gemini assistant agent ({type(e).__name__}): {e}")
            return {
                "response_text": f"Ошибка: Ассистент Gemini временно недоступен. Пожалуйста, отправьте запрос повторно позже. Ошибка: {str(e)}",
                "actions": []
            }

    def _extract_json(self, text: str) -> str:
        """Extract JSON from Claude response text"""
        json_text = text

        # Remove markdown code blocks
        if "```json" in json_text:
            json_text = json_text.split("```json")[1].split("```")[0].strip()
        elif "```" in json_text:
            json_text = json_text.split("```")[1].split("```")[0].strip()

        # Find JSON object in text
        if not json_text.startswith("{"):
            start_idx = json_text.find("{")
            end_idx = json_text.rfind("}")
            if start_idx >= 0 and end_idx > start_idx:
                json_text = json_text[start_idx:end_idx+1]

        logger.debug(f"Extracted JSON: {json_text}")
        return json_text

    async def check_message_intent(self, message_text: str, media_files: Optional[List[Dict]] = None) -> bool:
        """
        Check if the incoming message or media has business bookkeeping intent.
        Returns True if yes, False if it is banter, chit-chat, or random kitchen photo.
        """
        if not GEMINI_API_KEY:
            # Fallback to processing if no key is configured
            return True

        prompt = """
        Вы — быстрый ИИ-классификатор намерений для бухгалтерского ассистента сети ресторанов PizzBurg.
        Проанализируйте входящее сообщение (текст и/или прикрепленные медиафайлы/фото).
        Определите, содержит ли сообщение финансовую информацию, накладную, чек, отчет о расходах или команду для бота-бухгалтера.

        Условия для ответа ДА (is_business_intent = true):
        1. В сообщении есть финансовый документ (накладная поставщика, фискальный чек, товарный чек, чек из банковского терминала, скриншот перевода).
        2. В тексте сообщается о тратах/поставках (например, "мойка 5500", "заплатили за мясо 20000", "дали аванс Беке 10к").
        3. Сообщение содержит команду боту (например, "удали чек", "покажи расходы за сегодня", "отчет").

        Условия для ответа НЕТ (is_business_intent = false):
        1. Обычный разговор/болтовня сотрудников, подтверждения без финансовой сути (например, "ок", "спасибо", "приняла", "во сколько завтра?", "Мадина!!!", "я на месте").
        2. Фотография еды, кухни, ведер, упаковок продуктов, испорченного товара, плесени или весов, если на фото НЕТ никакого бумажного чека/накладной и в тексте НЕТ описания расхода с суммой.

        Верните результат строго в формате JSON:
        {
          "is_business_intent": true/false,
          "reason": "краткое объяснение на русском"
        }
        """

        parts = [
            {"text": prompt},
            {"text": f"=== Входящее сообщение ===\nТекст: {message_text}\nКоличество файлов: {len(media_files) if media_files else 0}"}
        ]

        if media_files:
            for media in media_files:
                parts.append({
                    "inlineData": {
                        "mimeType": media['mime_type'],
                        "data": base64.standard_b64encode(media['data']).decode("utf-8")
                    }
                })

        try:
            response_text = await self._call_gemini_api(parts, timeout_seconds=30)
            json_text = self._extract_json(response_text)
            parsed = json.loads(json_text)
            is_intent = bool(parsed.get('is_business_intent', False))
            logger.info(f"🤖 [Intent Classifier] Classification result: {is_intent}. Reason: {parsed.get('reason')}")
            return is_intent
        except Exception as e:
            logger.error(f"Error in intent classification: {e}")
            return True



# Singleton instance
_parser_service = None


def get_parser_service() -> ParserService:
    """Get singleton ParserService instance"""
    global _parser_service
    if _parser_service is None:
        _parser_service = ParserService()
    return _parser_service
