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


def test_supplier_is_resolved_separately_for_each_poster_account():
    from web_app import resolve_supplier_for_account

    cafe_suppliers = [
        {'supplier_id': '1', 'supplier_name': 'Metro'},
        {'supplier_id': '4', 'supplier_name': 'Сары Арка молочный'},
        {'supplier_id': '17', 'supplier_name': 'Богатырский Молочный отдел'},
        {'supplier_id': '19', 'supplier_name': 'Богатырский Мороженое'},
        {'supplier_id': '10', 'supplier_name': 'Япоша'},
    ]

    assert resolve_supplier_for_account(
        'Сары-Арка молочный отдел (Слева)', cafe_suppliers
    ) == (4, 'Сары Арка молочный')
    assert resolve_supplier_for_account('Япоша', cafe_suppliers) == (10, 'Япоша')
    assert resolve_supplier_for_account(
        'Богатырский молочка', cafe_suppliers
    ) == (17, 'Богатырский Молочный отдел')


def test_unknown_supplier_never_falls_back_to_metro():
    from web_app import resolve_supplier_for_account

    assert resolve_supplier_for_account(
        'Совершенно новый поставщик',
        [{'supplier_id': '1', 'supplier_name': 'Metro'}],
    ) == (None, None)


def test_finance_account_ids_are_remapped_by_meaning_between_departments():
    from web_app import resolve_finance_account_for_source

    cafe_accounts = [
        {'account_id': '1', 'account_name': 'Kaspi'},
        {'account_id': '4', 'account_name': 'Деньги дома'},
        {'account_id': '5', 'account_name': 'Оставил в кассе (на закупы)'},
        {'account_id': '8', 'account_name': 'Halyk'},
    ]

    # ID 4 means cash in the primary account but another account in Cafe.
    assert resolve_finance_account_for_source(cafe_accounts, 'cash', preferred_id=4) == (
        5, 'Оставил в кассе (на закупы)'
    )
    assert resolve_finance_account_for_source(cafe_accounts, 'kaspi', preferred_id=1) == (1, 'Kaspi')
    assert resolve_finance_account_for_source(cafe_accounts, 'halyk', preferred_id=10) == (8, 'Halyk')


def test_shared_supplier_mapping_is_one_to_one_and_leaves_exceptions_alone():
    from web_app import build_supplier_account_mapping_rows

    accounts = [
        {'id': 1, 'account_name': 'Pizzburg', 'is_primary': True},
        {'id': 2, 'account_name': 'Pizzburg-cafe', 'is_primary': False},
    ]
    suppliers = [
        {'id': 19, 'name': 'Сары-Арка молочный отдел (Слева)', 'poster_account_id': 1},
        {'id': 37, 'name': 'Богатырский молочка', 'poster_account_id': 1},
        {'id': 51, 'name': 'Богатырский Молочный отдел', 'poster_account_id': 1},
        {'id': 4, 'name': 'Сары Арка молочный', 'poster_account_id': 2},
        {'id': 17, 'name': 'Богатырский Молочный отдел', 'poster_account_id': 2},
        {'id': 19, 'name': 'Богатырский Мороженое', 'poster_account_id': 2},
        {'id': 21, 'name': 'Только Sunday', 'poster_account_id': 2},
    ]
    rows = build_supplier_account_mapping_rows(accounts, suppliers)

    pairs = {
        (row['canonical_name'], row['poster_account_id'], row['poster_supplier_id'])
        for row in rows
    }
    assert ('Сары-Арка молочный отдел (Слева)', 1, 19) in pairs
    assert ('Сары-Арка молочный отдел (Слева)', 2, 4) in pairs
    assert ('Богатырский Молочный отдел', 1, 51) in pairs
    assert ('Богатырский Молочный отдел', 2, 17) in pairs
    assert all(row['poster_supplier_name'] != 'Богатырский Мороженое' for row in rows)
    assert all(row['poster_supplier_name'] != 'Только Sunday' for row in rows)


def test_explicit_mapping_resolves_local_supplier_id(db):
    from web_app import resolve_supplier_for_account

    db.replace_auto_supplier_account_mappings(TEST_USER_ID, [
        {
            'canonical_name': 'Сары-Арка молочный отдел (Слева)',
            'poster_account_id': 7001,
            'poster_account_name': 'Pizzburg',
            'poster_supplier_id': 19,
            'poster_supplier_name': 'Сары-Арка молочный отдел (Слева)',
            'confidence': 99,
        },
        {
            'canonical_name': 'Сары-Арка молочный отдел (Слева)',
            'poster_account_id': 7002,
            'poster_account_name': 'Pizzburg-cafe',
            'poster_supplier_id': 4,
            'poster_supplier_name': 'Сары Арка молочный',
            'confidence': 99,
        },
    ])
    try:
        assert resolve_supplier_for_account(
            'Сарарка молочный',
            [{'supplier_id': '1', 'supplier_name': 'Metro'},
             {'supplier_id': '4', 'supplier_name': 'Сары Арка молочный'}],
            telegram_user_id=TEST_USER_ID,
            poster_account_id=7002,
        ) == (4, 'Сары Арка молочный')
    finally:
        db.replace_auto_supplier_account_mappings(TEST_USER_ID, [])
