"""Tests for assistant action error handling: failures must surface in the chat
bubble (response_text) instead of crashing the backend with a 500."""
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


def _ensure_user(db):
    db.create_user(TEST_USER_ID, "mock_token", "1", "https://mock.joinposter.com/api")


def test_create_supply_db_error_reported_in_chat(app_client, db):
    """If the DB layer explodes during create_supply, the user must get a
    readable warning in the response bubble, and the HTTP status stays 200."""
    _ensure_user(db)
    _login(app_client)

    mock_agent_response = {
        "response_text": "Создаю поставку от Япоши...",
        "actions": [
            {
                "action": "create_supply",
                "supplier_name": "Япоша",
                "total_sum": 15000.0,
                "source": "cash",
                "items": [{"name": "Фри", "qty": 5.0, "price": 3000.0, "sum": 15000.0}],
            }
        ],
        "_model_used": "mock-gemini",
    }

    with patch("parser_service.ParserService.call_gemini_assistant_agent",
               new_callable=AsyncMock, return_value=mock_agent_response), \
         patch("database.UserDatabase.create_empty_supply_draft",
               side_effect=RuntimeError("Poster API вернул ошибку 32")):
        response = app_client.post('/api/assistant/message', data={
            'message': 'поставка от Япоши 15000',
            'date': '2026-06-10',
        })

    assert response.status_code == 200
    data = json.loads(response.data.decode('utf-8'))
    assert data['success'] is True
    # Original agent text is kept
    assert "Создаю поставку" in data['response_text']
    # And the failure is reported, with supplier context
    assert "⚠️" in data['response_text']
    assert "создание поставки" in data['response_text']
    assert "Япоша" in data['response_text']
    assert "Poster API вернул ошибку 32" in data['response_text']


def test_delete_pos_receipt_api_error_reported(app_client, db):
    """Poster API failure on receipt deletion → friendly message, no 500"""
    _ensure_user(db)
    _login(app_client)

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
            {"action": "delete_pos_receipt", "transaction_id": 11111, "poster_account_id": 1}
        ],
        "_model_used": "mock-gemini",
    }

    with patch("parser_service.ParserService.call_gemini_assistant_agent",
               new_callable=AsyncMock, return_value=mock_agent_response), \
         patch("poster_client.PosterClient.remove_transaction",
               new_callable=AsyncMock, side_effect=Exception("Poster API недоступен (timeout)")), \
         patch("poster_client.PosterClient.close", new_callable=AsyncMock):
        response = app_client.post('/api/assistant/message', data={
            'message': 'да, удаляй',
            'date': '2026-06-10',
        })

    assert response.status_code == 200
    data = json.loads(response.data.decode('utf-8'))
    assert "Ошибка при удалении чека №11111" in data['response_text']
    assert "Poster API недоступен" in data['response_text']


def test_agent_crash_returns_chat_error_not_500(app_client, db):
    """Even if the AI call itself raises, the route answers 200 with text"""
    _ensure_user(db)
    _login(app_client)

    with patch("parser_service.ParserService.call_gemini_assistant_agent",
               new_callable=AsyncMock, side_effect=Exception("Gemini 503 overloaded")):
        response = app_client.post('/api/assistant/message', data={
            'message': 'привет',
            'date': '2026-06-10',
        })

    assert response.status_code == 200
    data = json.loads(response.data.decode('utf-8'))
    assert "Gemini 503 overloaded" in data['response_text']


def test_multiple_actions_partial_failure(app_client, db):
    """One failing action must not block the rest: first expense is created,
    second action fails and is reported, but the route still succeeds."""
    _ensure_user(db)
    _login(app_client)

    mock_agent_response = {
        "response_text": "Записал расходы.",
        "actions": [
            {
                "action": "create_expense",
                "amount": 1200,
                "description": "такси резилиенс-тест",
                "expense_type": "transaction",
                "category": "Транспорт",
                "source": "cash",
            },
            {
                "action": "create_supply",
                "supplier_name": "Смолл",
                "total_sum": 5000.0,
                "source": "kaspi",
                "items": [],
            },
        ],
        "_model_used": "mock-gemini",
    }

    with patch("parser_service.ParserService.call_gemini_assistant_agent",
               new_callable=AsyncMock, return_value=mock_agent_response), \
         patch("database.UserDatabase.create_empty_supply_draft",
               side_effect=RuntimeError("connection lost")):
        response = app_client.post('/api/assistant/message', data={
            'message': 'такси 1200, поставка смолл 5000',
            'date': '2026-06-10',
        })

    assert response.status_code == 200
    data = json.loads(response.data.decode('utf-8'))
    assert "⚠️" in data['response_text']
    assert "connection lost" in data['response_text']

    # The independent expense action was still applied
    drafts = db.get_expense_drafts(TEST_USER_ID, status="all")
    taxi = [d for d in drafts if d.get('description') == 'такси резилиенс-тест']
    assert len(taxi) >= 1
    assert float(taxi[0]['amount']) == 1200.0

    # cleanup
    for d in taxi:
        db.delete_expense_draft(d['id'], telegram_user_id=TEST_USER_ID)
