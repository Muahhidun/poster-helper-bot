"""Tests for per-file document processing: each uploaded file is processed
as a separate Gemini call to prevent cross-contamination between invoices."""
import io
import json
import pytest
from unittest.mock import AsyncMock, patch, call

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
    supplies = db.get_supply_drafts(TEST_USER_ID, status="all")
    for s in supplies:
        if marker in (s.get('supplier_name') or ''):
            db.delete_supply_draft(s['id'], telegram_user_id=TEST_USER_ID)


def test_multiple_files_separate_gemini_calls(app_client, db):
    """When 2+ files are uploaded, Gemini is called once per file."""
    db.create_user(TEST_USER_ID, "mock_token", "1", "https://mock.joinposter.com/api")
    _login(app_client)

    call_count = 0

    async def _mock_agent(**kwargs):
        nonlocal call_count
        call_count += 1
        return {
            "response_text": f"Файл {call_count} обработан.",
            "actions": [],
            "_model_used": "mock-gemini",
        }

    with patch("parser_service.ParserService.call_gemini_assistant_agent",
               new_callable=AsyncMock, side_effect=_mock_agent):
        response = app_client.post(
            '/api/assistant/message',
            data={
                'message': 'две накладные',
                'date': '2026-06-11',
                'files': [
                    (io.BytesIO(b'\x89PNG fake image 1'), 'invoice1.png', 'image/png'),
                    (io.BytesIO(b'\x89PNG fake image 2'), 'invoice2.png', 'image/png'),
                ],
            },
            content_type='multipart/form-data',
        )

    assert response.status_code == 200
    assert call_count == 2
    data = json.loads(response.data)
    assert 'invoice1.png' in data['response_text']
    assert 'invoice2.png' in data['response_text']


def test_single_file_single_call(app_client, db):
    """Single file still uses a single Gemini call (no overhead)."""
    db.create_user(TEST_USER_ID, "mock_token", "1", "https://mock.joinposter.com/api")
    _login(app_client)

    call_count = 0

    async def _mock_agent(**kwargs):
        nonlocal call_count
        call_count += 1
        return {
            "response_text": "OK.",
            "actions": [],
            "_model_used": "mock-gemini",
        }

    with patch("parser_service.ParserService.call_gemini_assistant_agent",
               new_callable=AsyncMock, side_effect=_mock_agent):
        response = app_client.post(
            '/api/assistant/message',
            data={
                'message': 'одна накладная',
                'date': '2026-06-11',
                'files': (io.BytesIO(b'\x89PNG fake'), 'single.png', 'image/png'),
            },
            content_type='multipart/form-data',
        )

    assert response.status_code == 200
    assert call_count == 1


def test_multifile_actions_aggregated(app_client, db):
    """Actions from separate file calls are merged and all executed."""
    db.create_user(TEST_USER_ID, "mock_token", "1", "https://mock.joinposter.com/api")
    _login(app_client)
    _cleanup(db, 'mftest')

    responses = [
        {
            "response_text": "Накладная 1.",
            "actions": [{"action": "create_expense", "amount": 5000,
                         "description": "Metro mftest", "expense_type": "transaction",
                         "category": "Прочее", "source": "kaspi"}],
            "_model_used": "mock-gemini",
        },
        {
            "response_text": "Накладная 2.",
            "actions": [{"action": "create_expense", "amount": 3000,
                         "description": "Smoll mftest", "expense_type": "transaction",
                         "category": "Прочее", "source": "cash"}],
            "_model_used": "mock-gemini",
        },
    ]
    call_idx = 0

    async def _mock_agent(**kwargs):
        nonlocal call_idx
        resp = responses[call_idx]
        call_idx += 1
        return resp

    with patch("parser_service.ParserService.call_gemini_assistant_agent",
               new_callable=AsyncMock, side_effect=_mock_agent):
        response = app_client.post(
            '/api/assistant/message',
            data={
                'message': 'две накладные',
                'date': '2026-06-11',
                'files': [
                    (io.BytesIO(b'img1'), 'metro.jpg', 'image/jpeg'),
                    (io.BytesIO(b'img2'), 'smoll.jpg', 'image/jpeg'),
                ],
            },
            content_type='multipart/form-data',
        )

    assert response.status_code == 200

    drafts = db.get_expense_drafts(TEST_USER_ID, status="all")
    created = {d['description']: d for d in drafts if 'mftest' in (d.get('description') or '')}
    assert len(created) == 2
    assert float(created['Metro mftest']['amount']) == 5000.0
    assert float(created['Smoll mftest']['amount']) == 3000.0

    _cleanup(db, 'mftest')
