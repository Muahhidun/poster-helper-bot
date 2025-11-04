"""Модуль для распознавания накладных (гибридный подход: Document AI OCR + GPT-4)"""
import json
import logging
from typing import Dict
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

# Создаём клиенты
def get_docai_client():
    """Создать клиент Document AI с credentials из переменной окружения"""
    if not GOOGLE_APPLICATION_CREDENTIALS_JSON:
        raise ValueError("GOOGLE_APPLICATION_CREDENTIALS_JSON не установлен")

    # Парсим JSON credentials
    credentials_dict = json.loads(GOOGLE_APPLICATION_CREDENTIALS_JSON)
    credentials = service_account.Credentials.from_service_account_info(credentials_dict)

    # Создаём клиент с правильным endpoint
    opts = {"api_endpoint": f"{GOOGLE_CLOUD_LOCATION}-documentai.googleapis.com"}
    client = documentai.DocumentProcessorServiceClient(
        credentials=credentials,
        client_options=opts
    )

    return client


# OpenAI клиент
openai_client = OpenAI(api_key=OPENAI_API_KEY)


async def recognize_invoice(image_path: str) -> Dict:
    """
    Распознать накладную с фото (гибридный подход)

    Шаг 1: Document AI OCR читает весь текст
    Шаг 2: GPT-4 парсит текст в JSON

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
        # ШАГ 1: Document AI OCR - прочитать весь текст
        logger.info("🔍 ШАГ 1/2: Document AI OCR читает текст...")

        # Читаем изображение
        with open(image_path, 'rb') as f:
            image_content = f.read()

        # Создаём клиент Document AI
        docai_client = get_docai_client()

        # Формируем имя процессора
        processor_name = docai_client.processor_path(
            GOOGLE_CLOUD_PROJECT_ID,
            GOOGLE_CLOUD_LOCATION,
            GOOGLE_DOCAI_OCR_PROCESSOR_ID
        )

        # Создаём запрос
        raw_document = documentai.RawDocument(
            content=image_content,
            mime_type="image/jpeg"
        )

        request = documentai.ProcessRequest(
            name=processor_name,
            raw_document=raw_document
        )

        # Отправляем запрос
        result = docai_client.process_document(request=request)
        document = result.document

        # Получаем весь распознанный текст
        ocr_text = document.text

        logger.info(f"📄 OCR получен: {len(ocr_text)} символов")
        logger.debug(f"OCR текст:\n{ocr_text}")

        # ШАГ 2: GPT-4 парсит текст в JSON
        logger.info("🔍 ШАГ 2/2: GPT-4 парсит текст в JSON...")

        parsing_prompt = f"""
Вот текст накладной (распознан через OCR):

---
{ocr_text}
---

Извлеки данные в JSON формате:

1. Найди название поставщика (ищи ТОО, ИП, ООО, "Организация" и т.д.)
2. Найди дату накладной (преобразуй в YYYY-MM-DD)
3. Найди ВСЕ строки товаров в таблице

Для КАЖДОЙ строки товара извлеки:
- name: полное название из колонки "Наименование"
- quantity: количество из колонки с количеством (обычно колонка 5 или "подлежит отпуску")
- unit: единица измерения (упак/шт/кг/л)
- price: цена за единицу (обычно колонка 6 или "цена за единицу")

ВАЖНО:
✅ Каждая строка таблицы = ОДНА позиция в items
✅ НЕ дублируй позиции
✅ НЕ пропускай строки
✅ Извлекай ТОЧНЫЕ названия товаров из колонки "Наименование"

Верни JSON:
{{
    "supplier_name": "Название поставщика",
    "invoice_date": "YYYY-MM-DD",
    "total_sum": 95580.0,
    "items": [
        {{"name": "Название товара", "quantity": 1.0, "unit": "упак", "price": 5190.0}}
    ]
}}
"""

        # Вызов GPT-4 для парсинга (БЕЗ изображения!)
        parsing_response = openai_client.chat.completions.create(
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

        # Парсим JSON
        data = json.loads(result_text)

        # Валидация и нормализация данных
        items = data.get('items', [])

        # Проверяем что есть хотя бы один товар
        if not items:
            logger.warning("⚠️ GPT-4 не нашел товаров в накладной")

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
        logger.error(f"❌ Ошибка парсинга JSON от GPT-4: {e}")
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
