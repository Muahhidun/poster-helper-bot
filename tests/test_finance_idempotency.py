"""Tests for retry-safe multi-operation finance workflows."""

from unittest.mock import AsyncMock, patch

import pytest

from poster_client import find_existing_finance_transaction
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


def test_existing_salary_transaction_is_matched_in_poster_units():
    transactions = [{
        'transaction_id': 712,
        'type': '0',
        'category': '16',
        'account_from': '5',
        'amount_from': '1500000',
        'comment': 'Айгуль',
    }]

    result = find_existing_finance_transaction(
        transactions,
        transaction_type=0,
        category_id=16,
        account_from_id=5,
        amount=15000,
        comment='Айгуль',
    )

    assert result['transaction_id'] == 712


def test_different_amount_or_employee_is_not_treated_as_same_salary():
    transactions = [{
        'transaction_id': 712,
        'type': '0',
        'category': '16',
        'account_from': '5',
        'amount_from': '1500000',
        'comment': 'Айгуль',
    }]

    assert find_existing_finance_transaction(
        transactions,
        transaction_type=0,
        category_id=16,
        account_from_id=5,
        amount=16000,
        comment='Айгуль',
    ) is None
    assert find_existing_finance_transaction(
        transactions,
        transaction_type=0,
        category_id=16,
        account_from_id=5,
        amount=15000,
        comment='Даурен',
    ) is None


def test_shift_transfer_retry_skips_operation_already_present_in_poster(app_client, db):
    from database import DB_TYPE

    _login(app_client)
    date = '2026-08-30'
    conn = db._get_connection()
    cursor = conn.cursor()
    placeholder = '?' if DB_TYPE == 'sqlite' else '%s'
    cursor.execute(
        f"DELETE FROM shift_closings WHERE telegram_user_id = {placeholder} AND date = {placeholder}",
        (TEST_USER_ID, date),
    )
    conn.commit()
    conn.close()

    db.save_shift_closing(TEST_USER_ID, date, {
        'collection': 200,
        'wolt': 100,
        'halyk': 0,
        'cashless_diff': 0,
    })

    mock_client = AsyncMock()
    mock_client.get_transactions.return_value = [{
        'type': '2',
        'comment': 'PHB shift main 2026-08-30 2>4',
    }]
    mock_client.create_transaction.return_value = 991

    try:
        with patch('poster_client.PosterClient', return_value=mock_client):
            response = app_client.post('/api/shift-closing/transfers', json={'date': date})

        assert response.status_code == 200
        payload = response.get_json()
        assert payload['success'] is True
        assert payload['created_count'] == 1
        assert len(payload['transfers']) == 2
        assert sum(1 for item in payload['transfers'] if item.get('already_exists')) == 1
        mock_client.create_transaction.assert_awaited_once()
        assert mock_client.create_transaction.call_args.kwargs['comment'] == (
            'PHB shift main 2026-08-30 1>8'
        )
    finally:
        conn = db._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            f"DELETE FROM shift_closings WHERE telegram_user_id = {placeholder} AND date = {placeholder}",
            (TEST_USER_ID, date),
        )
        conn.commit()
        conn.close()
