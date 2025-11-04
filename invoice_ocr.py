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
    Распознать накладную с фото с помощью GPT-4 Vision (двухэтапный подход)

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

        # ШАГ 1: Чистый OCR - просто прочитать ВСЁ что на изображении
        ocr_prompt = """
Ты OCR система. Твоя ЕДИНСТВЕННАЯ задача - прочитать ВЕСЬ текст с изображения БУКВАЛЬНО.

ИНСТРУКЦИИ:
1. Прочитай ВСЕ строки текста с изображения
2. Копируй ТОЧНО как написано - каждую букву, цифру, символ
3. НЕ интерпретируй, НЕ анализируй, НЕ резюмируй
4. НЕ пропускай ни одной строки
5. Сохрани структуру документа (таблицы, колонки)

Верни ТОЛЬКО текст, который видишь. Ничего больше.
"""

        logger.info("🔍 ШАГ 1/2: Отправляю изображение для чистого OCR...")

        # Вызов GPT-4 Vision для чистого OCR
        ocr_response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": ocr_prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_data}"
                            }
                        }
                    ]
                }
            ],
            max_tokens=3000,
            temperature=0.0   # Максимальная точность для OCR
        )

        ocr_text = ocr_response.choices[0].message.content.strip()
        logger.info(f"📄 OCR получен: {len(ocr_text)} символов")
        logger.debug(f"OCR текст:\n{ocr_text}")

        # ШАГ 2: Парсинг текста в JSON
        parsing_prompt = f"""
Вот текст накладной (распознан через OCR):

---
{ocr_text}
---

Извлеки данные в JSON формате:

1. Найди название поставщика (ТОО, ИП, ООО)
2. Найди дату (преобразуй в YYYY-MM-DD)
3. Найди ВСЕ строки товаров в таблице

Для КАЖДОЙ строки товара извлеки:
- name: полное название (со всеми характеристиками)
- quantity: количество (число)
- unit: единица измерения (упак/шт/кг/л)
- price: цена за единицу (число)

ВАЖНО - Каждая строка таблицы = ОДНА позиция в items!
НЕ дублируй позиции. НЕ пропускай строки.

Пример OCR текста:
```
ТОО "Поставщик"
Дата: 01.11.2025
Товар А  5 кг  100
Товар Б  3 шт  200
```

Правильный JSON:
{{
    "supplier_name": "ТОО Поставщик",
    "invoice_date": "2025-11-01",
    "total_sum": 1100.0,
    "items": [
        {{"name": "Товар А", "quantity": 5.0, "unit": "кг", "price": 100.0}},
        {{"name": "Товар Б", "quantity": 3.0, "unit": "шт", "price": 200.0}}
    ]
}}

Верни JSON для текста выше:
"""

        logger.info("🔍 ШАГ 2/2: Парсинг текста в JSON...")

        # Вызов GPT-4 для парсинга (БЕЗ изображения!)
        # Используем JSON mode для гарантированного валидного JSON
        parsing_response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": "Ты система извлечения данных из накладных. Ты ВСЕГДА возвращаешь валидный JSON."
                },
                {
                    "role": "user",
                    "content": parsing_prompt
                }
            ],
            max_tokens=3000,
            temperature=0.1,
            response_format={"type": "json_object"}  # Гарантирует валидный JSON
        )

        # Парсим ответ
        result_text = parsing_response.choices[0].message.content.strip()

        logger.info(f"📄 Получен JSON ({len(result_text)} символов)")

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
            'ocr_text': ocr_text,  # Для отладки
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
