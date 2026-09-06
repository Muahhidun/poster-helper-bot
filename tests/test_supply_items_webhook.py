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


def test_add_supply_items_creates_missing_supply_for_synced_expense(db):
    """A Poster-synced transaction can receive invoice items without a duplicate expense."""
    db.create_user(TEST_USER_ID, "mock_token", "1", "https://mock.joinposter.com/api")
    expense_id = db.create_expense_draft(
        telegram_user_id=TEST_USER_ID,
        amount=28900,
        description="Поставка №5747 от «Япоша»",
        expense_type="transaction",
        category="Прочее",
        source="kaspi",
        created_at="2026-09-01",
        completion_status="completed",
    )
    actions = [{
        "action": "add_supply_items",
        "expense_draft_id": str(expense_id),
        "items": [{
            "name": "Сыр творожный RASA 66%",
            "qty": 10,
            "unit": "кг",
            "price": 2890,
            "sum": 28900,
        }],
    }]

    with patch("web_app.resolve_supplier_name_and_id", return_value=("Япоша", 25)), \
         patch("matchers.get_ingredient_matcher") as ingredient_matcher, \
         patch("matchers.get_product_matcher") as product_matcher:
        ingredient_matcher.return_value.match.return_value = None
        product_matcher.return_value.match.return_value = None
        response_text, created_drafts = execute_assistant_actions(
            user_id=TEST_USER_ID,
            actions=actions,
            date_str="2026-09-01",
            response_text="Нашёл расход.",
            is_webhook=True,
        )

    expense = db.get_expense_draft(expense_id)
    assert expense['expense_type'] == 'supply'
    assert expense['description'] == 'Япоша'
    assert expense['completion_status'] == 'completed'

    linked = [
        draft for draft in db.get_supply_drafts(TEST_USER_ID, status='all')
        if draft.get('linked_expense_draft_id') == expense_id
    ]
    assert len(linked) == 1
    supply = db.get_supply_draft_with_items(linked[0]['id'])
    assert supply['supplier_name'] == 'Япоша'
    assert supply['total_sum'] == 28900
    assert len(supply['items']) == 1
    assert supply['items'][0]['quantity'] == 10
    assert supply['items'][0]['price_per_unit'] == 2890
    assert supply['items'][0]['poster_ingredient_id'] is None
    assert any(f"черновик #{supply['id']}" in item for item in created_drafts)
    assert f"черновик поставки #{supply['id']}" in response_text


def test_webhook_create_supply_reuses_prepared_expense_and_its_account(db):
    """An invoice fills the owner's prepared expense instead of creating another one."""
    db.create_user(TEST_USER_ID, "mock_token", "1", "https://mock.joinposter.com/api")
    expense_id = db.create_expense_draft(
        telegram_user_id=TEST_USER_ID,
        amount=27624,
        description="Кюрдамир",
        expense_type="supply",
        category="Прочее",
        source="cash",
        created_at="2026-09-06",
    )
    supply_id = db.create_empty_supply_draft(
        telegram_user_id=TEST_USER_ID,
        supplier_name="Кюрдамир",
        invoice_date="2026-09-06",
        total_sum=27624,
        linked_expense_draft_id=expense_id,
        source="cash",
    )
    actions = [{
        "action": "create_supply",
        "supplier_name": "Не указан",
        "total_sum": 27624,
        # Deliberately wrong model guess: the prepared expense must win.
        "source": "kaspi",
        "items": [
            {"name": "Шампиньоны", "qty": 2.66, "price": 2200, "sum": 5852},
            {"name": "Остальные товары", "qty": 1, "price": 20772, "sum": 20772},
        ],
    }]

    with patch("web_app.resolve_supplier_name_and_id", return_value=("Не указан", None)), \
         patch("matchers.get_ingredient_matcher") as ingredient_matcher, \
         patch("matchers.get_product_matcher") as product_matcher:
        ingredient_matcher.return_value.match.return_value = None
        product_matcher.return_value.match.return_value = None
        response_text, created_drafts = execute_assistant_actions(
            user_id=TEST_USER_ID,
            actions=actions,
            date_str="2026-09-06",
            response_text="Распознал накладную.",
            is_webhook=True,
        )

    expenses = db.get_expense_drafts(TEST_USER_ID, status="all")
    assert [expense['id'] for expense in expenses] == [expense_id]
    assert db.get_expense_draft(expense_id)['amount'] == 27624
    assert db.get_expense_draft(expense_id)['source'] == 'cash'

    supplies = db.get_supply_drafts(TEST_USER_ID, status="all")
    assert [supply['id'] for supply in supplies] == [supply_id]
    supply = db.get_supply_draft_with_items(supply_id)
    assert supply['linked_expense_draft_id'] == expense_id
    assert supply['supplier_name'] == 'Кюрдамир'
    assert supply['source'] == 'cash'
    assert supply['total_sum'] == 26624
    assert len(supply['items']) == 2
    assert f"расходом #{expense_id}" in response_text
    assert any(f"черновик #{supply_id}" in item for item in created_drafts)


def test_webhook_create_supply_without_prepared_expense_creates_both_drafts(db):
    """An unpaid invoice may create its own expense and linked supply draft."""
    db.create_user(TEST_USER_ID, "mock_token", "1", "https://mock.joinposter.com/api")
    actions = [{
        "action": "create_supply",
        "supplier_name": "Япоша",
        "total_sum": 36000,
        "source": "kaspi",
        "items": [{"name": "Фри", "qty": 25, "price": 1440, "sum": 36000}],
    }]

    with patch("web_app.resolve_supplier_name_and_id", return_value=("Япоша", 25)):
        response_text, created_drafts = execute_assistant_actions(
            user_id=TEST_USER_ID,
            actions=actions,
            date_str="2026-09-06",
            response_text="Распознал накладную.",
            is_webhook=True,
        )

    expenses = db.get_expense_drafts(TEST_USER_ID, status="all")
    assert len(expenses) == 1
    assert expenses[0]['description'] == 'Япоша'
    assert expenses[0]['amount'] == 36000
    assert expenses[0]['source'] == 'kaspi'

    supplies = db.get_supply_drafts(TEST_USER_ID, status="all")
    assert len(supplies) == 1
    assert supplies[0]['linked_expense_draft_id'] == expenses[0]['id']
    assert len(db.get_supply_draft_with_items(supplies[0]['id'])['items']) == 1
    assert any(f"черновик #{supplies[0]['id']}" in item for item in created_drafts)


def test_webhook_create_supply_does_not_duplicate_ambiguous_prepared_expenses(db):
    """Two equally suitable prepared rows require clarification, not a third expense."""
    db.create_user(TEST_USER_ID, "mock_token", "1", "https://mock.joinposter.com/api")
    for _ in range(2):
        expense_id = db.create_expense_draft(
            telegram_user_id=TEST_USER_ID,
            amount=36000,
            description="Япоша",
            expense_type="supply",
            category="Прочее",
            source="kaspi",
            created_at="2026-09-06",
        )
        db.create_empty_supply_draft(
            telegram_user_id=TEST_USER_ID,
            supplier_name="Япоша",
            invoice_date="2026-09-06",
            total_sum=36000,
            linked_expense_draft_id=expense_id,
            source="kaspi",
        )

    actions = [{
        "action": "create_supply",
        "supplier_name": "Япоша",
        "total_sum": 36000,
        "source": "kaspi",
        "items": [{"name": "Фри", "qty": 25, "price": 1440, "sum": 36000}],
    }]
    with patch("web_app.resolve_supplier_name_and_id", return_value=("Япоша", 25)):
        response_text, created_drafts = execute_assistant_actions(
            user_id=TEST_USER_ID,
            actions=actions,
            date_str="2026-09-06",
            response_text="Распознал накладную.",
            is_webhook=True,
        )

    assert len(db.get_expense_drafts(TEST_USER_ID, status="all")) == 2
    assert len(db.get_supply_drafts(TEST_USER_ID, status="all")) == 2
    assert created_drafts == []
    assert "несколько похожих расходов" in response_text
