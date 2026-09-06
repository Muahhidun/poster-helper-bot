"""Regression tests for context-free WhatsApp invoice extraction."""

from tests.conftest import TEST_USER_ID


def _delete_expense(db, expense_id):
    db.delete_expense_draft(expense_id, telegram_user_id=TEST_USER_ID)


def test_printed_invoice_fields_pass_through_without_history_substitution(db):
    from web_app import _actions_from_whatsapp_document

    db.create_user(TEST_USER_ID, 'mock_token', '1', 'https://mock.joinposter.com/api')
    parsed = {
        'document_type': 'printed_invoice',
        'invoice': {
            'supplier': 'YAPOSHA MARKET ЕКИБАСТУЗ',
            'total_sum': 7613.76,
            'items': [{
                'name': 'чеддер весовой',
                'qty': 1.442,
                'price': 5280,
                'sum': 7613.76,
                'source_text': 'чеддер весовой 1,442 кг 5280 7613,76',
            }],
        },
    }

    actions = _actions_from_whatsapp_document(
        db, TEST_USER_ID, parsed, '2099-01-01'
    )

    assert len(actions) == 1
    assert actions[0]['action'] == 'create_supply'
    assert actions[0]['supplier_name'] == 'YAPOSHA MARKET ЕКИБАСТУЗ'
    assert actions[0]['items'][0]['name'] == 'чеддер весовой'


def test_invoice_uses_unique_existing_expense_with_same_date_and_total(db):
    from web_app import _actions_from_whatsapp_document

    db.create_user(TEST_USER_ID, 'mock_token', '1', 'https://mock.joinposter.com/api')
    expense_id = db.create_expense_draft(
        telegram_user_id=TEST_USER_ID,
        amount=28900,
        description='Япоша',
        expense_type='supply',
        source='kaspi',
        created_at='2099-01-02',
    )
    try:
        actions = _actions_from_whatsapp_document(db, TEST_USER_ID, {
            'document_type': 'printed_invoice',
            'invoice': {
                'supplier': 'YAPOSHA MARKET',
                'total_sum': 28900,
                'items': [{'name': 'Сыр творожный RASA 10 кг', 'qty': 10, 'price': 2890, 'sum': 28900}],
            },
        }, '2099-01-02')

        assert actions[0]['action'] == 'add_supply_items'
        assert actions[0]['expense_draft_id'] == expense_id
        assert actions[0]['supplier_name'] == 'YAPOSHA MARKET'
    finally:
        _delete_expense(db, expense_id)


def test_mixed_photo_keeps_invoice_and_separate_cashier_notes(db):
    from web_app import _actions_from_whatsapp_document

    db.create_user(TEST_USER_ID, 'mock_token', '1', 'https://mock.joinposter.com/api')
    actions = _actions_from_whatsapp_document(db, TEST_USER_ID, {
        'document_type': 'mixed_document',
        'expenses': [{
            'amount': 13000,
            'description': 'Курьеры 95 А',
            'type': 'transaction',
            'category': 'Зарплаты',
        }],
        'invoice': {
            'supplier': 'Идея',
            'total_sum': 63680,
            'items': [{'name': 'Масло', 'qty': 3, 'price': 9000, 'sum': 27000}],
        },
    }, '2099-01-03')

    assert [action['action'] for action in actions] == ['create_expense', 'create_supply']
    assert actions[0]['description'] == 'Курьеры 95 А'
    assert actions[1]['supplier_name'] == 'Идея'


def test_command_caption_keeps_conversational_agent_path():
    from web_app import _should_use_isolated_whatsapp_document_parser

    media = [{'mime_type': 'image/jpeg', 'data': b'image'}]
    assert _should_use_isolated_whatsapp_document_parser('', media)
    assert not _should_use_isolated_whatsapp_document_parser('удали этот чек', media)
    assert not _should_use_isolated_whatsapp_document_parser('запомни правило', media)

