"""Simple test for shipment templates database operations"""
import sys
import re
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)

# Test imports
print("Testing database import...")
try:
    from database import get_database
    print("✅ database import OK")
except Exception as e:
    print(f"❌ database import failed: {e}")
    sys.exit(1)

# Test quick template parsing (without importing shipment_templates)
def try_parse_quick_template(text: str):
    """Parse quick template syntax like "лаваш 400" """
    pattern = r'^([а-яёa-z\s]+?)\s+(\d+)$'
    match = re.match(pattern, text.strip().lower(), re.IGNORECASE)

    if match:
        template_name = match.group(1).strip()
        quantity = int(match.group(2))
        return (template_name, quantity)

    return None

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

all_passed = True
for text, expected in test_cases:
    result = try_parse_quick_template(text)
    if result == expected:
        print(f"✅ '{text}' -> {result}")
    else:
        print(f"❌ '{text}' expected {expected}, got {result}")
        all_passed = False

if not all_passed:
    print("\n❌ Some parsing tests failed!")
    sys.exit(1)

# Test database operations
print("\nTesting database template operations...")
db = get_database()

# Clean up any existing test data
print("Cleaning up test data...")
db.delete_shipment_template(999999, "тестовый лаваш")

# Create a test template
print("\nCreating test template...")
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
    sys.exit(1)

# Get templates
print("\nGetting templates for test user...")
templates = db.get_shipment_templates(999999)
print(f"Found {len(templates)} templates")

if len(templates) == 0:
    print("❌ No templates found after creation!")
    sys.exit(1)

for template in templates:
    print(f"  - {template['template_name']}: {template['items']}")
    print(f"    Supplier: {template['supplier_name']}, Account: {template['account_name']}")

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
    sys.exit(1)

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
        if updated_template and updated_template['items'][0]['price'] == 45:
            print(f"   New price: {updated_template['items'][0]['price']} ✅")
        else:
            print(f"   ❌ Price not updated correctly!")
            sys.exit(1)
    else:
        print("❌ Failed to update template")
        sys.exit(1)

# Test creating multiple templates
print("\nCreating second template...")
success = db.create_shipment_template(
    telegram_user_id=999999,
    template_name="айран",
    supplier_id=2,
    supplier_name="Молочник",
    account_id=2,
    account_name="Каспи Пей",
    items=[
        {
            "id": 5,
            "name": "Айран",
            "price": 150
        }
    ],
    storage_id=1
)

if success:
    print("✅ Second template created")
    templates = db.get_shipment_templates(999999)
    print(f"   Total templates: {len(templates)}")
    if len(templates) != 2:
        print(f"   ❌ Expected 2 templates, got {len(templates)}")
        sys.exit(1)
else:
    print("❌ Failed to create second template")
    sys.exit(1)

# Delete templates
print("\nDeleting test templates...")
success1 = db.delete_shipment_template(999999, "тестовый лаваш")
success2 = db.delete_shipment_template(999999, "айран")

if success1 and success2:
    print("✅ Templates deleted successfully")
else:
    print("❌ Failed to delete templates")
    sys.exit(1)

# Verify deletion
templates_after = db.get_shipment_templates(999999)
if len(templates_after) == 0:
    print(f"✅ All templates cleaned up")
else:
    print(f"❌ Templates remaining: {len(templates_after)}")
    sys.exit(1)

print("\n" + "="*50)
print("✅ All tests PASSED!")
print("="*50)
