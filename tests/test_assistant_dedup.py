"""Tests for duplicate supply detection: sending the same invoice twice
should not create a second supply draft."""
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


def _cleanup_all(db):
    """Remove ALL drafts for the test user to ensure isolation."""
    for s in db.get_supply_drafts(TEST_USER_ID, status="all"):
        db.delete_supply_draft(s['id'], telegram_user_id=TEST_USER_ID)
    for d in db.get_expense_drafts(TEST_USER_ID, status="all"):
        db.delete_expense_draft(d['id'], telegram_user_id=TEST_USER_ID)


def test_create_supply_duplicate_skipped(app_client, db):
    """Second create_supply with same supplier/sum/date is skipped."""
    db.create_user(TEST_USER_ID, "mock_token", "1", "https://mock.joinposter.com/api")
    _login(app_client)
    _cleanup_all(db)

    mock_response = {
        "response_text": "Создаю поставку.",
        "actions": [{
            "action": "create_supply",
            "supplier_name": "XTestDupSupplier",
            "total_sum": 25777.0,
            "source": "kaspi",
            "items": [{"name": "Товар", "qty": 10, "price": 2577.7, "sum": 25777.0}],
        }],
        "_model_used": "mock-gemini",
    }

    # First call — creates the supply
    with patch("parser_service.ParserService.call_gemini_assistant_agent",
               new_callable=AsyncMock, return_value=mock_response):
        r1 = app_client.post('/api/assistant/message', data={
            'message': 'накладная', 'date': '2026-06-11'})
    assert r1.status_code == 200

    supplies_after_first = db.get_supply_drafts(TEST_USER_ID, status="all")
    count_first = len(supplies_after_first)
    assert count_first >= 1

    # Second call — same data, should be detected as duplicate
    with patch("parser_service.ParserService.call_gemini_assistant_agent",
               new_callable=AsyncMock, return_value=mock_response):
        r2 = app_client.post('/api/assistant/message', data={
            'message': 'накладная', 'date': '2026-06-11'})
    assert r2.status_code == 200
    data2 = json.loads(r2.data)
    assert 'дубликат' in data2['response_text'].lower()

    supplies_after_second = db.get_supply_drafts(TEST_USER_ID, status="all")
    assert len(supplies_after_second) == count_first

    _cleanup_all(db)


def test_different_supplier_not_duplicate(app_client, db):
    """Different supplier name -> not a duplicate, both are created."""
    db.create_user(TEST_USER_ID, "mock_token", "1", "https://mock.joinposter.com/api")
    _login(app_client)
    _cleanup_all(db)

    mock1 = {
        "response_text": "Поставка 1.",
        "actions": [{
            "action": "create_supply",
            "supplier_name": "XTestUniqueAlpha",
            "total_sum": 10000.0, "source": "kaspi", "items": [],
        }],
        "_model_used": "mock-gemini",
    }
    mock2 = {
        "response_text": "Поставка 2.",
        "actions": [{
            "action": "create_supply",
            "supplier_name": "XTestUniqueBeta",
            "total_sum": 10000.0, "source": "kaspi", "items": [],
        }],
        "_model_used": "mock-gemini",
    }

    with patch("parser_service.ParserService.call_gemini_assistant_agent",
               new_callable=AsyncMock, return_value=mock1):
        app_client.post('/api/assistant/message', data={
            'message': 'от Alpha', 'date': '2026-06-11'})

    with patch("parser_service.ParserService.call_gemini_assistant_agent",
               new_callable=AsyncMock, return_value=mock2):
        app_client.post('/api/assistant/message', data={
            'message': 'от Beta', 'date': '2026-06-11'})

    supplies = db.get_supply_drafts(TEST_USER_ID, status="all")
    assert len(supplies) == 2

    _cleanup_all(db)
