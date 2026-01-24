"""
Expense Input Module - обработка расходов из листа кассира и Kaspi выписки

Workflow:
1. Пользователь скидывает фото/текст
2. OCR распознаёт текст
3. GPT парсит в список расходов с типами (транзакция/поставка)
4. Пользователь подтверждает/редактирует
5. Создаются транзакции в Poster
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from enum import Enum
from datetime import datetime

from google.cloud import documentai_v1 as documentai
from google.oauth2 import service_account
from openai import OpenAI

from config import (
    GOOGLE_CLOUD_PROJECT_ID,
    GOOGLE_CLOUD_LOCATION,
    GOOGLE_DOCAI_OCR_PROCESSOR_ID,
    GOOGLE_APPLICATION_CREDENTIALS_JSON,
    OPENAI_API_KEY
)

logger = logging.getLogger(__name__)

# OpenAI клиент
openai_client = OpenAI(api_key=OPENAI_API_KEY)


class ExpenseType(Enum):
    """Тип расхода"""
    TRANSACTION = "транзакция"  # Простой расход (услуги, зарплаты)
    SUPPLY = "поставка"  # Закуп товаров (нужна накладная)


@dataclass
class ExpenseItem:
    """Одна позиция расхода"""
    amount: float  # Сумма в тенге
    description: str  # Описание/комментарий
    expense_type: ExpenseType  # Тип: транзакция или поставка
    category: Optional[str] = None  # Категория расхода
    source: str = "наличка"  # Источник: наличка, kaspi

    # Для поставок
    quantity: Optional[float] = None
    unit: Optional[str] = None
    price_per_unit: Optional[float] = None

    # Идентификатор для кнопок
    id: str = field(default_factory=lambda: "")

    def __post_init__(self):
        if not self.id:
            # Генерируем уникальный ID
            import hashlib
            data = f"{self.amount}{self.description}{datetime.now().timestamp()}"
            self.id = hashlib.md5(data.encode()).hexdigest()[:8]


@dataclass
class ExpenseSession:
    """Сессия ввода расходов"""
    items: List[ExpenseItem] = field(default_factory=list)
    source_account: str = "Оставил в кассе (на закупы)"  # Счёт списания
    created_at: datetime = field(default_factory=datetime.now)

    def get_transactions(self) -> List[ExpenseItem]:
        """Получить только транзакции"""
        return [i for i in self.items if i.expense_type == ExpenseType.TRANSACTION]

    def get_supplies(self) -> List[ExpenseItem]:
        """Получить только поставки"""
        return [i for i in self.items if i.expense_type == ExpenseType.SUPPLY]

    def total_amount(self) -> float:
        """Общая сумма"""
        return sum(i.amount for i in self.items)

    def toggle_type(self, item_id: str) -> bool:
        """Переключить тип расхода (транзакция <-> поставка)"""
        for item in self.items:
            if item.id == item_id:
                if item.expense_type == ExpenseType.TRANSACTION:
                    item.expense_type = ExpenseType.SUPPLY
                else:
                    item.expense_type = ExpenseType.TRANSACTION
                return True
        return False


# Категории расходов и ключевые слова для определения
CATEGORY_KEYWORDS = {
    "Зарплаты": ["зарплата", "зп", "курьер", "кассир", "повар", "оплата труда", "аванс"],
    "Хозтовары": ["мыло", "моющее", "салфетки", "туалетная", "губки", "перчатки", "мусорные", "пакеты"],
    "Транспорт": ["такси", "доставка", "яндекс", "убер", "бензин", "топливо"],
    "Коммуналка": ["свет", "электричество", "вода", "газ", "отопление", "аренда", "интернет"],
    "Ремонт": ["ремонт", "запчасти", "сантехник", "электрик"],
    "Реклама": ["реклама", "баннер", "флаер", "instagram", "smm"],
    "Канцелярия": ["канцелярия", "бумага", "ручки", "скотч", "файлы"],
}

# Ключевые слова для определения поставок (закуп продуктов)
SUPPLY_KEYWORDS = [
    # Мясо
    "фарш", "крыло", "курица", "говядина", "свинина", "мясо", "бедро", "филе",
    # Молочка
    "сыр", "молоко", "сметана", "творог", "масло", "пармезан", "чеддер", "моцарелла",
    # Овощи
    "овощи", "помидор", "огурец", "лук", "картофель", "морковь", "капуста", "перец",
    "кюрдамир", "зелень", "салат",
    # Другие продукты
    "мука", "соус", "кетчуп", "майонез", "колбаса", "сосиски", "яйца",
    # Напитки
    "кола", "спрайт", "вода", "сок", "напиток",
    # Поставщики
    "арзан", "магнум", "метро", "япоша", "идея", "сарыарка",
]


def detect_expense_type(description: str) -> ExpenseType:
    """Определить тип расхода по описанию"""
    desc_lower = description.lower()

    # Проверяем на поставку
    for keyword in SUPPLY_KEYWORDS:
        if keyword in desc_lower:
            return ExpenseType.SUPPLY

    # По умолчанию - транзакция
    return ExpenseType.TRANSACTION


def detect_category(description: str) -> Optional[str]:
    """Определить категорию расхода по описанию"""
    desc_lower = description.lower()

    for category, keywords in CATEGORY_KEYWORDS.items():
        for keyword in keywords:
            if keyword in desc_lower:
                return category

    return "Прочее"


def get_docai_client():
    """Создать клиент Document AI"""
    if not GOOGLE_APPLICATION_CREDENTIALS_JSON:
        raise ValueError("GOOGLE_APPLICATION_CREDENTIALS_JSON не установлен")

    credentials_dict = json.loads(GOOGLE_APPLICATION_CREDENTIALS_JSON)
    credentials = service_account.Credentials.from_service_account_info(credentials_dict)

    opts = {"api_endpoint": f"{GOOGLE_CLOUD_LOCATION}-documentai.googleapis.com"}
    client = documentai.DocumentProcessorServiceClient(
        credentials=credentials,
        client_options=opts
    )

    return client


async def ocr_image(image_path: str) -> str:
    """Распознать текст с изображения через Document AI"""
    with open(image_path, 'rb') as f:
        image_content = f.read()

    docai_client = get_docai_client()

    processor_name = docai_client.processor_path(
        GOOGLE_CLOUD_PROJECT_ID,
        GOOGLE_CLOUD_LOCATION,
        GOOGLE_DOCAI_OCR_PROCESSOR_ID
    )

    raw_document = documentai.RawDocument(
        content=image_content,
        mime_type="image/jpeg"
    )

    request = documentai.ProcessRequest(
        name=processor_name,
        raw_document=raw_document
    )

    result = docai_client.process_document(request=request)
    return result.document.text


async def parse_cashier_sheet(ocr_text: str, source: str = "наличка") -> List[ExpenseItem]:
    """
    Распарсить лист кассира через GPT-4

    Args:
        ocr_text: Распознанный текст с листа
        source: Источник средств (наличка, kaspi)

    Returns:
        Список ExpenseItem
    """
    prompt = f"""
Вот текст листа расходов кассира (распознан через OCR):

---
{ocr_text}
---

Извлеки ВСЕ расходы в JSON формате. Для каждого расхода определи:
1. amount - сумма в тенге (число)
2. description - описание (что купили/оплатили)
3. type - тип: "транзакция" (услуги, зарплаты, хозтовары) или "поставка" (продукты питания для ресторана)

Правила определения типа:
- "поставка" = закуп продуктов: мясо, овощи, молочка, напитки, ингредиенты
- "транзакция" = всё остальное: зарплаты, такси, хозтовары, ремонт, услуги

Примеры:
- "Фарш 12кг 33600" → поставка (мясо)
- "Зарплата курьеру 15000" → транзакция (зарплата)
- "Овощи Кюрдамир 8500" → поставка (овощи)
- "Мыломойка 3500" → транзакция (хозтовары)

Если есть количество и цена за единицу, извлеки их тоже:
- quantity - количество (число)
- unit - единица (кг, шт, л, упак)
- price_per_unit - цена за единицу

Верни JSON:
{{
    "items": [
        {{
            "amount": 33600,
            "description": "Фарш",
            "type": "поставка",
            "quantity": 12,
            "unit": "кг",
            "price_per_unit": 2800
        }},
        {{
            "amount": 15000,
            "description": "Зарплата курьеру",
            "type": "транзакция"
        }}
    ]
}}

ВАЖНО:
- Извлекай ВСЕ строки расходов
- Суммы должны быть точными
- Если не можешь определить тип - ставь "транзакция"
"""

    response = openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {
                "role": "system",
                "content": "Ты система извлечения данных из рукописных листов расходов. Возвращаешь валидный JSON."
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        max_tokens=3000,
        temperature=0.1,
        response_format={"type": "json_object"}
    )

    result_text = response.choices[0].message.content.strip()
    data = json.loads(result_text)

    items = []
    for item_data in data.get('items', []):
        expense_type = (
            ExpenseType.SUPPLY
            if item_data.get('type') == 'поставка'
            else ExpenseType.TRANSACTION
        )

        item = ExpenseItem(
            amount=float(item_data['amount']),
            description=item_data['description'],
            expense_type=expense_type,
            category=detect_category(item_data['description']),
            source=source,
            quantity=item_data.get('quantity'),
            unit=item_data.get('unit'),
            price_per_unit=item_data.get('price_per_unit')
        )
        items.append(item)

    return items


async def parse_cashier_sheet_from_image(image_path: str, source: str = "наличка") -> List[ExpenseItem]:
    """
    Распознать и распарсить лист кассира с фото

    Args:
        image_path: Путь к фото
        source: Источник средств

    Returns:
        Список ExpenseItem
    """
    logger.info(f"🔍 OCR листа кассира: {image_path}")

    # OCR
    ocr_text = await ocr_image(image_path)
    logger.info(f"📄 OCR получен: {len(ocr_text)} символов")

    # Парсинг через GPT
    items = await parse_cashier_sheet(ocr_text, source)
    logger.info(f"✅ Распознано {len(items)} позиций")

    return items


async def parse_cashier_sheet_from_url(image_url: str, source: str = "наличка") -> List[ExpenseItem]:
    """Распознать лист кассира по URL"""
    import aiohttp
    import tempfile
    import os

    async with aiohttp.ClientSession() as session:
        async with session.get(image_url) as response:
            if response.status != 200:
                raise Exception(f"Не удалось скачать: HTTP {response.status}")
            image_data = await response.read()

    with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as tmp_file:
        tmp_file.write(image_data)
        tmp_path = tmp_file.name

    try:
        return await parse_cashier_sheet_from_image(tmp_path, source)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def format_expense_list(session: ExpenseSession) -> str:
    """Форматировать список расходов для отображения в боте"""
    lines = [f"📋 **{session.source_account}**\n"]

    for i, item in enumerate(session.items, 1):
        type_emoji = "📦" if item.expense_type == ExpenseType.SUPPLY else "💰"
        type_label = "поставка" if item.expense_type == ExpenseType.SUPPLY else "транзакция"

        lines.append(f"{i}. {type_emoji} {item.amount:,.0f}₸ ({type_label})")
        lines.append(f"   └ {item.description}")
        if item.category and item.expense_type == ExpenseType.TRANSACTION:
            lines.append(f"   └ Категория: {item.category}")
        lines.append("")

    # Итоги
    transactions = session.get_transactions()
    supplies = session.get_supplies()

    lines.append("─" * 25)
    lines.append(f"💰 Транзакций: {len(transactions)} на {sum(t.amount for t in transactions):,.0f}₸")
    lines.append(f"📦 Поставок: {len(supplies)} на {sum(s.amount for s in supplies):,.0f}₸")
    lines.append(f"**Итого: {session.total_amount():,.0f}₸**")

    return "\n".join(lines)


async def create_transactions_in_poster(
    session: ExpenseSession,
    telegram_user_id: int,
    account_id: int,
    category_map: Dict[str, int]
) -> Tuple[int, int, List[str]]:
    """
    Создать транзакции в Poster

    Args:
        session: Сессия с расходами
        telegram_user_id: ID пользователя Telegram
        account_id: ID счёта в Poster
        category_map: Маппинг название категории -> ID в Poster

    Returns:
        (успешно, ошибок, список ошибок)
    """
    from database import get_database
    from poster_client import PosterClient

    db = get_database()
    accounts = db.get_accounts(telegram_user_id)

    if not accounts:
        return 0, 0, ["Нет подключенных аккаунтов Poster"]

    # Берём первый аккаунт
    account = accounts[0]

    client = PosterClient(
        telegram_user_id=telegram_user_id,
        poster_token=account['poster_token'],
        poster_user_id=account['poster_user_id'],
        poster_base_url=account['poster_base_url']
    )

    success_count = 0
    error_count = 0
    errors = []

    try:
        transactions = session.get_transactions()

        for item in transactions:
            try:
                # Определяем категорию
                category_id = category_map.get(item.category, category_map.get("Прочее", 1))

                # Создаём транзакцию
                await client.create_transaction(
                    transaction_type=0,  # expense
                    category_id=category_id,
                    account_from_id=account_id,
                    amount=int(item.amount),
                    comment=item.description
                )

                success_count += 1
                logger.info(f"✅ Создана транзакция: {item.amount}₸ - {item.description}")

            except Exception as e:
                error_count += 1
                errors.append(f"{item.description}: {str(e)}")
                logger.error(f"❌ Ошибка создания транзакции: {e}")

    finally:
        await client.close()

    return success_count, error_count, errors
