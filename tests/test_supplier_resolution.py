"""Regression tests for supplier names being replaced by unrelated suppliers."""

from unittest.mock import AsyncMock, patch

import pytest

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


def _delete_alias(db, alias_text: str):
    for alias in db.get_supplier_aliases(TEST_USER_ID):
        if alias['alias_text'] == alias_text:
            db.delete_supplier_alias(TEST_USER_ID, alias['id'])


def _cleanup_drafts(db):
    for supply in db.get_supply_drafts(TEST_USER_ID, status='all'):
        db.delete_supply_draft(supply['id'], telegram_user_id=TEST_USER_ID)
    for expense in db.get_expense_drafts(TEST_USER_ID, status='all'):
        db.delete_expense_draft(expense['id'], telegram_user_id=TEST_USER_ID)


def test_legacy_auto_alias_cannot_override_canonical_supplier(db):
    """A stale bad alias from the removed auto-learning feature is ignored."""
    from matchers import _supplier_matchers
    from web_app import resolve_supplier_name_and_id

    db.create_user(TEST_USER_ID, 'mock_token', '1', 'https://mock.joinposter.com/api')
    alias_text = 'алимжан помидоры'
    _delete_alias(db, alias_text)

    try:
        db.add_supplier_alias(
            telegram_user_id=TEST_USER_ID,
            alias_text=alias_text,
            poster_supplier_id=17,
            poster_supplier_name='Кюрдамир',
            notes='Авто-обучено при редактировании черновика',
        )
        _supplier_matchers.pop(TEST_USER_ID, None)

        assert resolve_supplier_name_and_id(TEST_USER_ID, 'Алимжан помидоры') == (
            'Алимжан помидоры',
            21,
        )
        assert db.get_supplier_by_alias(TEST_USER_ID, alias_text) is None
    finally:
        _delete_alias(db, alias_text)
        _supplier_matchers.pop(TEST_USER_ID, None)


def test_explicit_supplier_in_message_overrides_ai_guess(app_client, db):
    """The deterministic user text wins when Gemini returns another supplier."""
    db.create_user(TEST_USER_ID, 'mock_token', '1', 'https://mock.joinposter.com/api')
    _login(app_client)
    _cleanup_drafts(db)

    mock_response = {
        'response_text': 'Создаю поставку Кюрдамир.',
        'actions': [{
            'action': 'create_supply',
            'supplier_name': 'Кюрдамир',
            'total_sum': 23117,
            'source': 'cash',
            'items': [],
        }],
        '_model_used': 'mock-gemini',
    }

    try:
        with patch(
            'parser_service.ParserService.call_gemini_assistant_agent',
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            response = app_client.post('/api/assistant/message', data={
                'message': 'Алимжан помидоры 23117',
                'date': '2026-08-30',
            })

        assert response.status_code == 200
        assert 'Кюрдамир' not in response.get_json()['response_text']
        assert 'Алимжан помидоры' in response.get_json()['response_text']
        supplies = db.get_supply_drafts(TEST_USER_ID, status='all')
        created = next(s for s in supplies if float(s.get('total_sum') or 0) == 23117)
        assert created['supplier_name'] == 'Алимжан помидоры'

        expense = db.get_expense_draft(created['linked_expense_draft_id'])
        assert expense['description'] == 'Алимжан помидоры'
    finally:
        _cleanup_drafts(db)


def test_explicit_supplier_detection_handles_common_spelling_variants():
    from web_app import find_explicit_supplier_name_and_id

    assert find_explicit_supplier_name_and_id(TEST_USER_ID, 'Сарыарка молочный 18000') == (
        'Сары-Арка молочный',
        19,
    )
    assert find_explicit_supplier_name_and_id(TEST_USER_ID, 'Сарарка молочный 18000') == (
        'Сары-Арка молочный',
        19,
    )
