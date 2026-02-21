"""Tests for supply text parser (quantities, prices, unit conversions)"""
import pytest


@pytest.mark.parametrize("input_text,expected_qty,expected_price", [
    # Simple: unit price specified
    ("Поставка Айсберг 2.2 кг по 1600", 2.2, 1600),
    # Total price: "за" keyword (3350 / 2.5 = 1340)
    ("Поставка Фри 2.5 кг за 3350", 2.5, 1340),
    # Packages: multiply quantity (5 * 4 = 20 кг)
    ("Поставка 5 упаковок по 4 кг по 1500", 20, 1500),
    # Tare: subtract from quantity (11 - 0.5 = 10.5)
    ("Поставка Помидоры 11 кг минус 500 грамм по 850", 10.5, 850),
    # Gram conversion (250г = 0.25кг)
    ("Поставка Специи 250 г по 500", 0.25, 500),
])
def test_single_item_parsing(simple_parser, input_text, expected_qty, expected_price):
    """Test parsing of single supply items with various formats"""
    result = simple_parser.parse_supply(input_text)
    assert result is not None, f"Parser returned None for: {input_text}"

    items = result.get('items', [])
    assert len(items) > 0, f"No items parsed from: {input_text}"

    item = items[0]
    assert abs(item['qty'] - expected_qty) < 0.01, \
        f"qty mismatch: expected {expected_qty}, got {item['qty']}"
    assert abs(item['price'] - expected_price) < 0.01, \
        f"price mismatch: expected {expected_price}, got {item['price']}"


def test_multiple_items_parsing(simple_parser):
    """Test parsing text with multiple supply items"""
    text = "Поставка поставщик Астана: Фри 2.5 кг за 3350, Айсберг 2 кг по 1600"
    result = simple_parser.parse_supply(text)
    assert result is not None, "Parser returned None for multi-item input"

    items = result.get('items', [])
    assert len(items) == 2, f"Expected 2 items, got {len(items)}"
