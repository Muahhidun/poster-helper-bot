"""Обработчик накладных с использованием Pokee AI"""
import logging
import re
from typing import Dict, List, Optional, Tuple
from pokee_client import PokeeClient
from poster_client import PosterClient
from matchers import SupplierMatcher, IngredientMatcher

logger = logging.getLogger(__name__)


class InvoiceProcessor:
    """Обработчик накладных с автоматическим распознаванием через Pokee AI"""

    def __init__(self, telegram_user_id: int):
        """
        Инициализация процессора

        Args:
            telegram_user_id: ID пользователя Telegram
        """
        self.telegram_user_id = telegram_user_id
        self.pokee_client = PokeeClient()
        self.poster_client = PosterClient(telegram_user_id)
        self.supplier_matcher = SupplierMatcher(telegram_user_id)
        self.ingredient_matcher = IngredientMatcher(telegram_user_id)

    async def close(self):
        """Закрыть все клиенты"""
        await self.pokee_client.close()
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
        try:
            # 1. Получить URL изображения из Telegram
            logger.info("📸 Получаю изображение из Telegram...")
            image_url = await self.pokee_client.upload_image_to_telegram(
                photo_file_id,
                bot_token
            )

            # 2. Обработать через Pokee AI
            logger.info("🤖 Отправляю изображение в Pokee AI...")
            pokee_result = await self.pokee_client.process_invoice_image(image_url)

            if pokee_result.get('status') == 'error':
                raise Exception(f"Pokee AI error: {pokee_result.get('error')}")

            formatted_text = pokee_result.get('formatted_text', '')
            logger.info(f"✅ Pokee AI вернул текст ({len(formatted_text)} символов)")

            # 3. Распарсить отформатированный текст
            logger.info("📋 Парсинг распознанного текста...")
            parsed_data = self._parse_pokee_response(formatted_text)

            # 4. Создать черновик поставки в Poster
            logger.info("📦 Создаю черновик поставки в Poster...")
            supply_draft = await self._create_supply_draft(parsed_data)

            return {
                'success': True,
                'formatted_text': formatted_text,
                'parsed_data': parsed_data,
                'supply_draft': supply_draft
            }

        except Exception as e:
            logger.error(f"❌ Ошибка обработки накладной: {e}")
            return {
                'success': False,
                'error': str(e)
            }

    def _parse_pokee_response(self, formatted_text: str) -> Dict:
        """
        Распарсить ответ от Pokee AI

        Формат ожидается примерно такой:
        ```
        Поставщик: Япоша
        Дата: 01.11.2025
        Сумма: 15000

        Товары:
        1. Лук репчатый - 10 кг - 500₸
        2. Картофель - 20 кг - 1000₸
        3. Морковь - 5 кг - 300₸
        ```

        Args:
            formatted_text: Отформатированный текст от Pokee AI

        Returns:
            Распарсованные данные накладной
        """
        lines = formatted_text.strip().split('\n')

        # Извлекаем поставщика
        supplier_name = None
        supplier_id = None
        for line in lines:
            if line.lower().startswith('поставщик:'):
                supplier_name = line.split(':', 1)[1].strip()
                supplier_id = self.supplier_matcher.match(supplier_name)
                break

        # Извлекаем дату
        invoice_date = None
        for line in lines:
            if line.lower().startswith('дата:'):
                date_str = line.split(':', 1)[1].strip()
                # Парсим дату (формат: DD.MM.YYYY или YYYY-MM-DD)
                invoice_date = self._parse_date(date_str)
                break

        # Извлекаем общую сумму
        total_sum = None
        for line in lines:
            if line.lower().startswith('сумма:'):
                sum_str = line.split(':', 1)[1].strip()
                # Убираем валюту и пробелы
                sum_str = re.sub(r'[^\d.]', '', sum_str)
                if sum_str:
                    total_sum = float(sum_str)
                break

        # Извлекаем товары
        items = []
        in_items_section = False

        for line in lines:
            line = line.strip()

            # Начало секции товаров
            if line.lower() in ['товары:', 'продукты:', 'items:']:
                in_items_section = True
                continue

            if not in_items_section or not line:
                continue

            # Парсинг строки товара
            # Форматы:
            # "1. Лук репчатый - 10 кг - 500₸"
            # "Картофель 20кг 1000"
            # "Морковь | 5 кг | 300₸"

            item = self._parse_item_line(line)
            if item:
                items.append(item)

        return {
            'supplier_name': supplier_name,
            'supplier_id': supplier_id,
            'invoice_date': invoice_date,
            'total_sum': total_sum,
            'items': items
        }

    def _parse_item_line(self, line: str) -> Optional[Dict]:
        """
        Распарсить строку товара

        Args:
            line: Строка с данными товара

        Returns:
            Данные товара или None
        """
        # Убираем номер строки в начале
        line = re.sub(r'^\d+[\.\)]\s*', '', line)

        # Пробуем различные паттерны
        patterns = [
            # "Лук репчатый - 10 кг - 500₸"
            r'(.+?)\s*-\s*([\d.]+)\s*(кг|г|л|мл|шт)\s*-\s*([\d.]+)',
            # "Картофель 20кг 1000"
            r'(.+?)\s+([\d.]+)\s*(кг|г|л|мл|шт)\s+([\d.]+)',
            # "Морковь | 5 кг | 300"
            r'(.+?)\s*\|\s*([\d.]+)\s*(кг|г|л|мл|шт)\s*\|\s*([\d.]+)',
        ]

        for pattern in patterns:
            match = re.search(pattern, line, re.IGNORECASE)
            if match:
                name = match.group(1).strip()
                quantity = float(match.group(2))
                unit = match.group(3).lower()
                price = float(match.group(4))

                # Поиск ингредиента в Poster
                ingredient_id = self.ingredient_matcher.match(name)

                return {
                    'name': name,
                    'ingredient_id': ingredient_id,
                    'quantity': quantity,
                    'unit': unit,
                    'price': price,
                    'total': quantity * price
                }

        logger.warning(f"Не удалось распарсить строку товара: {line}")
        return None

    def _parse_date(self, date_str: str) -> Optional[str]:
        """
        Распарсить дату из строки

        Args:
            date_str: Строка с датой

        Returns:
            Дата в формате YYYY-MM-DD или None
        """
        from datetime import datetime

        formats = [
            '%d.%m.%Y',  # 01.11.2025
            '%Y-%m-%d',  # 2025-11-01
            '%d/%m/%Y',  # 01/11/2025
        ]

        for fmt in formats:
            try:
                dt = datetime.strptime(date_str.strip(), fmt)
                return dt.strftime('%Y-%m-%d')
            except ValueError:
                continue

        logger.warning(f"Не удалось распарсить дату: {date_str}")
        return None

    async def _create_supply_draft(self, parsed_data: Dict) -> Dict:
        """
        Создать черновик поставки в Poster

        Args:
            parsed_data: Распарсованные данные накладной

        Returns:
            Данные созданного черновика
        """
        supplier_id = parsed_data.get('supplier_id')
        supplier_not_found = False

        # Если поставщик не найден, используем первого доступного
        if not supplier_id:
            supplier_not_found = True
            logger.warning(f"⚠️ Поставщик '{parsed_data.get('supplier_name')}' не найден, используем дефолтного")

            # Получаем список поставщиков и берём первого
            suppliers_result = await self.poster_client._request('GET', 'storage.getSuppliers')
            suppliers = suppliers_result.get('response', [])

            if suppliers:
                supplier_id = int(suppliers[0]['supplier_id'])
                logger.info(f"📦 Использую дефолтного поставщика: {suppliers[0]['supplier_name']}")
            else:
                raise Exception("Не найдено ни одного поставщика в Poster")

        items = parsed_data.get('items', [])
        if not items:
            raise Exception("Не найдено ни одного товара в накладной")

        # Подготовить данные для Poster API
        from datetime import datetime
        supply_date = parsed_data.get('invoice_date') or datetime.now().strftime('%Y-%m-%d')

        # Создать поставку
        supply_data = {
            'supplier_id': supplier_id,
            'type': 1,  # Приход
            'date': supply_date,
            'status': 0,  # Черновик
        }

        # Создаём поставку
        supply_result = await self.poster_client._request('POST', 'supply.createIncomingOrder', data=supply_data)
        supply_id = supply_result.get('incoming_order_id')

        logger.info(f"✅ Создан черновик поставки #{supply_id}")

        # Добавляем товары
        added_items = []
        for item in items:
            if not item.get('ingredient_id'):
                logger.warning(f"⚠️ Пропускаю '{item['name']}' - ингредиент не найден в Poster")
                continue

            item_data = {
                'incoming_order_id': supply_id,
                'product_id': item['ingredient_id'],
                'type': 'ingredient',
                'num': item['quantity'],
                'cost': item['price']
            }

            try:
                await self.poster_client._request('POST', 'supply.createIncomingOrderProduct', data=item_data)
                added_items.append(item)
                logger.info(f"  ✓ {item['name']}: {item['quantity']} {item['unit']} x {item['price']}₸")
            except Exception as e:
                logger.error(f"  ✗ Ошибка добавления '{item['name']}': {e}")

        return {
            'supply_id': supply_id,
            'supplier_name': parsed_data.get('supplier_name'),
            'supplier_id': supplier_id,
            'supplier_not_found': supplier_not_found,
            'date': supply_date,
            'items_count': len(added_items),
            'items': added_items,
            'total_sum': sum(item.get('total', 0) for item in added_items)
        }
