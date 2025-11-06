"""Test shipment templates functionality"""
import sys
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)

# Test imports
print("Testing imports...")
try:
    from database import get_database
    print("✅ database import OK")
except Exception as e:
    print(f"❌ database import failed: {e}")
    sys.exit(1)

try:
    from shipment_templates import try_parse_quick_template
    print("✅ shipment_templates import OK")
except Exception as e:
    print(f"❌ shipment_templates import failed: {e}")
    sys.exit(1)

# Test quick template parsing
print("\nTesting quick template parsing...")

test_cases = [
    ("лаваш 400", ("лаваш", 400)),
    ("Лаваш 400", ("лаваш", 400)),
    ("айран 50", ("айран", 50)),
    ("донер маринад 100", ("донер маринад", 100)),
    ("ЛАВАШ 250", ("лаваш", 250)),
    ("лаваш", None),  # Missing quantity
    ("400", None),  # Missing name
    ("лаваш abc", None),  # Invalid quantity
]

for text, expected in test_cases:
    result = try_parse_quick_template(text)
    if result == expected:
        print(f"✅ '{text}' -> {result}")
    else:
        print(f"❌ '{text}' expected {expected}, got {result}")

# Test database operations
print("\nTesting database template operations...")
db = get_database()

# Create a test template
print("Creating test template...")
success = db.create_shipment_template(
    telegram_user_id=999999,  # Test user ID
    template_name="тестовый лаваш",
    supplier_id=1,
    supplier_name="Тестовый поставщик",
    account_id=2,
    account_name="Тестовый счет",
    items=[
        {
            "id": 1,
            "name": "Лаваш",
            "price": 40
        }
    ],
    storage_id=1
)

if success:
    print("✅ Template created successfully")
else:
    print("❌ Failed to create template")

# Get templates
print("\nGetting templates for test user...")
templates = db.get_shipment_templates(999999)
print(f"Found {len(templates)} templates")

for template in templates:
    print(f"  - {template['template_name']}: {template['items']}")

# Get single template
print("\nGetting single template...")
template = db.get_shipment_template(999999, "тестовый лаваш")
if template:
    print(f"✅ Template found: {template['template_name']}")
    print(f"   Supplier: {template['supplier_name']}")
    print(f"   Account: {template['account_name']}")
    print(f"   Items: {template['items']}")
else:
    print("❌ Template not found")

# Update template price
print("\nUpdating template price...")
if template:
    new_items = template['items'].copy()
    new_items[0]['price'] = 45
    success = db.update_shipment_template(
        telegram_user_id=999999,
        template_name="тестовый лаваш",
        items=new_items
    )
    if success:
        print("✅ Template updated successfully")
        updated_template = db.get_shipment_template(999999, "тестовый лаваш")
        print(f"   New price: {updated_template['items'][0]['price']}")
    else:
        print("❌ Failed to update template")

# Delete template
print("\nDeleting test template...")
success = db.delete_shipment_template(999999, "тестовый лаваш")
if success:
    print("✅ Template deleted successfully")
else:
    print("❌ Failed to delete template")

# Verify deletion
templates_after = db.get_shipment_templates(999999)
print(f"Templates remaining: {len(templates_after)}")

print("\n✅ All tests completed!")
