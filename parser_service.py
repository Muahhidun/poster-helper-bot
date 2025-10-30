"""Parser service using Claude API for structured data extraction"""
import logging
import json
import base64
from typing import Dict, Optional, List
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from config import ANTHROPIC_API_KEY, OPENAI_API_KEY

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
   - Сумма / Итого (можно игнорировать, мы посчитаем сами)

3. **Единицы измерения**: кг, шт, л, упак

ВАЖНЫЕ ПРАВИЛА:

1. **Количество**: Бери количество ровно как указано в накладной
   - Если написано "10 упак" — бери 10
   - Если написано "2760.00 кг" — бери 2760.00
   - НЕ КОНВЕРТИРУЙ упаковки в штуки! Бот сделает это сам
   - Убери пробелы из чисел: "2 760.00" → 2760.00

2. **Цена**: Бери цену ровно как указано в накладной
   - Ищи столбец "Цена", "Цена за ед.", "Стоимость"
   - Если цена за упаковку 5190 — бери 5190
   - НЕ КОНВЕРТИРУЙ цену! Бот сделает это сам
   - НЕ бери значение из столбца "Сумма" или "Итого"

3. **Название товара**: Бери полное название из накладной как есть
   - Примеры: "Филе ЦБ, групп, охл", "Крыло ЦБ, групп, охл"
   - НЕ пытайся сокращать или переименовывать

4. **Игнорируй лишние столбцы**:
   - Артикулы, коды товаров
   - НДС, налоги
   - Сумма без НДС, с НДС
   - Нас интересуют только: название, количество, цена за единицу

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
      "price": <цена за единицу числом>
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
- Товар 1: Филе ЦБ, групп, охл | 2760.00 кг | 2160.00 тг/кг
- Товар 2: Крыло ЦБ, групп, охл | 567.30 кг | 502.30 тг/кг

Результат:
{{
  "type": "supply",
  "supplier": "Фирма ИртышИнтерФуд",
  "account": null,
  "items": [
    {{"name": "Филе ЦБ, групп, охл", "qty": 2760.00, "price": 2160.00}},
    {{"name": "Крыло ЦБ, групп, охл", "qty": 567.30, "price": 502.30}}
  ],
  "unrecognized_items": [],
  "total": null,
  "notes": "Накладная распознана полностью"
}}

ВАЖНО: отвечай ТОЛЬКО валидным JSON, без дополнительного текста!"""


class ParserService:
    """Service for parsing text using Claude API and OpenAI Vision"""

    def __init__(self):
        self.client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
        self.openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

    async def parse_transaction(self, text: str) -> Optional[Dict]:
        """
        Parse transaction data from text using Claude

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

            response = await self.openai_client.chat.completions.create(
                model="gpt-4o-mini",
                max_tokens=512,
                temperature=0,
                messages=[
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"}
            )

            response_text = response.choices[0].message.content.strip()
            logger.debug(f"OpenAI raw response: {response_text}")

            # Try to extract JSON from response
            json_text = self._extract_json(response_text)
            parsed = json.loads(json_text)
            logger.info(f"✅ Transaction parsed successfully: {parsed}")

            # Validate required fields
            if not parsed.get("amount") or not parsed.get("category"):
                logger.warning("Parsed data missing required fields")
                return None

            return parsed

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Claude response as JSON: {e}")
            return None
        except Exception as e:
            logger.error(f"Claude parsing failed: {e}")
            return None

    async def parse_supply(self, text: str) -> Optional[Dict]:
        """
        Parse supply data from text using Claude

        Args:
            text: Input text (from voice or manual input)

        Returns:
            Parsed supply dict or None if parsing failed
        """
        try:
            logger.info(f"Parsing supply text: '{text}'")

            prompt = SUPPLY_PARSER_PROMPT.format(text=text)

            try:
                response = await self.openai_client.chat.completions.create(
                    model="gpt-4o-mini",
                    max_tokens=1024,
                    temperature=0,
                    messages=[
                        {"role": "user", "content": prompt}
                    ],
                    response_format={"type": "json_object"}
                )
            except Exception as api_error:
                logger.error(f"OpenAI API call failed: {type(api_error).__name__}: {api_error}")
                raise

            response_text = response.choices[0].message.content.strip()
            logger.debug(f"OpenAI raw response: {response_text}")

            # Extract JSON from response
            json_text = self._extract_json(response_text)
            logger.debug(f"json_text type: {type(json_text)}, repr: {repr(json_text)}")
            parsed = json.loads(json_text)
            logger.info(f"✅ Supply parsed successfully: {parsed}")

            # Validate required fields
            if not parsed.get("supplier") or not parsed.get("items"):
                logger.warning("Parsed supply missing required fields")
                return None

            return parsed

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Claude response as JSON: {e}")
            return None
        except Exception as e:
            logger.error(f"Claude supply parsing failed: {e}")
            return None

    async def parse_multiple_transactions(self, text: str) -> Optional[Dict]:
        """
        Parse multiple transactions from text using Claude

        Args:
            text: Input text with multiple transactions

        Returns:
            Parsed dict with type='multiple_expenses' and list of transactions
        """
        try:
            logger.info(f"Parsing multiple transactions: '{text}'")

            prompt = MULTIPLE_TRANSACTIONS_PARSER_PROMPT.format(text=text)

            response = await self.openai_client.chat.completions.create(
                model="gpt-4o-mini",
                max_tokens=1024,
                temperature=0,
                messages=[
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"}
            )

            response_text = response.choices[0].message.content.strip()
            logger.debug(f"OpenAI raw response: {response_text}")

            # Try to extract JSON from response
            json_text = self._extract_json(response_text)
            parsed = json.loads(json_text)
            logger.info(f"✅ Multiple transactions parsed successfully: {parsed}")

            # Validate required fields
            if not parsed.get("transactions") or not isinstance(parsed["transactions"], list):
                logger.warning("Parsed data missing transactions list")
                return None

            return parsed

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Claude response as JSON: {e}")
            return None
        except Exception as e:
            logger.error(f"Claude multiple transactions parsing failed: {e}")
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
        Parse supply invoice from image or PDF using GPT-4o Vision API

        Args:
            file_data: Image or PDF bytes
            media_type: MIME type (image/jpeg, image/png, or application/pdf)

        Returns:
            Parsed supply dict or None if parsing failed
        """
        try:
            file_type = "PDF" if media_type == "application/pdf" else "image"
            logger.info(f"Parsing invoice from {file_type} using GPT-4o Vision API")

            # Encode to base64
            file_base64 = base64.standard_b64encode(file_data).decode("utf-8")

            # Create message with image
            # Using GPT-4o for best OCR performance
            response = await self.openai_client.chat.completions.create(
                model="gpt-4o",
                max_tokens=4096,
                temperature=0,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": INVOICE_PARSER_PROMPT
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{media_type};base64,{file_base64}",
                                    "detail": "high"  # High detail for better OCR
                                }
                            }
                        ]
                    }
                ]
            )

            response_text = response.choices[0].message.content.strip()
            logger.info(f"GPT-4o Vision raw response: {response_text[:500]}...")

            # Extract JSON from response
            json_text = self._extract_json(response_text)
            logger.debug(f"Extracted JSON: {json_text[:500]}...")

            try:
                parsed = json.loads(json_text)
            except json.JSONDecodeError as e:
                # Try to fix common JSON issues
                logger.warning(f"JSON parse error: {e}, attempting to fix...")
                # Replace single quotes with double quotes (common GPT error)
                json_text = json_text.replace("'", '"')
                # Try again
                try:
                    parsed = json.loads(json_text)
                    logger.info("✅ Fixed JSON successfully")
                except json.JSONDecodeError as e2:
                    logger.error(f"Failed to fix JSON: {e2}")
                    logger.error(f"Problematic JSON: {json_text}")
                    raise

            logger.info(f"✅ Invoice parsed successfully: supplier={parsed.get('supplier')}, items={len(parsed.get('items', []))}")

            # Validate required fields (supplier is optional, will be selected manually if missing)
            if not parsed.get("items"):
                logger.warning("Parsed invoice has no items")
                return None

            return parsed

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse GPT-4o Vision response as JSON: {e}")
            return None
        except Exception as e:
            logger.error(f"GPT-4o Vision invoice parsing failed: {e}")
            return None

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


# Singleton instance
_parser_service = None


def get_parser_service() -> ParserService:
    """Get singleton ParserService instance"""
    global _parser_service
    if _parser_service is None:
        _parser_service = ParserService()
    return _parser_service
