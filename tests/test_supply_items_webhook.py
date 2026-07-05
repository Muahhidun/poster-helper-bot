import json
import pytest
from unittest.mock import patch, AsyncMock
from tests.conftest import TEST_USER_ID
from web_app import execute_assistant_actions, _add_items_to_supply_draft

@pytest.fixture(autouse=True)
def clean_database(db):
    """Clean the test database tables related to supply drafts and expense drafts."""
    conn = db._get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM expense_drafts WHERE telegram_user_id = ?", (TEST_USER_ID,))
    cursor.execute("DELETE FROM supply_drafts WHERE telegram_user_id = ?", (TEST_USER_ID,))
    conn.commit()
    conn.close()
    yield

def test_add_supply_items_webhook_adds_only_new_items(db):
    """Test that add_supply_items from a webhook adds only new items to an existing supply draft,
    avoiding duplication of already present items and updating the supply draft total sum."""
    db.create_user(TEST_USER_ID, "mock_token", "1", "https://mock.joinposter.com/api")

    # 1. Create an initial expense draft (type = 'supply')
    expense_draft_id = db.create_expense_draft(
        telegram_user_id=TEST_USER_ID,
        amount=1000.0,
        description="ИП Ержанова",
        expense_type="supply",
        category="Продукты",
        source="kaspi",
        created_at="2026-07-05"
    )
    assert expense_draft_id is not None

    # 2. Create the linked supply draft
    db.create_empty_supply_draft(
        telegram_user_id=TEST_USER_ID,
        supplier_name="ИП Ержанова",
        invoice_date="2026-07-05",
        total_sum=1000.0,
        linked_expense_draft_id=expense_draft_id,
        source="kaspi"
    )

    supplies = db.get_supply_drafts(TEST_USER_ID, status="all")
    supply = next((s for s in supplies if s.get('linked_expense_draft_id') == expense_draft_id), None)
    assert supply is not None

    # 3. Add initial item ("Молоко") to the supply draft
    initial_items = [
        {"name": "Молоко", "qty": 2.0, "price": 500.0, "sum": 1000.0}
    ]
    _add_items_to_supply_draft(db, TEST_USER_ID, supply['id'], initial_items)

    # Verify initial item exists in DB
    sd_with_items = db.get_supply_draft_with_items(supply['id'])
    assert len(sd_with_items['items']) == 1
    assert sd_with_items['items'][0]['item_name'] == "Молоко"
    assert sd_with_items['items'][0]['quantity'] == 2.0

    # 4. Trigger execute_assistant_actions simulating webhook updates.
    # LLM sends:
    #   - update_expense (updates total to 1500.0)
    #   - add_supply_items (contains "Молоко" (old) and "Новый товар" (new))
    actions = [
        {
            "action": "update_expense",
            "id": expense_draft_id,
            "amount": 1500.0
        },
        {
            "action": "add_supply_items",
            "expense_draft_id": expense_draft_id,
            "items": [
                {"name": "Молоко", "qty": 2.0, "price": 500.0, "sum": 1000.0},
                {"name": "Новый товар", "qty": 1.0, "price": 500.0, "sum": 500.0}
            ]
        }
    ]

    with patch("matchers.get_ingredient_matcher") as mock_ing_matcher, \
         patch("matchers.get_product_matcher") as mock_prod_matcher:
        # Mock ingredient matcher to not match anything, keeping raw name
        mock_ing_matcher.return_value.match.return_value = None
        mock_prod_matcher.return_value.match.return_value = None

        response_text, created_drafts = execute_assistant_actions(
            user_id=TEST_USER_ID,
            actions=actions,
            date_str="2026-07-05",
            response_text="",
            is_webhook=True
        )

    # 5. Assertions
    # Verify expense draft sum updated
    exp = db.get_expense_draft(expense_draft_id)
    assert exp['amount'] == 1500.0

    # Verify supply draft items: only "Новый товар" was added (total items = 2)
    sd_updated = db.get_supply_draft_with_items(supply['id'])
    assert len(sd_updated['items']) == 2
    item_names = [item['item_name'] for item in sd_updated['items']]
    assert "Молоко" in item_names
    assert "Новый товар" in item_names
    
    # Verify quantities
    moloko_item = next(item for item in sd_updated['items'] if item['item_name'] == "Молоко")
    new_item = next(item for item in sd_updated['items'] if item['item_name'] == "Новый товар")
    assert moloko_item['quantity'] == 2.0
    assert new_item['quantity'] == 1.0

    # Verify supply draft total_sum was updated and synced with expense draft
    assert sd_updated['total_sum'] == 1500.0

    # Verify response lists the added and updated items
    assert any("Обновлен черновик расхода" in d for d in created_drafts)
    assert any("Добавлено 1 новых поз." in d for d in created_drafts)
