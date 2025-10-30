"""Test script for advanced supply parser"""
import sys
from simple_parser import get_simple_parser

def test_supply_parser():
    """Test supply parser with examples from ChatGPT instructions"""
    parser = get_simple_parser()

    test_cases = [
        # Simple: unit price specified
        {
            'input': 'Поставка Айсберг 2.2 кг по 1600',
            'expected': {'qty': 2.2, 'price': 1600}
        },
        # Total price: "за" keyword
        {
            'input': 'Поставка Фри 2.5 кг за 3350',
            'expected': {'qty': 2.5, 'price': 1340}  # 3350 / 2.5
        },
        # Packages: multiply quantity
        {
            'input': 'Поставка 5 упаковок по 4 кг по 1500',
            'expected': {'qty': 20, 'price': 1500}  # 5 * 4 = 20 кг
        },
        # Tare: subtract from quantity
        {
            'input': 'Поставка Помидоры 11 кг минус 500 грамм по 850',
            'expected': {'qty': 10.5, 'price': 850}  # 11 - 0.5 = 10.5
        },
        # Gram conversion
        {
            'input': 'Поставка Специи 250 г по 500',
            'expected': {'qty': 0.25, 'price': 500}  # 250г = 0.25кг
        },
        # Multiple items
        {
            'input': 'Поставка поставщик Астана: Фри 2.5 кг за 3350, Айсберг 2 кг по 1600',
            'expected_items': 2
        },
    ]

    print("🧪 Testing Supply Parser\n" + "="*60)

    passed = 0
    failed = 0

    for i, test in enumerate(test_cases, 1):
        print(f"\n📝 Test {i}: {test['input']}")

        result = parser.parse_supply(test['input'])

        if result is None:
            print("   ❌ FAILED: Parser returned None")
            failed += 1
            continue

        if 'expected_items' in test:
            # Test multiple items
            num_items = len(result.get('items', []))
            if num_items == test['expected_items']:
                print(f"   ✅ PASSED: Found {num_items} items")
                passed += 1
            else:
                print(f"   ❌ FAILED: Expected {test['expected_items']} items, got {num_items}")
                failed += 1
        else:
            # Test single item
            items = result.get('items', [])
            if not items:
                print("   ❌ FAILED: No items parsed")
                failed += 1
                continue

            item = items[0]
            expected_qty = test['expected']['qty']
            expected_price = test['expected']['price']

            qty_match = abs(item['qty'] - expected_qty) < 0.01
            price_match = abs(item['price'] - expected_price) < 0.01

            if qty_match and price_match:
                print(f"   ✅ PASSED: qty={item['qty']}, price={item['price']}")
                passed += 1
            else:
                print(f"   ❌ FAILED:")
                print(f"      Expected: qty={expected_qty}, price={expected_price}")
                print(f"      Got:      qty={item['qty']}, price={item['price']}")
                failed += 1

        # Show full result
        print(f"   Result: {result}")

    print("\n" + "="*60)
    print(f"📊 Results: {passed} passed, {failed} failed")

    return failed == 0

if __name__ == '__main__':
    success = test_supply_parser()
    sys.exit(0 if success else 1)
