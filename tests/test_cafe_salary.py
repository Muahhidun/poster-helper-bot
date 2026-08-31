import json
import pytest
from unittest.mock import AsyncMock, patch
from tests.conftest import TEST_USER_ID

@pytest.fixture
def app_client():
    from web_app import app
    app.config['TESTING'] = True
    return app.test_client()

def _login(app_client):
    with app_client.session_transaction() as sess:
        sess['telegram_user_id'] = TEST_USER_ID
        sess['web_user_id'] = 1
        sess['role'] = 'owner'

def _clean_db(db):
    from database import DB_TYPE
    conn = db._get_connection()
    cursor = conn.cursor()
    placeholder = "?" if DB_TYPE == "sqlite" else "%s"
    cursor.execute(f"DELETE FROM shift_closings WHERE telegram_user_id = {placeholder}", (TEST_USER_ID,))
    conn.commit()
    conn.close()

def test_cafe_salaries_create_with_helper_sushist(app_client, db):
    """Test creating cafe salaries including 'Помощник сушиста' with positive and zero salaries."""
    _login(app_client)
    _clean_db(db)

    # 1. Setup mock resolve_cafe_info
    mock_info = {
        'telegram_user_id': TEST_USER_ID,
        'poster_account_id': 12345,
        'account_name': 'Pizzburg-cafe',
        'poster_token': 'mock_token',
        'poster_user_id': 1,
        'poster_base_url': 'https://mock.joinposter.com/api'
    }

    # Clean database just in case
    db.clear_assistant_chat_history(TEST_USER_ID)
    # 2. Input salary data (including 'Помощник сушиста' with positive salary and another with zero)
    payload = {
        'salaries': [
            {'role': 'Кассир', 'name': 'Айгуль', 'amount': 15000},
            {'role': 'Сушист', 'name': 'Даурен', 'amount': 18000},
            {'role': 'Помощник сушиста', 'name': 'Адиль', 'amount': 8000},
            {'role': 'Повар Сандей', 'name': 'Нурлан', 'amount': 0}  # Should be skipped because it is 0
        ]
    }

    # Mock PosterClient
    mock_poster_client = AsyncMock()
    mock_poster_client.create_transaction.return_value = 999
    mock_poster_client.get_categories.return_value = []
    mock_poster_client.get_transactions.return_value = []

    with patch('web_app.resolve_cafe_info', return_value=mock_info), \
         patch('poster_client.PosterClient', return_value=mock_poster_client):
        
        resp = app_client.post('/api/cafe/salaries/create', 
                               data=json.dumps(payload),
                               content_type='application/json')
        
        assert resp.status_code == 200
        data = resp.json
        assert data['success'] is True
        assert len(data['salaries']) == 3  # Кассир, Сушист, Помощник сушиста
        assert data['total'] == 41000      # 15000 + 18000 + 8000

        # Check PosterClient calls
        # 3 calls: Кассир (cat 16), Сушист (cat 17), Помощник сушиста (cat 17)
        assert mock_poster_client.create_transaction.call_count == 3
        
        # Verify category IDs passed to Poster
        calls = mock_poster_client.create_transaction.call_args_list
        # Each call matches (transaction_type, category_id, account_from_id, amount, date, comment)
        # Check first call: Cashier (cat_id = 16)
        c1 = calls[0].kwargs
        assert c1['category_id'] == 16
        assert c1['amount'] == 15000
        assert c1['comment'] == 'Айгуль'

        # Check second call: Sushist (cat_id = 17)
        c2 = calls[1].kwargs
        assert c2['category_id'] == 17
        assert c2['amount'] == 18000
        assert c2['comment'] == 'Даурен'

        # Check third call: Helper Sushist (cat_id = 17)
        c3 = calls[2].kwargs
        assert c3['category_id'] == 17
        assert c3['amount'] == 8000
        assert c3['comment'] == 'Адиль'

def test_api_cafe_employees_last_backward_compatibility(app_client, db):
    """Test that api_cafe_employees_last returns both 'salaries' and 'employees' keys."""
    _login(app_client)
    _clean_db(db)

    mock_info = {
        'telegram_user_id': TEST_USER_ID,
        'poster_account_id': 12345,
        'account_name': 'Pizzburg-cafe',
        'poster_token': 'mock_token',
        'poster_user_id': 1,
        'poster_base_url': 'https://mock.joinposter.com/api'
    }

    # Save mock salaries to database
    from datetime import datetime
    date_str = datetime.now().strftime('%Y-%m-%d')
    salaries_data = json.dumps([
        {'role': 'Кассир', 'name': 'Айгуль', 'amount': 15000},
        {'role': 'Сушист', 'name': 'Даурен', 'amount': 18000}
    ])
    db.set_cafe_salaries(TEST_USER_ID, date_str, 12345, salaries_data)

    with patch('web_app.resolve_cafe_info', return_value=mock_info):
        resp = app_client.get('/api/cafe/employees/last')
        assert resp.status_code == 200
        data = resp.json
        assert data['success'] is True
        assert 'salaries' in data
        assert 'employees' in data
        assert data['salaries'] == data['employees']
        assert len(data['employees']) == 2
        assert data['employees'][0]['name'] == 'Айгуль'
