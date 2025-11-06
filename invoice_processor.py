"""Обработчик накладных с использованием GPT-4 Vision OCR"""
import logging
import re
from typing import Dict, List, Optional, Tuple
import invoice_ocr
from poster_client import PosterClient
from matchers import SupplierMatcher, IngredientMatcher

logger = logging.getLogger(__name__)


class InvoiceProcessor:
    """Обработчик накладных с автоматическим распознаванием через GPT-4 Vision"""

    def __init__(self, telegram_user_id: int):
        """
        Инициализация процессора

        Args:
            telegram_user_id: ID пользователя Telegram
        """
        self.telegram_user_id = telegram_user_id
        self.poster_client = PosterClient(telegram_user_id)
        self.supplier_matcher = SupplierMatcher(telegram_user_id)
        self.ingredient_matcher = IngredientMatcher(telegram_user_id)

    async def close(self):
        """Закрыть все клиенты"""
        await self.poster_client.close()

    async def process_invoice_photo(
        self,
        photo_file_id: str,
        bot_token: str
    ) -> Dict:
        """
        Обработать фото накладной и создать черновик поставки

        Args:
            photo_file_id: ID фото в Telegram
            bot_token: Токен Telegram бота

        Returns:
            Результат обработки с черновиком поставки
        """
        ocr_result = None
        parsed_data = None

        try:
            # 1. Получить URL изображения из Telegram
            logger.info("📸 Получаю изображение из Telegram...")
            try:
                image_url = await self._get_telegram_file_url(photo_file_id, bot_token)
            except Exception as e:
                raise Exception(f"[ШАГ 1] Ошибка получения изображения: {e}")

            # 2. Обработать через GPT-4 Vision OCR
            logger.info("🤖 Отправляю изображение в GPT-4 Vision...")
            try:
                ocr_result = await invoice_ocr.recognize_invoice_from_url(image_url)

                if not ocr_result.get('success'):
                    raise Exception(f"GPT-4 Vision не смог распознать: {ocr_result.get('error')}")

                logger.info(f"✅ GPT-4 Vision распознал накладную: товаров={len(ocr_result.get('items', []))}")
            except Exception as e:
                raise Exception(f"[ШАГ 1 - Распознавание] {e}")

            # 3. Обработать распознанные данные
            logger.info("📋 Обрабатываю распознанные данные...")
            try:
                parsed_data = self._process_ocr_result(ocr_result)
                logger.info(f"✅ Обработано: поставщик={parsed_data.get('supplier_name')}, товаров={len(parsed_data.get('items', []))}")
            except Exception as e:
                raise Exception(f"[ШАГ 2 - Обработка данных] {e}")

            # 4. Создать черновик поставки в Poster
            logger.info("📦 Создаю черновик поставки в Poster...")
            try:
                supply_draft = await self._create_supply_draft(parsed_data)
            except Exception as e:
                raise Exception(f"[ШАГ 3 - Создание черновика] {e}")

            return {
                'success': True,
                'ocr_result': ocr_result,
                'parsed_data': parsed_data,
                'supply_draft': supply_draft
            }

        except Exception as e:
            logger.error(f"❌ Ошибка обработки накладной: {e}")
            return {
                'success': False,
                'error': str(e),
                'ocr_result': ocr_result,
                'parsed_data': parsed_data
            }

    async def _get_telegram_file_url(self, file_id: str, bot_token: str) -> str:
        """
        Получить URL файла из Telegram

        Args:
            file_id: ID файла в Telegram
            bot_token: Токен бота

        Returns:
            URL файла
        """
        import aiohttp

        async with aiohttp.ClientSession() as session:
            # Получаем информацию о файле
            url = f"https://api.telegram.org/bot{bot_token}/getFile?file_id={file_id}"
            async with session.get(url) as response:
                if response.status != 200:
                    raise Exception(f"Failed to get file info: {response.status}")

                data = await response.json()
                file_path = data['result']['file_path']

            # Формируем URL для скачивания
            file_url = f"https://api.telegram.org/file/bot{bot_token}/{file_path}"
            return file_url

    def _process_ocr_result(self, ocr_result: Dict) -> Dict:
        """
        Обработать результат распознавания от GPT-4 Vision

        Args:
            ocr_result: Результат от invoice_ocr.recognize_invoice_from_url()

        Returns:
            Распарсованные данные накладной в формате для создания поставки
        """
        # Получаем поставщика и ищем его ID
        supplier_name = ocr_result.get('supplier_name')
        supplier_id = None
        if supplier_name:
            supplier_id = self.supplier_matcher.match(supplier_name)

        # Дата накладной
        invoice_date = ocr_result.get('invoice_date')

        # Общая сумма
        total_sum = ocr_result.get('total_sum')

        # Обрабатываем товары
        items = []
        for item in ocr_result.get('items', []):
            # Ищем ингредиент в Poster по названию
            match_result = self.ingredient_matcher.match(item['name'])

            # Извлекаем только ID из кортежа (ingredient_id, name, unit, score)
            ingredient_id = match_result[0] if match_result else None

            processed_item = {
                'name': item['name'],
                'ingredient_id': ingredient_id,
                'quantity': item['quantity'],
                'unit': item['unit'],
                'price': item['price'],
                'total': item.get('total', item['quantity'] * item['price'])
            }
            items.append(processed_item)

            if ingredient_id:
                logger.debug(f"  ✓ Товар сопоставлен: {item['name']} -> ingredient_id={ingredient_id}")
            else:
                logger.debug(f"  ✗ Товар не найден в Poster: {item['name']}")

        return {
            'supplier_name': supplier_name,
            'supplier_id': supplier_id,
            'invoice_date': invoice_date,
            'total_sum': total_sum,
            'items': items
        }

    async def _create_supply_draft(self, parsed_data: Dict) -> Dict:
        """
        Создать черновик поставки в Poster

        Args:
            parsed_data: Распарсованные данные накладной

        Returns:
            Данные созданного черновика
        """
        from datetime import datetime
        from config import DEFAULT_WAREHOUSE_ID, DEFAULT_ACCOUNT_FROM_ID

        supplier_id = parsed_data.get('supplier_id')
        supplier_not_found = False

        # Если поставщик не найден, используем первого доступного из CSV
        if not supplier_id:
            supplier_not_found = True
            logger.warning(f"⚠️ Поставщик '{parsed_data.get('supplier_name')}' не найден, используем дефолтного")

            # Получаем поставщиков из локального CSV файла
            if self.supplier_matcher.suppliers:
                # Берём первого поставщика из списка
                first_supplier = list(self.supplier_matcher.suppliers.values())[0]
                supplier_id = first_supplier['id']
                logger.info(f"📦 Использую дефолтного поставщика: {first_supplier['name']} (ID={supplier_id})")
            else:
                raise Exception("Не найдено ни одного поставщика в базе данных (poster_suppliers.csv пуст)")

        items = parsed_data.get('items', [])
        if not items:
            raise Exception("Не найдено ни одного товара в накладной")

        # Подготовить ингредиенты для Poster API
        # НЕ объединяем дубликаты - каждая позиция из накладной отдельная!
        ingredients_for_poster = []
        added_items = []
        skipped_items = []

        for item in items:
            if not item.get('ingredient_id'):
                logger.warning(f"⚠️ Пропускаю '{item['name']}' - ингредиент не найден в Poster")
                skipped_items.append(item['name'])
                continue

            # Добавляем каждую позицию отдельно (без объединения)
            ingredients_for_poster.append({
                'id': item['ingredient_id'],
                'num': item['quantity'],
                'price': item['price']
            })

            added_items.append({
                'name': item['name'],
                'quantity': item['quantity'],
                'unit': item['unit'],
                'price': item['price'],
                'total': item['quantity'] * item['price']
            })

            logger.info(f"  ✓ {item['name']}: {item['quantity']} {item['unit']} x {item['price']}₸")

        if not ingredients_for_poster:
            raise Exception("Ни один товар не был сопоставлен с ингредиентами в Poster")

        # Дата поставки - ВСЕГДА текущая (фактическая)
        supply_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # Создаём поставку через правильный метод API
        logger.info(f"📦 Создаю поставку: поставщик={supplier_id}, товаров={len(ingredients_for_poster)}")

        supply_id = await self.poster_client.create_supply(
            supplier_id=supplier_id,
            storage_id=DEFAULT_WAREHOUSE_ID,
            date=supply_date,
            ingredients=ingredients_for_poster,
            account_id=DEFAULT_ACCOUNT_FROM_ID,
            comment=f"Накладная от {parsed_data.get('supplier_name', 'Неизвестно')}"
        )

        logger.info(f"✅ Создана поставка #{supply_id}")

        # Save price history for smart monitoring
        try:
            from database import get_database
            db = get_database()

            # Get supplier name
            supplier_name = parsed_data.get('supplier_name', 'Неизвестно')

            # Prepare price history records
            price_records = []
            for item in added_items:
                # Find ingredient_id for this item
                ingredient_id = None
                for ing in ingredients_for_poster:
                    # Match by checking if the item name corresponds to this ingredient
                    # We need to find the original item to get ingredient_id
                    pass

                # Actually, we need to iterate through original items with ingredient_id
                for original_item in items:
                    if (original_item.get('ingredient_id') and
                        original_item['name'] == item['name'] and
                        original_item['quantity'] == item['quantity'] and
                        original_item['price'] == item['price']):

                        price_records.append({
                            'ingredient_id': original_item['ingredient_id'],
                            'ingredient_name': original_item['name'],
                            'supplier_id': supplier_id,
                            'supplier_name': supplier_name,
                            'date': supply_date.split()[0],  # Extract date part only
                            'price': original_item['price'],
                            'quantity': original_item['quantity'],
                            'unit': original_item.get('unit', ''),
                            'supply_id': supply_id
                        })
                        break

            # Bulk save price history
            if price_records:
                saved_count = db.bulk_add_price_history(self.telegram_user_id, price_records)
                logger.info(f"💾 Saved {saved_count} price records to history")

        except Exception as e:
            # Don't fail the supply creation if price history fails
            logger.error(f"⚠️ Failed to save price history: {e}")

        return {
            'supply_id': supply_id,
            'supplier_name': parsed_data.get('supplier_name'),
            'supplier_id': supplier_id,
            'supplier_not_found': supplier_not_found,
            'date': supply_date,
            'items_count': len(added_items),
            'items': added_items,
            'skipped_items': skipped_items,
            'total_sum': sum(item.get('total', 0) for item in added_items),
            # Данные для активации через update_supply
            'storage_id': DEFAULT_WAREHOUSE_ID,
            'account_id': DEFAULT_ACCOUNT_FROM_ID,
            'ingredients_for_api': ingredients_for_poster,
            'comment': f"Накладная от {parsed_data.get('supplier_name', 'Неизвестно')}"
        }
