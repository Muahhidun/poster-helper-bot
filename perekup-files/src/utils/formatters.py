from decimal import Decimal
from datetime import date, datetime
from typing import Union


def format_money(amount: Union[Decimal, int, float]) -> str:
    """
    Форматирование денег в KZT

    Пример: 1234567.89 -> "1 234 567.89 ₸"
    """
    if amount is None:
        return "0 ₸"

    amount_decimal = Decimal(str(amount))

    # Форматируем с пробелами между тысячами
    integer_part = int(amount_decimal)
    decimal_part = amount_decimal - integer_part

    # Форматирование целой части с пробелами
    formatted = f"{integer_part:,}".replace(",", " ")

    # Добавляем дробную часть если есть
    if decimal_part > 0:
        formatted += f"{decimal_part:.2f}"[1:]  # Убираем "0" в начале

    return f"{formatted} ₸"


def format_date(d: Union[date, datetime]) -> str:
    """
    Форматирование даты

    Пример: 2025-11-09 -> "09.11.2025"
    """
    if isinstance(d, datetime):
        d = d.date()
    return d.strftime("%d.%m.%Y")


def format_date_short(d: Union[date, datetime]) -> str:
    """
    Форматирование даты (короткий формат)

    Пример: 2025-11-09 -> "09.11"
    """
    if isinstance(d, datetime):
        d = d.date()
    return d.strftime("%d.%m")


def parse_money(text: str) -> Decimal:
    """
    Парсинг суммы из текста

    Примеры:
    "150 000" -> 150000
    "150000" -> 150000
    "150k" -> 150000
    "1.5m" -> 1500000
    """
    text = text.strip().lower().replace(" ", "").replace(",", "")

    # Обработка k, m суффиксов
    if text.endswith("k"):
        return Decimal(text[:-1]) * 1000
    elif text.endswith("m") or text.endswith("м"):
        return Decimal(text[:-1]) * 1000000

    return Decimal(text)


def parse_date(text: str) -> date:
    """
    Парсинг даты из текста

    Поддерживает форматы:
    - "09.11.2025"
    - "09.11" (текущий год)
    - "сегодня"
    """
    text = text.strip().lower()

    if text == "сегодня":
        return date.today()

    # Попытка парсинга разных форматов
    formats = ["%d.%m.%Y", "%d.%m", "%Y-%m-%d"]

    for fmt in formats:
        try:
            parsed = datetime.strptime(text, fmt)
            # Если год не указан, используем текущий
            if fmt == "%d.%m":
                parsed = parsed.replace(year=datetime.now().year)
            return parsed.date()
        except ValueError:
            continue

    raise ValueError(f"Не удалось распознать дату: {text}")
