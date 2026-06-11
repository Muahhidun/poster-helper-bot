"""Tests for Excel (Kaspi statement) processing in the assistant chat:
xlsx files are parsed server-side and passed to the model as text,
since Gemini cannot read xlsx binaries."""
import io
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


def _make_kaspi_xlsx():
    """Build a minimal Kaspi statement workbook in memory."""
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    # Header row that the parser looks for
    ws.append(["№ документа", "Дата операции", "Дебет", "Кредит",
               "Наименование бенефициара", "ИИК", "БИК", "КНП", "Назначение платежа"])
    ws.append(["1", "2", "3", "4", "5", "6", "7", "8", "9"])  # column numbers row
    ws.append(["DOC1", "11.06.2026", 46000, None,
               "ИП ЕРЖАНОВА", "KZ123", "CASPKZKA", "710", "ИП ЕРЖАНОВА. Оплата с Kaspi QR"])
    ws.append(["DOC2", "11.06.2026", 1500, None,
               "ЯНДЕКС ТАКСИ", "KZ456", "CASPKZKA", "710", "Оплата такси"])
    ws.append(["Итого", None, 47500, None, None, None, None, None, None])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def test_xlsx_parsed_to_text_for_assistant(app_client, db):
    """Uploading an xlsx: rows are parsed and injected as text into the agent message."""
    db.create_user(TEST_USER_ID, "mock_token", "1", "https://mock.joinposter.com/api")
    _login(app_client)

    captured = {}

    async def _mock_agent(**kwargs):
        captured['user_message'] = kwargs.get('user_message', '')
        captured['media_files'] = kwargs.get('media_files', [])
        return {"response_text": "Выписка обработана.", "actions": [], "_model_used": "mock-gemini"}

    with patch("parser_service.ParserService.call_gemini_assistant_agent",
               new_callable=AsyncMock, side_effect=_mock_agent):
        response = app_client.post(
            '/api/assistant/message',
            data={
                'message': 'выписка каспи',
                'date': '2026-06-11',
                'files': (_make_kaspi_xlsx(), 'statement.xlsx',
                          'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
            },
            content_type='multipart/form-data',
        )

    assert response.status_code == 200
    msg = captured['user_message']
    assert 'Excel-выписки Kaspi' in msg
    assert '46000' in msg
    assert '1500' in msg
    assert 'ЕРЖАНОВА' in msg
    # Binary must NOT be sent to the model as media
    assert captured['media_files'] == []


def test_broken_xlsx_reports_error_text(app_client, db):
    """Corrupt xlsx does not crash the endpoint; model gets an error note."""
    db.create_user(TEST_USER_ID, "mock_token", "1", "https://mock.joinposter.com/api")
    _login(app_client)

    captured = {}

    async def _mock_agent(**kwargs):
        captured['user_message'] = kwargs.get('user_message', '')
        return {"response_text": "OK.", "actions": [], "_model_used": "mock-gemini"}

    with patch("parser_service.ParserService.call_gemini_assistant_agent",
               new_callable=AsyncMock, side_effect=_mock_agent):
        response = app_client.post(
            '/api/assistant/message',
            data={
                'message': 'выписка',
                'date': '2026-06-11',
                'files': (io.BytesIO(b'not a real xlsx'), 'broken.xlsx',
                          'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
            },
            content_type='multipart/form-data',
        )

    assert response.status_code == 200
    assert 'Не удалось разобрать Excel-файл' in captured['user_message']
