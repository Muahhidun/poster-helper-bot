import pytest
import json
from unittest.mock import patch, MagicMock
from database import get_database
from web_app import app, send_telegram_message

@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client

def test_database_purchase_methods():
    db = get_database()
    user_id = 999999
    
    # 1. Test adding purchase supplier
    schedule = {"6": 3, "2": 4}
    sup_id = db.add_purchase_supplier(user_id, "Тестовый Поставщик", schedule)
    assert sup_id != -1
    
    # 2. Verify getting supplier
    suppliers = db.get_purchase_suppliers(user_id)
    supplier = next((s for s in suppliers if s['id'] == sup_id), None)
    assert supplier is not None
    assert supplier['name'] == "Тестовый Поставщик"
    assert supplier['schedule'] == schedule
    
    # 3. Test adding ingredient to purchase sheet
    success = db.add_purchase_ingredient(
        telegram_user_id=user_id,
        supplier_id=sup_id,
        name="Тестовый Ингредиент",
        poster_ingredient_id=12345,
        default_target_stock=10.0,
        sort_order=5
    )
    assert success is True
    
    # 4. Verify getting ingredient
    ingredients = db.get_purchase_ingredients(user_id, supplier_id=sup_id)
    assert len(ingredients) > 0
    ing = ingredients[0]
    assert ing['name'] == "Тестовый Ингредиент"
    assert ing['poster_ingredient_id'] == 12345
    assert ing['default_target_stock'] == 10.0
    
    # 5. Clean up
    success_del = db.delete_purchase_supplier(user_id, sup_id)
    assert success_del is True
    
    # Supplier and ingredients should be deleted cascadingly
    suppliers_after = db.get_purchase_suppliers(user_id)
    assert not any(s['id'] == sup_id for s in suppliers_after)
    
    ingredients_after = db.get_purchase_ingredients(user_id, supplier_id=sup_id)
    assert len(ingredients_after) == 0

@patch('web_app.send_telegram_message')
@patch('database.UserDatabase.get_purchase_suppliers')
@patch('database.UserDatabase.get_purchase_ingredients')
@patch('poster_client.PosterClient.get_ingredient_movements')
def test_api_purchase_endpoints(
    mock_movements,
    mock_ingredients,
    mock_suppliers,
    mock_send_tg,
    client
):
    user_id = 999999
    
    # Mock data
    mock_suppliers.return_value = [
        {
            'id': 101,
            'name': 'Инарин (Аст)',
            'schedule': {"6": 4, "3": 4} # Sunday (6) covers 4 days
        }
    ]
    
    mock_ingredients.return_value = [
        {
            'id': 201,
            'supplier_id': 101,
            'name': 'Булч бол',
            'poster_ingredient_id': 198,
            'default_target_stock': 12.0,
            'sort_order': 0
        }
    ]
    
    # Mock Poster client movements returning:
    # 15.0 total expense over 30 days -> 0.5 average daily consumption
    mock_movements.return_value = [
        {
            'ingredient_id': '198',
            'name': 'Булч бол',
            'expense': '-15.0'
        }
    ]
    
    # Setup mock session user
    with client.session_transaction() as sess:
        sess['telegram_user_id'] = user_id
        sess['web_user_id'] = user_id
        sess['role'] = 'owner'
        
    # GET purchase blank for Sunday (2026-07-05 is a Sunday)
    resp = client.get('/api/purchase/blank?date=2026-07-05')
    assert resp.status_code == 200
    
    data = json.loads(resp.data)
    assert data['date'] == '2026-07-05'
    assert data['weekday'] == 6 # Sunday
    assert len(data['suppliers']) == 1
    
    supplier = data['suppliers'][0]
    assert supplier['is_order_day'] is True
    assert supplier['cover_days'] == 4
    
    assert len(supplier['ingredients']) == 1
    ing = supplier['ingredients'][0]
    assert ing['name'] == 'Булч бол'
    assert ing['avg_daily_consumption'] == 0.5
    # calculated_target = 0.5 (daily) * 4 (days) * 1.15 (safety margin) = 2.3
    assert ing['calculated_target'] == 2.3
    assert ing['target_stock'] == 2.3
    
    # POST purchase submit
    submit_data = {
        'date': '2026-07-05',
        'supplier_id': 101,
        'items': [
            {
                'name': 'Булч бол',
                'target_stock': 2.3,
                'actual_stock': 0.3,
                'order_qty': 2.0
            }
        ]
    }
    
    resp_submit = client.post('/api/purchase/submit', json=submit_data)
    assert resp_submit.status_code == 200
    
    # Check that message was sent to Telegram with correct details
    mock_send_tg.assert_called_once()
    args, _ = mock_send_tg.call_args
    assert args[0] == user_id
    assert "Заказ для Инарин (Аст) на 2026-07-05" in args[1]
    assert "Булч бол: 2" in args[1]
