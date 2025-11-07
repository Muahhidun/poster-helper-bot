"""
Модуль для распознавания накладных используя только GPT-4 Vision
Не требует Document AI и системных библиотек C++
"""
import json
import logging
import base64
from typing import Dict
from openai import OpenAI
from config import OPENAI_API_KEY

logger = logging.getLogger(__name__)

# OpenAI клиент
openai_client = OpenAI(api_key=OPENAI_API_KEY)


async def recognize_invoice_from_url(image_url: str) -> Dict:
    """
    Распознать накладную по URL изображения используя только GPT-4 Vision

    Args:
        image_url: URL изображения (например, из Telegram)

    Returns:
        Dict с распознанными данными:
        - supplier_name: название поставщика (str)
        - items: список товаров (list)
        - success: bool
        - error: str (если ошибка)
    """
    try:
        logger.info("🤖 Распознавание через GPT-4 Vision (без Document AI)...")

        # Формируем промпт для GPT-4 Vision
        parsing_prompt = """
Это фото накладной. Извлеки данные в JSON формате:

1. Найди название поставщика (ищи ТОО, ИП, ООО, "Организация" и т.д.)
2. Найди ВСЕ строки товаров в таблице

Для КАЖДОЙ строки товара извлеки:
- name: полное название товара
- quantity: количество (число)
- price: цена за единицу (число в тенге)

ВАЖНО:
✅ Каждая строка таблицы = ОДНА позиция в items
✅ НЕ дублируй позиции
✅ НЕ пропускай строки
✅ Извлекай ТОЧНЫЕ названия товаров
✅ Quantity и price должны быть числами

Верни JSON:
{
    "supplier_name": "Название поставщика",
    "items": [
        {"name": "Название товара", "quantity": 10.5, "price": 1500}
    ]
}
"""

        # Вызов GPT-4 Vision
        response = openai_client.chat.completions.create(
            model="gpt-4o",  # gpt-4o поддерживает vision
            messages=[
                {
                    "role": "system",
                    "content": "Ты система извлечения данных из накладных. Ты ВСЕГДА возвращаешь валидный JSON."
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": parsing_prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": image_url}
                        }
                    ]
                }
            ],
            max_tokens=3000,
            temperature=0.1,
            response_format={"type": "json_object"}  # Гарантирует валидный JSON
        )

        # Парсим ответ
        result_text = response.choices[0].message.content.strip()
        logger.info(f"📄 Получен JSON ({len(result_text)} символов)")

        # Парсим JSON
        data = json.loads(result_text)

        # Валидация данных
        items = data.get('items', [])

        if not items:
            logger.warning("⚠️ GPT-4 не нашел товаров в накладной")
            return {
                'success': False,
                'error': 'Не найдено товаров в накладной'
            }

        # Нормализуем данные
        for item in items:
            # Убедимся что quantity и price - числа
            if not isinstance(item.get('quantity'), (int, float)):
                logger.warning(f"Invalid quantity for {item.get('name')}: {item.get('quantity')}")
                item['quantity'] = 0
            if not isinstance(item.get('price'), (int, float)):
                logger.warning(f"Invalid price for {item.get('name')}: {item.get('price')}")
                item['price'] = 0

        result = {
            'success': True,
            'supplier_name': data.get('supplier_name', 'Неизвестный поставщик'),
            'items': items,
            'raw_response': result_text  # Для отладки
        }

        logger.info(f"✅ Накладная распознана: поставщик={result['supplier_name']}, товаров={len(items)}")

        return result

    except Exception as e:
        logger.error(f"❌ Ошибка распознавания накладной: {e}", exc_info=True)
        return {
            'success': False,
            'error': str(e)
        }


async def recognize_invoice_from_path(image_path: str) -> Dict:
    """
    Распознать накладную из локального файла используя GPT-4 Vision

    Args:
        image_path: Путь к файлу изображения

    Returns:
        Dict с распознанными данными (см. recognize_invoice_from_url)
    """
    try:
        # Читаем файл и кодируем в base64
        with open(image_path, 'rb') as f:
            image_data = f.read()

        image_base64 = base64.b64encode(image_data).decode('utf-8')
        data_url = f"data:image/jpeg;base64,{image_base64}"

        # Используем функцию для URL
        return await recognize_invoice_from_url(data_url)

    except Exception as e:
        logger.error(f"❌ Ошибка чтения файла: {e}", exc_info=True)
        return {
            'success': False,
            'error': str(e)
        }


if __name__ == "__main__":
    import asyncio
    import sys

    if len(sys.argv) < 2:
        print("Использование: python invoice_ocr_gpt4_only.py <путь_к_фото_накладной>")
        sys.exit(1)

    image_path = sys.argv[1]

    async def test():
        print(f"🔍 Распознаём накладную (GPT-4 Vision only): {image_path}")
        print("=" * 70)
        print()

        result = await recognize_invoice_from_path(image_path)

        if result['success']:
            print("✅ Накладная успешно распознана!")
            print()
            print(f"📦 Поставщик: {result['supplier_name']}")

            print(f"\n📋 Товары ({len(result['items'])} шт.):")
            print("-" * 70)

            for i, item in enumerate(result['items'], 1):
                print(f"{i}. {item['name']}")
                print(f"   Количество: {item['quantity']}")
                print(f"   Цена: {item['price']:,.2f}₸")
                print()
        else:
            print(f"❌ Ошибка: {result['error']}")

        print("=" * 70)

    asyncio.run(test())
