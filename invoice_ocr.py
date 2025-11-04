"""Модуль для распознавания накладных с помощью Google Document AI"""
import base64
import json
import logging
from typing import Dict, List, Optional
from google.cloud import documentai_v1 as documentai
from google.oauth2 import service_account
from config import (
    GOOGLE_CLOUD_PROJECT_ID,
    GOOGLE_CLOUD_LOCATION,
    GOOGLE_DOCAI_PROCESSOR_ID,
    GOOGLE_APPLICATION_CREDENTIALS_JSON
)

logger = logging.getLogger(__name__)

# Создаём клиент Document AI
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


async def recognize_invoice(image_path: str) -> Dict:
    """
    Распознать накладную с фото с помощью Google Document AI

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
        # Читаем изображение
        with open(image_path, 'rb') as f:
            image_content = f.read()

        logger.info("🔍 Отправляю накладную в Google Document AI...")

        # Создаём клиент
        client = get_docai_client()

        # Формируем имя процессора
        processor_name = client.processor_path(
            GOOGLE_CLOUD_PROJECT_ID,
            GOOGLE_CLOUD_LOCATION,
            GOOGLE_DOCAI_PROCESSOR_ID
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
        result = client.process_document(request=request)
        document = result.document

        logger.info(f"📄 Документ обработан Document AI")

        # Извлекаем данные из entities
        supplier_name = None
        invoice_date = None
        total_sum = None
        items = []

        # Обрабатываем entities
        for entity in document.entities:
            entity_type = entity.type_

            # Поставщик
            if entity_type in ['supplier_name', 'remit_to_name', 'vendor_name']:
                supplier_name = entity.mention_text

            # Дата
            elif entity_type in ['invoice_date', 'invoice_receipt_date']:
                # Document AI возвращает дату, нужно преобразовать
                date_text = entity.mention_text
                invoice_date = _parse_date(date_text)

            # Общая сумма
            elif entity_type in ['total_amount', 'net_amount']:
                total_sum = _parse_amount(entity.mention_text)

            # Позиции товаров
            elif entity_type == 'line_item':
                item = _extract_line_item(entity)
                if item:
                    items.append(item)

        # Если нет line_items, пробуем извлечь из таблиц
        if not items:
            logger.info("📋 Line items не найдены, пробую извлечь из таблиц...")
            items = _extract_items_from_tables(document)

        # Нормализуем единицы измерения
        for item in items:
            if 'unit' not in item or not item['unit']:
                item['unit'] = 'шт'
            item['unit'] = item['unit'].lower().strip()

            # Вычисляем итог по позиции
            item['total'] = item['quantity'] * item['price']

        result = {
            'success': True,
            'supplier_name': supplier_name,
            'invoice_date': invoice_date,
            'total_sum': total_sum,
            'items': items,
            'raw_text': document.text  # Для отладки
        }

        logger.info(
            f"✅ Накладная распознана: поставщик={result['supplier_name']}, "
            f"дата={result['invoice_date']}, товаров={len(items)}"
        )

        return result

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


def _extract_line_item(entity: documentai.Document.Entity) -> Optional[Dict]:
    """Извлечь данные позиции товара из entity"""
    item = {
        'name': None,
        'quantity': 0.0,
        'unit': 'шт',
        'price': 0.0
    }

    # Обрабатываем свойства line_item
    for prop in entity.properties:
        prop_type = prop.type_

        if prop_type in ['line_item/description', 'line_item/product_code']:
            item['name'] = prop.mention_text
        elif prop_type == 'line_item/quantity':
            item['quantity'] = _parse_amount(prop.mention_text)
        elif prop_type == 'line_item/unit_price':
            item['price'] = _parse_amount(prop.mention_text)
        elif prop_type == 'line_item/unit':
            item['unit'] = prop.mention_text

    # Валидация: должны быть хотя бы название и цена
    if item['name'] and item['price'] > 0:
        if item['quantity'] == 0:
            item['quantity'] = 1.0
        return item

    return None


def _extract_items_from_tables(document: documentai.Document) -> List[Dict]:
    """Извлечь товары из таблиц документа"""
    items = []

    for page in document.pages:
        for table in page.tables:
            # Пропускаем заголовок (первая строка)
            for row_idx, row in enumerate(table.body_rows):
                if row_idx == 0:
                    continue  # Скип заголовка

                # Извлекаем данные из колонок
                # Обычно: № | Название | ... | Кол-во | Цена | ...
                cells = row.cells
                if len(cells) < 4:
                    continue

                item = {
                    'name': _get_cell_text(cells[1], document),  # Колонка 2 - название
                    'quantity': 1.0,
                    'unit': 'шт',
                    'price': 0.0
                }

                # Ищем количество и цену
                for cell in cells[2:]:
                    text = _get_cell_text(cell, document).strip()

                    # Пробуем распарсить как число
                    amount = _parse_amount(text)
                    if amount > 0:
                        # Эвристика: если < 1000 и есть дробная часть - скорее всего количество
                        if amount < 1000 and ('.' in text or ',' in text):
                            item['quantity'] = amount
                        # Иначе если > 10 - скорее всего цена
                        elif amount > 10:
                            item['price'] = amount

                # Валидация
                if item['name'] and item['price'] > 0:
                    items.append(item)

    return items


def _get_cell_text(cell: documentai.Document.Page.Table.TableCell, document: documentai.Document) -> str:
    """Получить текст из ячейки таблицы"""
    text = ""
    for segment in cell.layout.text_anchor.text_segments:
        start_index = int(segment.start_index) if hasattr(segment, 'start_index') else 0
        end_index = int(segment.end_index) if hasattr(segment, 'end_index') else 0
        text += document.text[start_index:end_index]
    return text.strip()


def _parse_amount(text: str) -> float:
    """Распарсить сумму из текста"""
    if not text:
        return 0.0

    # Удаляем пробелы и заменяем запятую на точку
    text = text.strip().replace(' ', '').replace(',', '.')

    # Убираем валюту и другие символы
    text = ''.join(c for c in text if c.isdigit() or c == '.')

    try:
        return float(text)
    except ValueError:
        return 0.0


def _parse_date(text: str) -> str:
    """Распарсить дату в формат YYYY-MM-DD"""
    from datetime import datetime

    # Пробуем разные форматы
    formats = [
        '%d.%m.%Y',
        '%d/%m/%Y',
        '%Y-%m-%d',
        '%d-%m-%Y',
        '%m/%d/%Y'
    ]

    for fmt in formats:
        try:
            dt = datetime.strptime(text.strip(), fmt)
            return dt.strftime('%Y-%m-%d')
        except ValueError:
            continue

    # Если не получилось - возвращаем как есть
    return text


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
