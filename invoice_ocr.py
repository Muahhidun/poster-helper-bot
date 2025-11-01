"""Модуль для распознавания накладных с помощью OCR (GPT-4 Vision)"""
import base64
import logging
from typing import Dict, List, Optional
from openai import OpenAI
from config import OPENAI_API_KEY

logger = logging.getLogger(__name__)

# Создаём клиент OpenAI
client = OpenAI(api_key=OPENAI_API_KEY)


async def recognize_invoice(image_path: str) -> Dict:
    """
    Распознать накладную с фото с помощью GPT-4 Vision

    Args:
        image_path: Путь к файлу с фото накладной

    Returns:
        Dict с распознанными данными:
        - supplier_name: название поставщика (str)
        - invoice_date: дата в формате YYYY-MM-DD (str)
        - items: список товаров (list)
        - total_sum: общая сумма (float, optional)
        - success: bool
        - error: str (если ошибка)
    """
    try:
        # Читаем изображение и кодируем в base64
        with open(image_path, 'rb') as f:
            image_data = base64.b64encode(f.read()).decode('utf-8')

        # Создаем промпт для GPT-4 Vision
        prompt = """
Проанализируй эту накладную поставщика и извлеки следующую информацию:

1. Название поставщика (организация, от которой поступили товары)
2. Дата накладной (в формате YYYY-MM-DD)
3. Список ВСЕХ товаров с:
   - Название товара (точно как написано)
   - Количество (число)
   - Единица измерения (кг, г, л, мл, шт)
   - Цена за единицу (число)
4. Общая сумма накладной (если указана)

ВАЖНО:
- НЕ придумывай данные, которых нет в накладной
- Если какого-то поля нет - оставь его пустым (null)
- Дата может быть в разных форматах: "01.11.2025", "1 ноября 2025", "2025-11-01" - преобразуй в YYYY-MM-DD
- Единицы измерения могут быть: кг, г, л, мл, шт, упак, бут
- Если единица измерения не указана явно - используй "шт"
- Извлекай ТОЧНЫЕ данные из накладной, не интерпретируй

Верни ТОЛЬКО JSON в таком формате (без дополнительного текста):
{
    "supplier_name": "Название поставщика",
    "invoice_date": "YYYY-MM-DD",
    "total_sum": 15000.0,
    "items": [
        {
            "name": "Название товара",
            "quantity": 10.0,
            "unit": "кг",
            "price": 500.0
        },
        {
            "name": "Другой товар",
            "quantity": 5.0,
            "unit": "шт",
            "price": 1000.0
        }
    ]
}

Пример ответа:
{
    "supplier_name": "ТОО Япоша",
    "invoice_date": "2025-11-01",
    "total_sum": 45000.0,
    "items": [
        {
            "name": "Лук репчатый",
            "quantity": 10.0,
            "unit": "кг",
            "price": 500.0
        },
        {
            "name": "Картофель",
            "quantity": 20.0,
            "unit": "кг",
            "price": 1000.0
        },
        {
            "name": "Морковь",
            "quantity": 5.0,
            "unit": "кг",
            "price": 300.0
        }
    ]
}
"""

        logger.info("🔍 Отправляю накладную в GPT-4 Vision...")

        # Вызов GPT-4 Vision API
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_data}"
                            }
                        }
                    ]
                }
            ],
            max_tokens=2000,  # Больше токенов для списка товаров
            temperature=0.1   # Низкая температура для точности
        )

        # Парсим ответ
        result_text = response.choices[0].message.content.strip()

        logger.info(f"📄 Получен ответ от GPT-4 Vision ({len(result_text)} символов)")

        # Удаляем markdown форматирование если есть
        if result_text.startswith('```'):
            result_text = result_text.split('```')[1]
            if result_text.startswith('json'):
                result_text = result_text[4:]
            result_text = result_text.strip()

        # Парсим JSON
        import json
        data = json.loads(result_text)

        # Валидация и нормализация данных
        items = data.get('items', [])

        # Проверяем что есть хотя бы один товар
        if not items:
            logger.warning("⚠️ GPT-4 Vision не нашел товаров в накладной")

        # Нормализуем единицы измерения
        for item in items:
            if 'unit' not in item or not item['unit']:
                item['unit'] = 'шт'
            item['unit'] = item['unit'].lower().strip()

            # Вычисляем итог по позиции
            item['total'] = item['quantity'] * item['price']

        result = {
            'success': True,
            'supplier_name': data.get('supplier_name'),
            'invoice_date': data.get('invoice_date'),
            'total_sum': data.get('total_sum'),
            'items': items,
            'raw_response': result_text
        }

        logger.info(
            f"✅ Накладная распознана: поставщик={result['supplier_name']}, "
            f"дата={result['invoice_date']}, товаров={len(items)}"
        )

        return result

    except json.JSONDecodeError as e:
        logger.error(f"❌ Ошибка парсинга JSON от GPT-4 Vision: {e}")
        logger.error(f"Ответ GPT-4: {result_text if 'result_text' in locals() else 'не получен'}")
        return {
            'success': False,
            'error': f'Не удалось распарсить ответ GPT-4: {str(e)}'
        }
    except Exception as e:
        logger.error(f"❌ Ошибка распознавания накладной: {e}", exc_info=True)
        return {
            'success': False,
            'error': str(e)
        }


async def recognize_invoice_from_url(image_url: str) -> Dict:
    """
    Распознать накладную по URL изображения

    Args:
        image_url: URL изображения (например, из Telegram)

    Returns:
        Dict с распознанными данными (см. recognize_invoice)
    """
    try:
        import aiohttp
        import tempfile
        import os

        # Скачиваем изображение
        async with aiohttp.ClientSession() as session:
            async with session.get(image_url) as response:
                if response.status != 200:
                    raise Exception(f"Не удалось скачать изображение: HTTP {response.status}")

                image_data = await response.read()

        # Сохраняем во временный файл
        with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as tmp_file:
            tmp_file.write(image_data)
            tmp_path = tmp_file.name

        try:
            # Распознаем
            result = await recognize_invoice(tmp_path)
            return result
        finally:
            # Удаляем временный файл
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    except Exception as e:
        logger.error(f"❌ Ошибка распознавания накладной по URL: {e}", exc_info=True)
        return {
            'success': False,
            'error': str(e)
        }


if __name__ == "__main__":
    import asyncio
    import sys

    if len(sys.argv) < 2:
        print("Использование: python invoice_ocr.py <путь_к_фото_накладной>")
        sys.exit(1)

    image_path = sys.argv[1]

    async def test():
        print(f"🔍 Распознаём накладную: {image_path}")
        print("=" * 70)
        print()

        result = await recognize_invoice(image_path)

        if result['success']:
            print("✅ Накладная успешно распознана!")
            print()
            print(f"📦 Поставщик: {result['supplier_name']}")
            print(f"📅 Дата: {result['invoice_date']}")

            if result.get('total_sum'):
                print(f"💰 Общая сумма: {result['total_sum']:,.2f}₸")

            print(f"\n📋 Товары ({len(result['items'])} шт.):")
            print("-" * 70)

            for i, item in enumerate(result['items'], 1):
                print(f"{i}. {item['name']}")
                print(f"   Количество: {item['quantity']} {item['unit']}")
                print(f"   Цена: {item['price']:,.2f}₸")
                print(f"   Итого: {item['total']:,.2f}₸")
                print()
        else:
            print(f"❌ Ошибка: {result['error']}")

        print("=" * 70)

    asyncio.run(test())
