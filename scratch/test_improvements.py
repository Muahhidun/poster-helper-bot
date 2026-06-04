import os
# Mock keys for testing parser without real APIs
os.environ["OPENAI_API_KEY"] = "mock_key"
os.environ["GEMINI_API_KEY"] = "mock_key"
os.environ["TELEGRAM_BOT_TOKEN"] = "mock_key"

import asyncio
import logging
from matchers import SupplierMatcher
from parser_service import get_parser_service

# Setup logging to console
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

def test_supplier_matcher():
    print("\n=== Testing Supplier Matching ===")
    
    # Initialize supplier matcher with global data
    matcher = SupplierMatcher()
    
    # Test cases: (Input string, expected matched supplier name or None)
    test_cases = [
        # Resto-bro test cases
        ('Товарищество с ограниченной ответственностью "Resto-bro"', 'Ресто бразерс'),
        ('Resto-bro', 'Ресто бразерс'),
        ('Ресто', 'Ресто бразерс'),
        ('ТОО Ресто бразерс', 'Ресто бразерс'),
        # Other test cases
        ('ТОО Инарин', 'Инарин'),
        ('Инарин', 'Инарин'),
        ('товарищество с ограниченной ответственностью инарин', 'Инарин'),
        ('ИртышИнтерФуд', 'Кус Вкус'),
        ('фирма иртышинтерфуд', 'Кус Вкус'),
        # Non-matching cases (should not match anything with cutoff=70)
        ('ТОО "Неизвестный Поставщик"', None),
    ]
    
    for input_text, expected in test_cases:
        matched_id = matcher.match(input_text, score_cutoff=70)
        matched_name = matcher.get_supplier_name(matched_id) if matched_id else None
        status = "PASSED" if matched_name == expected else "FAILED"
        print(f"[{status}] Input: '{input_text}' -> Matched: '{matched_name}' (Expected: '{expected}')")

def test_mathematical_reconciliation():
    print("\n=== Testing Mathematical Reconciliation ===")
    parser = get_parser_service()
    
    # Scenario: OCR misidentified quantity as 1.0, price is 1820.0, but sum is 14560.0 (which is 8 * 1820)
    mock_parsed_invoice = {
        "document_type": "printed_invoice",
        "invoice": {
            "supplier": "Resto-bro",
            "total_sum": 14560.0,
            "items": [
                {
                    "name": "Ветчина смолл",
                    "qty": 1.0,
                    "price": 1820.0,
                    "sum": 14560.0
                }
            ]
        }
    }
    
    reconciled = parser._reconcile_invoice_items(mock_parsed_invoice)
    item = reconciled["invoice"]["items"][0]
    
    status = "PASSED" if item["qty"] == 8.0 else "FAILED"
    print(f"[{status}] Quantity corrected: {item['qty']} (Expected: 8.0), price: {item['price']}, sum: {item['sum']}")
    
    # Scenario: total_sum is missing but we have items
    mock_missing_total = {
        "type": "supply",
        "items": [
            {"name": "Фри", "qty": 10.0, "price": 2100.0, "sum": 21000.0},
            {"name": "Сыр", "qty": 5.0, "price": 3000.0, "sum": 15000.0}
        ]
    }
    reconciled_supply = parser._reconcile_invoice_items(mock_missing_total)
    
    # We expect total_sum to be calculated as 36000.0
    status_total = "PASSED" if reconciled_supply.get("total_sum") == 36000.0 or reconciled_supply.get("total") == 36000.0 else "FAILED"
    print(f"[{status_total}] Total sum computed: {reconciled_supply.get('total_sum')} (Expected: 36000.0)")

if __name__ == "__main__":
    test_supplier_matcher()
    test_mathematical_reconciliation()
