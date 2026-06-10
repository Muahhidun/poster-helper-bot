"""Tests for handwritten expense sheet flow: photo of a notebook page →
one create_expense action per line → separate drafts with normalized categories."""
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


def _cleanup(db, marker):
    drafts = db.get_expense_drafts(TEST_USER_ID, status="all")
    for d in drafts:
        if marker in (d.get('description') or ''):
            db.delete_expense_draft(d['id'], telegram_user_id=TEST_USER_ID)


def test_handwritten_sheet_creates_separate_drafts(app_client, db):
    """Photo of «хлеб 500, такси 1200, Сабитов 15000» → 3 drafts with correct
    amounts, sources and types"""
    db.create_user(TEST_USER_ID, "mock_token", "1", "https://mock.joinposter.com/api")
    _login(app_client)
    _cleanup(db, 'hwtest')

    # The agent (vision model) is mocked: returns one create_expense per
    # handwritten line, as the system prompt instructs.
    mock_agent_response = {
        "response_text": "Распознал 3 строки расходов с фото.",
        "actions": [
            {
                "action": "create_expense",
                "amount": 500,
                "description": "хлеб hwtest",
                "expense_type": "supply",
                "category": "Прочее",
                "source": "cash",
            },
            {
                "action": "create_expense",
                "amount": 1200,
                "description": "такси hwtest",
                "expense_type": "transaction",
                "category": "Транспорт",
                "source": "cash",
            },
            {
                "action": "create_expense",
                "amount": 15000,
                "description": "Сабитов hwtest",
                "expense_type": "transaction",
                "category": "Зарплаты",
                "source": "cash",
            },
        ],
        "_model_used": "mock-gemini",
    }

    with patch("parser_service.ParserService.call_gemini_assistant_agent",
               new_callable=AsyncMock, return_value=mock_agent_response):
        response = app_client.post('/api/assistant/message', data={
            'message': '',
            'date': '2026-06-10',
        })

    assert response.status_code == 200
    data = json.loads(response.data.decode('utf-8'))
    assert data['success'] is True

    drafts = db.get_expense_drafts(TEST_USER_ID, status="all")
    created = {d['description']: d for d in drafts if 'hwtest' in (d.get('description') or '')}

    assert len(created) == 3
    assert float(created['хлеб hwtest']['amount']) == 500.0
    assert created['хлеб hwtest']['expense_type'] == 'supply'
    assert float(created['такси hwtest']['amount']) == 1200.0
    assert created['такси hwtest']['expense_type'] == 'transaction'
    assert float(created['Сабитов hwtest']['amount']) == 15000.0
    assert all(d['source'] == 'cash' for d in created.values())

    _cleanup(db, 'hwtest')


def test_category_normalized_via_matcher(app_client, db):
    """Free-form category from the model is fuzzy-matched onto the real
    Poster category catalog (e.g. «кассир» → «Кассиры»)"""
    db.create_user(TEST_USER_ID, "mock_token", "1", "https://mock.joinposter.com/api")
    _login(app_client)
    _cleanup(db, 'cattest')

    mock_agent_response = {
        "response_text": "Записал зарплату кассира.",
        "actions": [
            {
                "action": "create_expense",
                "amount": 10000,
                "description": "Мадина cattest",
                "expense_type": "transaction",
                "category": "кассир",
                "source": "cash",
            },
        ],
        "_model_used": "mock-gemini",
    }

    with patch("parser_service.ParserService.call_gemini_assistant_agent",
               new_callable=AsyncMock, return_value=mock_agent_response):
        response = app_client.post('/api/assistant/message', data={
            'message': 'Мадина кассир 10000',
            'date': '2026-06-10',
        })

    assert response.status_code == 200

    drafts = db.get_expense_drafts(TEST_USER_ID, status="all")
    created = [d for d in drafts if 'cattest' in (d.get('description') or '')]
    assert len(created) == 1
    # global alias CSV maps "кассир" → "Кассиры"
    assert created[0]['category'] == 'Кассиры'

    _cleanup(db, 'cattest')


def test_income_and_kaspi_source_preserved(app_client, db):
    """Lines with «+» (income) and Kaspi transfers keep their attributes"""
    db.create_user(TEST_USER_ID, "mock_token", "1", "https://mock.joinposter.com/api")
    _login(app_client)
    _cleanup(db, 'srctest')

    mock_agent_response = {
        "response_text": "Записал.",
        "actions": [
            {
                "action": "create_expense",
                "amount": 7000,
                "description": "перевод каспи srctest",
                "expense_type": "transaction",
                "category": "Прочее",
                "source": "kaspi",
            },
        ],
        "_model_used": "mock-gemini",
    }

    with patch("parser_service.ParserService.call_gemini_assistant_agent",
               new_callable=AsyncMock, return_value=mock_agent_response):
        app_client.post('/api/assistant/message', data={
            'message': 'каспи перевод 7000',
            'date': '2026-06-10',
        })

    drafts = db.get_expense_drafts(TEST_USER_ID, status="all")
    created = [d for d in drafts if 'srctest' in (d.get('description') or '')]
    assert len(created) == 1
    assert created[0]['source'] == 'kaspi'

    _cleanup(db, 'srctest')


def test_supply_items_reconciled_before_draft(app_client, db):
    """OCR math errors in supply items are fixed before hitting the DB:
    qty 84 with sum 26880 at price 3200 becomes qty 8.4"""
    db.create_user(TEST_USER_ID, "mock_token", "1", "https://mock.joinposter.com/api")
    _login(app_client)
    _cleanup(db, 'rectest')

    mock_agent_response = {
        "response_text": "Создаю поставку.",
        "actions": [
            {
                "action": "create_supply",
                "supplier_name": "Денер Караганда rectest",
                "total_sum": 26880.0,
                "source": "cash",
                "items": [
                    # OCR lost the decimal point: 8.4 → 84
                    {"name": "Мясо", "qty": 84, "price": 3200.0, "sum": 26880.0},
                ],
            },
        ],
        "_model_used": "mock-gemini",
    }

    with patch("parser_service.ParserService.call_gemini_assistant_agent",
               new_callable=AsyncMock, return_value=mock_agent_response):
        response = app_client.post('/api/assistant/message', data={
            'message': 'фото накладной',
            'date': '2026-06-10',
        })

    assert response.status_code == 200

    # supplier name may be canonicalized by fuzzy matching — locate the draft
    # through the linked expense draft (created with amount == invoice total)
    expenses = db.get_expense_drafts(TEST_USER_ID, status="all")
    linked_expense = [
        e for e in expenses
        if e.get('expense_type') == 'supply' and float(e.get('amount') or 0) == pytest.approx(26880.0)
    ]
    assert len(linked_expense) >= 1
    expense_id = linked_expense[-1]['id']

    supplies = db.get_supply_drafts(TEST_USER_ID, status="all")
    target = [s for s in supplies if s.get('linked_expense_draft_id') == expense_id]
    assert len(target) == 1

    draft_with_items = db.get_supply_draft_with_items(target[0]['id'])
    items = draft_with_items.get('items', [])
    assert len(items) == 1
    assert float(items[0]['quantity']) == pytest.approx(8.4)
    assert float(items[0]['price_per_unit']) == pytest.approx(3200.0)

    # cleanup supply + linked expense
    db.delete_supply_draft(target[0]['id'], telegram_user_id=TEST_USER_ID)
    db.delete_expense_draft(expense_id, telegram_user_id=TEST_USER_ID)
    _cleanup(db, 'rectest')
