"""Клиент для Pokee AI API"""
import logging
import aiohttp
import json
from typing import Optional, Dict, Any
import config

logger = logging.getLogger(__name__)


class PokeeClient:
    """Клиент для взаимодействия с Pokee AI API"""

    API_URL = "https://api.pokee.ai/run-workflow-api"
    WORKFLOW_ID = 54670  # ID вашего workflow для обработки накладных

    def __init__(self):
        """Инициализация клиента"""
        self.api_token = config.POKEE_API_TOKEN
        self.session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Получить или создать aiohttp сессию"""
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session

    async def close(self):
        """Закрыть сессию"""
        if self.session and not self.session.closed:
            await self.session.close()

    async def process_invoice_image(
        self,
        image_url: str,
        chat_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Обработать изображение накладной через Pokee AI

        Args:
            image_url: URL изображения или путь к файлу
            chat_id: ID чата для контекста (опционально)

        Returns:
            Распознанные данные накладной
        """
        try:
            session = await self._get_session()

            # Генерируем уникальный chat_id если не передан
            if not chat_id:
                import uuid
                chat_id = str(uuid.uuid4())

            # Подготовка запроса
            headers = {
                'Content-Type': 'application/json',
                'Accept': 'text/event-stream',
                'Authorization': f'Bearer {self.api_token}'
            }

            payload = {
                "seed_workflow_id": self.WORKFLOW_ID,
                "chat_id": chat_id,
                "input_data": {
                    "invoice_image": image_url
                },
                "stream": True
            }

            logger.info(f"Отправка изображения в Pokee AI: {image_url}")

            # Отправка запроса с обработкой SSE (Server-Sent Events)
            async with session.post(self.API_URL, headers=headers, json=payload) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"Ошибка Pokee AI API: {response.status} - {error_text}")
                    raise Exception(f"Pokee AI API error: {response.status}")

                # Обработка потока SSE событий
                result_data = {}
                async for line in response.content:
                    line = line.decode('utf-8').strip()

                    if not line or line.startswith(':'):
                        continue

                    # Парсинг SSE формата: "data: {...}"
                    if line.startswith('data: '):
                        data_str = line[6:]  # Убираем "data: "

                        try:
                            event_data = json.loads(data_str)

                            # Логируем события
                            event_type = event_data.get('type')
                            logger.debug(f"Pokee AI event: {event_type}")

                            # Собираем результат
                            if event_type == 'content':
                                content = event_data.get('content', '')
                                if 'formatted_text' not in result_data:
                                    result_data['formatted_text'] = ''
                                result_data['formatted_text'] += content

                            elif event_type == 'workflow_finished':
                                result_data['status'] = 'completed'
                                result_data['workflow_result'] = event_data.get('result', {})

                            elif event_type == 'error':
                                result_data['status'] = 'error'
                                result_data['error'] = event_data.get('error', 'Unknown error')

                        except json.JSONDecodeError as e:
                            logger.warning(f"Не удалось распарсить SSE данные: {data_str[:100]}")
                            continue

                logger.info(f"✅ Pokee AI обработал накладную успешно")
                return result_data

        except Exception as e:
            logger.error(f"❌ Ошибка при обработке накладной через Pokee AI: {e}")
            raise

    async def upload_image_to_telegram(
        self,
        file_id: str,
        bot_token: str
    ) -> str:
        """
        Получить URL изображения из Telegram

        Args:
            file_id: ID файла в Telegram
            bot_token: Токен Telegram бота

        Returns:
            URL изображения
        """
        try:
            session = await self._get_session()

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

        except Exception as e:
            logger.error(f"Ошибка получения URL изображения из Telegram: {e}")
            raise
