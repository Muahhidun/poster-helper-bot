"""Модуль для распознавания чеков с помощью OCR (GPT-4 Vision)"""
import base64
import logging
from datetime import datetime
from typing import Dict, Optional
from openai import OpenAI
from config import OPENAI_API_KEY

logger = logging.getLogger(__name__)

# Создаём клиент OpenAI
client = OpenAI(api_key=OPENAI_API_KEY)


async def recognize_receipt(image_path: str) -> Dict:
    """
    Распознать чек с фото с помощью GPT-4 Vision

    Args:
        image_path: Путь к файлу с фото чека

    Returns:
        Dict с распознанными данными:
        - date: дата в формате YYYY-MM-DD
        - time: время в формате HH:MM
        - amount: сумма в тийинах (int)
        - success: bool
        - error: str (если ошибка)
    """
    try:
        # Читаем изображение и кодируем в base64
        with open(image_path, 'rb') as f:
            image_data = base64.b64encode(f.read()).decode('utf-8')

        # Создаем промпт для GPT-4 Vision
        prompt = """
Проанализируй этот чек из системы Poster POS (кассовый чек ресторана) и извлеки следующую информацию:

1. Дата заказа (в формате YYYY-MM-DD)
2. Время закрытия/оплаты заказа (в формате HH:MM)
3. Итоговая сумма оплаты

ВАЖНО:
- Дата может быть в форматах: "28 октября 2025 19:31" или "10 октября 2025" или "DD.MM.YYYY"
- Время обычно указано рядом с датой в формате "HH:MM" (например "19:31")
- Если дата и время на одной строке - используй их оба
- Сумма указана как "К оплате" или "Оплата наличными" или "Оплата картой" (ищи строку с "тг." или "₸")
- ИГНОРИРУЙ промежуточные суммы и подсчёты позиций
- Ищи ИТОГОВУЮ сумму оплаты (обычно внизу чека)

Верни ТОЛЬКО JSON в таком формате (без дополнительного текста):
{
    "date": "YYYY-MM-DD",
    "time": "HH:MM",
    "amount": сумма_в_тенге_целым_числом
}

Пример ответа:
{
    "date": "2025-10-28",
    "time": "19:31",
    "amount": 650
}
"""

        # Вызов GPT-4 Vision API
        response = client.chat.completions.create(
            model="gpt-4o",  # Используем gpt-4o вместо gpt-4-vision-preview
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
            max_tokens=300
        )

        # Парсим ответ
        result_text = response.choices[0].message.content.strip()

        # Удаляем markdown форматирование если есть
        if result_text.startswith('```'):
            result_text = result_text.split('```')[1]
            if result_text.startswith('json'):
                result_text = result_text[4:]

        # Парсим JSON
        import json
        data = json.loads(result_text)

        # Конвертируем сумму в тийины (если еще не в тийинах)
        amount = int(data['amount'])
        if amount < 100000:  # Если сумма выглядит как тенге (< 100к), умножаем на 100
            amount = amount * 100

        result = {
            'success': True,
            'date': data['date'],
            'time': data['time'],
            'amount': amount,
            'raw_response': result_text
        }

        logger.info(
            f"✅ Чек распознан: дата={result['date']}, "
            f"время={result['time']}, сумма={result['amount']/100:,.0f}₸"
        )

        return result

    except Exception as e:
        logger.error(f"❌ Ошибка распознавания чека: {e}", exc_info=True)
        return {
            'success': False,
            'error': str(e)
        }


def parse_date_time(date_str: str, time_str: str) -> datetime:
    """
    Преобразовать дату и время в datetime объект

    Args:
        date_str: Дата в формате "YYYY-MM-DD"
        time_str: Время в формате "HH:MM"

    Returns:
        datetime объект
    """
    return datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")


if __name__ == "__main__":
    import asyncio
    import sys

    if len(sys.argv) < 2:
        print("Использование: python receipt_ocr.py <путь_к_фото_чека>")
        sys.exit(1)

    image_path = sys.argv[1]

    async def test():
        print(f"🔍 Распознаём чек: {image_path}")
        print("=" * 70)
        print()

        result = await recognize_receipt(image_path)

        if result['success']:
            print("✅ Чек успешно распознан!")
            print()
            print(f"📅 Дата: {result['date']}")
            print(f"🕐 Время: {result['time']}")
            print(f"💰 Сумма: {result['amount']/100:,.2f}₸ ({result['amount']} тийинов)")
        else:
            print(f"❌ Ошибка: {result['error']}")

        print()
        print("=" * 70)

    asyncio.run(test())
