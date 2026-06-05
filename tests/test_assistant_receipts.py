"""Tests for POS receipt search and deletion through assistant chat"""
import pytest
import json
from unittest.mock import MagicMock, AsyncMock, patch
from tests.conftest import TEST_USER_ID


@pytest.fixture
def app_client():
    """Create Flask test client"""
    from web_app import app
    app.config['TESTING'] = True
    return app.test_client()


def test_find_pos_receipt_action(app_client, db):
    """Test find_pos_receipt action handler in assistant route"""
    # 1. Setup mock account in database if not exists
    db.create_user(TEST_USER_ID, "mock_token", "1", "https://mock.joinposter.com/api")
    
    # Check if mock poster account exists, otherwise create it
    accounts = db.get_accounts(TEST_USER_ID)
    if not any(a['id'] == 1 for a in accounts):
        # Insert a mock account
        conn = db._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO poster_accounts (id, telegram_user_id, account_name, poster_token, poster_user_id, poster_base_url, is_primary) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (1, TEST_USER_ID, 'Pizzburg', 'mock_token', '1', 'https://mock.joinposter.com/api', 1)
        )
        conn.commit()
        conn.close()

    # 2. Mock Gemini agent response to return find_pos_receipt action
    mock_agent_response = {
        "response_text": "Ищу чек в системе...",
        "actions": [
            {
                "action": "find_pos_receipt",
                "amount": 5000,
                "order_number": None
            }
        ],
        "_model_used": "mock-gemini"
    }

    # Mock transactions list from Poster API
    mock_transactions_resp = {
        "response": [
            {
                "transaction_id": "11111",
                "status": "2",  # closed
                "payed_sum": "500000",  # 5000 KZT in tiyins
                "date_close": "2026-06-05 18:30:15"
            },
            {
                "transaction_id": "22222",
                "status": "2",
                "payed_sum": "200000",  # 2000 KZT in tiyins
                "date_close": "2026-06-05 19:40:20"
            }
        ]
    }

    # Mock products list from Poster API
    mock_products_resp = {
        "response": [
            {
                "product_id": "101",
                "product_name": "Пицца Пепперони",
                "count": "1"
            },
            {
                "product_id": "102",
                "product_name": "Кола",
                "count": "2"
            }
        ]
    }

    # 3. Patch dependencies
    with patch("parser_service.ParserService.call_gemini_assistant_agent", new_callable=AsyncMock) as mock_agent, \
         patch("poster_client.PosterClient._request", new_callable=AsyncMock) as mock_poster_request:
        
        mock_agent.return_value = mock_agent_response
        
        # Define mock responses for PosterClient _request based on endpoint
        async def side_effect(method, endpoint, params=None, data=None, use_json=True):
            if endpoint == "dash.getTransactions":
                return mock_transactions_resp
            elif endpoint == "dash.getTransactionProducts":
                return mock_products_resp
            return {"response": []}
        
        mock_poster_request.side_effect = side_effect

        # Set session variable for authentication
        with app_client.session_transaction() as sess:
            sess['telegram_user_id'] = TEST_USER_ID
            sess['web_user_id'] = 1
            sess['role'] = 'owner'

        # Call Flask assistant endpoint
        response = app_client.post('/api/assistant/message', data={
            'message': 'удали вчерашний чек на 5000',
            'date': '2026-06-05'
        })

        assert response.status_code == 200
        resp_data = json.loads(response.data.decode('utf-8'))
        assert resp_data['success'] is True
        assert "Найден чек №11111" in resp_data['response_text']
        assert "Пицца Пепперони (1 шт), Кола (2 шт)" in resp_data['response_text']
        assert "Точно он? Удаляем?" in resp_data['response_text']
        assert "[Метаданные: ID чека: 11111" in resp_data['response_text']


def test_delete_pos_receipt_action(app_client, db):
    """Test delete_pos_receipt action handler in assistant route"""
    db.create_user(TEST_USER_ID, "mock_token", "1", "https://mock.joinposter.com/api")
    
    # Check if mock poster account exists, otherwise create it
    accounts = db.get_accounts(TEST_USER_ID)
    if not any(a['id'] == 1 for a in accounts):
        conn = db._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO poster_accounts (id, telegram_user_id, account_name, poster_token, poster_user_id, poster_base_url, is_primary) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (1, TEST_USER_ID, 'Pizzburg', 'mock_token', '1', 'https://mock.joinposter.com/api', 1)
        )
        conn.commit()
        conn.close()

    mock_agent_response = {
        "response_text": "Удаляю чек №11111...",
        "actions": [
            {
                "action": "delete_pos_receipt",
                "transaction_id": 11111,
                "poster_account_id": 1
            }
        ],
        "_model_used": "mock-gemini"
    }

    with patch("parser_service.ParserService.call_gemini_assistant_agent", new_callable=AsyncMock) as mock_agent, \
         patch("poster_client.PosterClient.remove_transaction", new_callable=AsyncMock) as mock_remove_tx:
        
        mock_agent.return_value = mock_agent_response
        mock_remove_tx.return_value = True

        with app_client.session_transaction() as sess:
            sess['telegram_user_id'] = TEST_USER_ID
            sess['web_user_id'] = 1
            sess['role'] = 'owner'

        response = app_client.post('/api/assistant/message', data={
            'message': 'Да, точно он. Удаляй.',
            'date': '2026-06-05'
        })

        assert response.status_code == 200
        resp_data = json.loads(response.data.decode('utf-8'))
        assert resp_data['success'] is True
        assert "Чек №11111 успешно удалён" in resp_data['response_text']
        mock_remove_tx.assert_called_once_with(11111)
