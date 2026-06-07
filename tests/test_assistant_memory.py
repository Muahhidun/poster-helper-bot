"""Tests for assistant memory storage, manual editing, and automatic updating"""
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


def test_db_assistant_memory_methods(db):
    """Test get_assistant_memory and save_assistant_memory directly on the DB"""
    # Reset any leftover state from other tests
    db.save_assistant_memory(TEST_USER_ID, "")

    # 1. Initially memory should be empty
    assert db.get_assistant_memory(TEST_USER_ID) == ""

    # 2. Save memory and retrieve
    test_text = "# Test Memory\n- Rule 1: Always check weight"
    assert db.save_assistant_memory(TEST_USER_ID, test_text) is True
    assert db.get_assistant_memory(TEST_USER_ID) == test_text

    # 3. Update memory and retrieve
    test_text_v2 = "# Test Memory\n- Rule 1: Always check weight\n- Rule 2: Kaspi is default"
    assert db.save_assistant_memory(TEST_USER_ID, test_text_v2) is True
    assert db.get_assistant_memory(TEST_USER_ID) == test_text_v2


def test_manual_save_memory_route(app_client, db):
    """Test POST /api/assistant/memory manual saving"""
    # Clear any memory
    db.save_assistant_memory(TEST_USER_ID, "")

    with app_client.session_transaction() as sess:
        sess['telegram_user_id'] = TEST_USER_ID
        sess['web_user_id'] = 1
        sess['role'] = 'owner'

    payload = {"memory_text": "Manual note content here"}
    response = app_client.post('/api/assistant/memory', 
                              json=payload)

    assert response.status_code == 200
    resp_data = json.loads(response.data.decode('utf-8'))
    assert resp_data['success'] is True
    assert db.get_assistant_memory(TEST_USER_ID) == "Manual note content here"


def test_assistant_update_memory_action(app_client, db):
    """Test that assistant message response handles update_memory action"""
    db.create_user(TEST_USER_ID, "mock_token", "1", "https://mock.joinposter.com/api")
    db.save_assistant_memory(TEST_USER_ID, "Old Rule")

    mock_agent_response = {
        "response_text": "Я обновил ваши правила.",
        "actions": [
            {
                "action": "update_memory",
                "memory_text": "New Rule text saved by AI"
            }
        ],
        "_model_used": "mock-gemini-memory"
    }

    with patch("parser_service.ParserService.call_gemini_assistant_agent", new_callable=AsyncMock) as mock_agent:
        mock_agent.return_value = mock_agent_response

        with app_client.session_transaction() as sess:
            sess['telegram_user_id'] = TEST_USER_ID
            sess['web_user_id'] = 1
            sess['role'] = 'owner'

        response = app_client.post('/api/assistant/message', data={
            'message': 'Запомни новое правило: ...',
            'date': '2026-06-05'
        })

        assert response.status_code == 200
        resp_data = json.loads(response.data.decode('utf-8'))
        assert resp_data['success'] is True
        assert resp_data['response_text'] == "Я обновил ваши правила."
        # Verify database is updated
        assert db.get_assistant_memory(TEST_USER_ID) == "New Rule text saved by AI"
        # Verify response JSON contains updated memory
        assert resp_data['assistant_memory'] == "New Rule text saved by AI"
