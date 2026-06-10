"""Tests for PDF upload handling in /api/assistant/message:
digital PDFs feed their exact text layer into the model context,
scanned PDFs flow through as media for the Vision path."""
import io
import json
import pytest
from unittest.mock import AsyncMock, patch

from tests.conftest import TEST_USER_ID
from tests.test_pdf_utils import _make_text_pdf, _make_blank_pdf


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


def test_digital_pdf_text_injected_into_agent_context(app_client, db):
    """Text layer of an e-invoice must reach the agent verbatim, so numbers
    are taken from exact text instead of OCR"""
    db.create_user(TEST_USER_ID, "mock_token", "1", "https://mock.joinposter.com/api")
    _login(app_client)

    pdf_data = _make_text_pdf([
        "INVOICE Metro",
        "Fri dolki 10.0 x 1200.00 = 12000.00",
        "TOTAL: 12000.00",
    ])

    mock_agent_response = {
        "response_text": "Накладная обработана.",
        "actions": [],
        "_model_used": "mock-gemini",
    }

    with patch("parser_service.ParserService.call_gemini_assistant_agent",
               new_callable=AsyncMock, return_value=mock_agent_response) as mock_agent:
        response = app_client.post(
            '/api/assistant/message',
            data={
                'message': 'накладная от Метро',
                'date': '2026-06-10',
                'files': (io.BytesIO(pdf_data), 'invoice.pdf', 'application/pdf'),
            },
            content_type='multipart/form-data',
        )

    assert response.status_code == 200
    assert json.loads(response.data.decode('utf-8'))['success'] is True

    # The agent received the user text enriched with the extracted PDF text
    call_kwargs = mock_agent.call_args.kwargs
    agent_msg = call_kwargs['user_message']
    assert 'накладная от Метро' in agent_msg
    assert 'Извлечённый текст из PDF' in agent_msg
    assert 'Fri dolki' in agent_msg
    assert '12000.00' in agent_msg

    # The PDF itself is still passed as media (Gemini reads PDF natively)
    media = call_kwargs['media_files']
    assert any(m['mime_type'] == 'application/pdf' for m in media)


def test_scanned_pdf_passed_as_media(app_client, db):
    """Scanned PDF (no text layer): nothing to inject, the file goes to the
    Vision path (raw PDF, or page images when poppler is available)"""
    db.create_user(TEST_USER_ID, "mock_token", "1", "https://mock.joinposter.com/api")
    _login(app_client)

    mock_agent_response = {
        "response_text": "Сканированная накладная обработана.",
        "actions": [],
        "_model_used": "mock-gemini",
    }

    with patch("parser_service.ParserService.call_gemini_assistant_agent",
               new_callable=AsyncMock, return_value=mock_agent_response) as mock_agent:
        response = app_client.post(
            '/api/assistant/message',
            data={
                'message': 'скан накладной',
                'date': '2026-06-10',
                'files': (io.BytesIO(_make_blank_pdf()), 'scan.pdf', 'application/pdf'),
            },
            content_type='multipart/form-data',
        )

    assert response.status_code == 200

    call_kwargs = mock_agent.call_args.kwargs
    # No text was injected (nothing to extract)
    assert 'Извлечённый текст из PDF' not in call_kwargs['user_message']
    # But the document reached the model as media
    media = call_kwargs['media_files']
    assert len(media) >= 1
    assert all(m['mime_type'] in ('application/pdf', 'image/jpeg') for m in media)


def test_corrupt_pdf_does_not_break_route(app_client, db):
    """A broken file must not crash the endpoint"""
    db.create_user(TEST_USER_ID, "mock_token", "1", "https://mock.joinposter.com/api")
    _login(app_client)

    mock_agent_response = {
        "response_text": "Не смог прочитать файл.",
        "actions": [],
        "_model_used": "mock-gemini",
    }

    with patch("parser_service.ParserService.call_gemini_assistant_agent",
               new_callable=AsyncMock, return_value=mock_agent_response):
        response = app_client.post(
            '/api/assistant/message',
            data={
                'message': 'вот файл',
                'date': '2026-06-10',
                'files': (io.BytesIO(b'definitely not a pdf'), 'bad.pdf', 'application/pdf'),
            },
            content_type='multipart/form-data',
        )

    assert response.status_code == 200
    assert json.loads(response.data.decode('utf-8'))['success'] is True
