import json
import os
import pytest
import threading
from unittest.mock import AsyncMock, MagicMock, patch, mock_open

from tests.conftest import TEST_USER_ID

@pytest.fixture
def app_client():
    from web_app import app
    app.config['TESTING'] = True
    return app.test_client()

@pytest.fixture
def mock_whatsapp_config():
    """Mock config settings for WhatsApp testing"""
    with patch("config.WHATSAPP_GROUP_ID", "120363000000000000@g.us"), \
         patch("config.WHATSAPP_USER_ID_MAPPING", TEST_USER_ID), \
         patch("config.WHATSAPP_API_URL", "https://api.green-api.com"), \
         patch("config.WHATSAPP_INSTANCE_ID", "12345"), \
         patch("config.WHATSAPP_API_TOKEN", "mock_token"):
        yield

@pytest.fixture(autouse=True)
def patch_thread():
    """Safely mock threading.Thread to run the WhatsApp worker synchronously without breaking flask_limiter timers"""
    original_thread = threading.Thread
    
    class MockThread(original_thread):
        def start(self):
            target_name = getattr(self._target, '__name__', '')
            if target_name == '_process_whatsapp_async':
                # Run the target function synchronously in the test thread
                self._target(*self._args, **self._kwargs)
            else:
                super().start()
                
    with patch("threading.Thread", new=MockThread):
        yield

def test_whatsapp_webhook_ignored_type(app_client):
    """Webhook ignores non-message webhooks"""
    response = app_client.post(
        '/api/whatsapp/webhook',
        json={"typeWebhook": "stateInstanceChanged"}
    )
    assert response.status_code == 200
    assert response.data.decode('utf-8') == 'Ignored webhook type'

def test_whatsapp_webhook_ignored_chat(app_client, mock_whatsapp_config):
    """Webhook ignores messages from other chats/groups"""
    payload = {
        "typeWebhook": "incomingMessageReceived",
        "senderData": {
            "chatId": "wrong_group@g.us"
        }
    }
    response = app_client.post(
        '/api/whatsapp/webhook',
        json=payload
    )
    assert response.status_code == 200
    assert response.data.decode('utf-8') == 'Ignored non-target chat'

def test_whatsapp_webhook_unconfigured_user(app_client):
    """Webhook returns error or ignores if user mapping is 0/unset"""
    with patch("config.WHATSAPP_GROUP_ID", "120363000000000000@g.us"), \
         patch("config.WHATSAPP_USER_ID_MAPPING", 0):
        
        payload = {
            "typeWebhook": "incomingMessageReceived",
            "senderData": {
                "chatId": "120363000000000000@g.us"
            }
        }
        response = app_client.post(
            '/api/whatsapp/webhook',
            json=payload
        )
        assert response.status_code == 200
        assert response.data.decode('utf-8') == 'User mapping not configured'

def test_whatsapp_webhook_no_content(app_client, mock_whatsapp_config):
    """Webhook ignores messages that have no text and no file"""
    payload = {
        "typeWebhook": "incomingMessageReceived",
        "senderData": {
            "chatId": "120363000000000000@g.us"
        },
        "messageData": {
            "typeMessage": "other"
        }
    }
    response = app_client.post(
        '/api/whatsapp/webhook',
        json=payload
    )
    assert response.status_code == 200
    assert response.data.decode('utf-8') == 'No content to parse'

@patch("web_app.send_whatsapp_message")
@patch("web_app.execute_assistant_actions")
def test_whatsapp_webhook_success_text(mock_execute_actions, mock_send_whatsapp, app_client, db, mock_whatsapp_config):
    """Webhook successfully handles a text message and calls Gemini asynchronously"""
    db.create_user(TEST_USER_ID, "mock_token", "1", "https://mock.joinposter.com/api")
    
    # Mocking Gemini response
    mock_agent_response = {
        "response_text": "Расход сохранен.",
        "actions": [{"action": "create_expense", "amount": 500, "description": "Молоко"}],
        "_model_used": "mock-gemini"
    }
    
    # Mock action execution
    mock_execute_actions.return_value = ("Расход сохранен.", ["Расход: Молоко (500₸, Прочее)"])

    payload = {
        "typeWebhook": "incomingMessageReceived",
        "senderData": {
            "chatId": "120363000000000000@g.us"
        },
        "messageData": {
            "typeMessage": "textMessage",
            "textMessageData": {
                "textMessage": "Бот Расход молоко 500"
            }
        }
    }

    with patch("parser_service.ParserService.call_gemini_assistant_agent", new_callable=AsyncMock, return_value=mock_agent_response) as mock_call_gemini:
        response = app_client.post(
            '/api/whatsapp/webhook',
            json=payload
        )
        
        assert response.status_code == 200
        assert response.data.decode('utf-8') == 'OK'
        
        # Verify Gemini agent was called with correct message text
        mock_call_gemini.assert_called_once()
        assert mock_call_gemini.call_args.kwargs['user_message'] == "Расход молоко 500"
        
        # Verify actions executed
        mock_execute_actions.assert_called_once()
        assert mock_execute_actions.call_args[0][0] == TEST_USER_ID
        assert mock_execute_actions.call_args[0][1] == [{"action": "create_expense", "amount": 500, "description": "Молоко"}]
        
        # Verify message was sent to WhatsApp
        mock_send_whatsapp.assert_called_once()
        sent_chat_id = mock_send_whatsapp.call_args[0][0]
        sent_message = mock_send_whatsapp.call_args[0][1]
        
        assert sent_chat_id == "120363000000000000@g.us"
        assert "🤖 *Ассистент PizzBurg*" in sent_message
        assert "Расход: Молоко" in sent_message


@patch("web_app.send_whatsapp_message")
@patch("web_app.execute_assistant_actions")
def test_whatsapp_webhook_success_outgoing_text(mock_execute_actions, mock_send_whatsapp, app_client, db, mock_whatsapp_config):
    """Webhook successfully handles an outgoing text message (sent from phone) and calls Gemini"""
    db.create_user(TEST_USER_ID, "mock_token", "1", "https://mock.joinposter.com/api")
    
    mock_agent_response = {
        "response_text": "Расход сохранен.",
        "actions": [{"action": "create_expense", "amount": 500, "description": "Молоко"}],
        "_model_used": "mock-gemini"
    }
    
    mock_execute_actions.return_value = ("Расход сохранен.", ["Расход: Молоко (500₸, Прочее)"])

    payload = {
        "typeWebhook": "outgoingMessageReceived",
        "senderData": {
            "chatId": "120363000000000000@g.us"
        },
        "messageData": {
            "typeMessage": "textMessage",
            "textMessageData": {
                "textMessage": "Бот Расход молоко 500"
            }
        }
    }

    with patch("parser_service.ParserService.call_gemini_assistant_agent", new_callable=AsyncMock, return_value=mock_agent_response) as mock_call_gemini:
        response = app_client.post(
            '/api/whatsapp/webhook',
            json=payload
        )
        
        assert response.status_code == 200
        assert response.data.decode('utf-8') == 'OK'
        
        mock_call_gemini.assert_called_once()
        assert mock_call_gemini.call_args.kwargs['user_message'] == "Расход молоко 500"
        mock_execute_actions.assert_called_once()
        mock_send_whatsapp.assert_called_once()

@patch("web_app.send_whatsapp_message")
@patch("web_app.execute_assistant_actions")
@patch("web_app.download_whatsapp_media")
def test_whatsapp_webhook_success_media(mock_download, mock_execute_actions, mock_send_whatsapp, app_client, db, mock_whatsapp_config, tmp_path):
    """Webhook downloads media, passes it to Gemini, and replies successfully"""
    db.create_user(TEST_USER_ID, "mock_token", "1", "https://mock.joinposter.com/api")
    
    # Create a dummy image file
    dummy_file = tmp_path / "test.jpg"
    dummy_file.write_bytes(b"dummy image data")
    
    mock_download.return_value = str(dummy_file)
    
    # Mocking Gemini response
    mock_agent_response = {
        "response_text": "Чек распознан.",
        "actions": [{"action": "create_expense", "amount": 1200, "description": "Сливки"}],
        "_model_used": "mock-gemini"
    }
    mock_execute_actions.return_value = ("Чек распознан.", ["Расход: Сливки (1200₸, Прочее)"])

    payload = {
        "typeWebhook": "incomingFileMessageReceived",
        "senderData": {
            "chatId": "120363000000000000@g.us"
        },
        "messageData": {
            "typeMessage": "imageMessage",
            "imageMessageData": {
                "downloadUrl": "https://api.green-api.com/download/some_id.jpg",
                "fileName": "receipt.jpg",
                "caption": "расход сливки"
            }
        }
    }

    with patch("parser_service.ParserService.call_gemini_assistant_agent", new_callable=AsyncMock, return_value=mock_agent_response) as mock_call_gemini:
        response = app_client.post(
            '/api/whatsapp/webhook',
            json=payload
        )
        
        assert response.status_code == 200
        assert response.data.decode('utf-8') == 'OK'
        
        # Verify media download was called
        from unittest.mock import ANY
        mock_download.assert_called_once_with("https://api.green-api.com/download/some_id.jpg", ANY)
        
        # Verify Gemini agent was called with caption and image data
        mock_call_gemini.assert_called_once()
        assert mock_call_gemini.call_args.kwargs['user_message'] == "расход сливки"
        media_files = mock_call_gemini.call_args.kwargs['media_files']
        assert len(media_files) == 1
        assert media_files[0]['mime_type'] == 'image/jpeg'
        assert media_files[0]['data'] == b"dummy image data"
        
        # Verify WhatsApp message sent
        mock_send_whatsapp.assert_called_once()
        assert "Расход: Сливки" in mock_send_whatsapp.call_args[0][1]


@patch("web_app.send_whatsapp_message")
@patch("web_app.execute_assistant_actions")
def test_whatsapp_webhook_fallback_user(mock_execute_actions, mock_send_whatsapp, app_client, db):
    """Test webhook handles fallback user when configured mapping doesn't exist in DB"""
    # Clean up existing users in the test database to ensure isolation
    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM users")
        conn.commit()
    finally:
        conn.close()

    # Create an active user in the database (e.g. 167084307)
    db.create_user(167084307, "mock_token", "1", "https://mock.joinposter.com/api")
    
    # Configure user mapping to 123456789 (which does not exist in DB)
    with patch("config.WHATSAPP_GROUP_ID", "120363000000000000@g.us"), \
         patch("config.WHATSAPP_USER_ID_MAPPING", 123456789):
        
        # Mocking Gemini response
        mock_agent_response = {
            "response_text": "Расход сохранен.",
            "actions": [],
            "_model_used": "mock-gemini"
        }
        
        mock_execute_actions.return_value = ("Расход сохранен.", [])

        payload = {
            "typeWebhook": "incomingMessageReceived",
            "senderData": {
                "chatId": "120363000000000000@g.us"
            },
            "messageData": {
                "typeMessage": "textMessage",
                "textMessageData": {
                    "textMessage": "Бот Расход молоко 500"
                }
            }
        }

        with patch("parser_service.ParserService.call_gemini_assistant_agent", new_callable=AsyncMock, return_value=mock_agent_response) as mock_call_gemini:
            response = app_client.post(
                '/api/whatsapp/webhook',
                json=payload
            )
            
            assert response.status_code == 200
            assert response.data.decode('utf-8') == 'OK'
            
            # Verify action execution is called with the fallback user ID (167084307)
            mock_execute_actions.assert_called_once()
            assert mock_execute_actions.call_args[0][0] == 167084307


@patch("web_app.send_whatsapp_message")
@patch("web_app.execute_assistant_actions")
def test_whatsapp_webhook_multiple_groups(mock_execute_actions, mock_send_whatsapp, app_client, db):
    """Test webhook handles multiple comma-separated groups correctly"""
    db.create_user(TEST_USER_ID, "mock_token", "1", "https://mock.joinposter.com/api")
    
    # Configure user mapping and two allowed group IDs
    with patch("config.WHATSAPP_GROUP_ID", "group1@g.us, group2@g.us"), \
         patch("config.WHATSAPP_USER_ID_MAPPING", TEST_USER_ID):
        
        mock_agent_response = {
            "response_text": "Расход сохранен.",
            "actions": [],
            "_model_used": "mock-gemini"
        }
        mock_execute_actions.return_value = ("Расход сохранен.", [])

        # 1. First group is allowed
        payload = {
            "typeWebhook": "incomingMessageReceived",
            "senderData": {"chatId": "group1@g.us"},
            "messageData": {
                "typeMessage": "textMessage",
                "textMessageData": {"textMessage": "Бот Тест"}
            }
        }
        with patch("parser_service.ParserService.call_gemini_assistant_agent", new_callable=AsyncMock, return_value=mock_agent_response):
            response = app_client.post('/api/whatsapp/webhook', json=payload)
            assert response.status_code == 200
            assert response.data.decode('utf-8') == 'OK'

        # 2. Second group is allowed
        payload = {
            "typeWebhook": "incomingMessageReceived",
            "senderData": {"chatId": "group2@g.us"},
            "messageData": {
                "typeMessage": "textMessage",
                "textMessageData": {"textMessage": "Бот Тест"}
            }
        }
        with patch("parser_service.ParserService.call_gemini_assistant_agent", new_callable=AsyncMock, return_value=mock_agent_response):
            response = app_client.post('/api/whatsapp/webhook', json=payload)
            assert response.status_code == 200
            assert response.data.decode('utf-8') == 'OK'

        # 3. Third group is ignored
        payload = {
            "typeWebhook": "incomingMessageReceived",
            "senderData": {"chatId": "group3@g.us"},
            "messageData": {
                "typeMessage": "textMessage",
                "textMessageData": {"textMessage": "Бот Тест"}
            }
        }
        response = app_client.post('/api/whatsapp/webhook', json=payload)
        assert response.status_code == 200
        assert response.data.decode('utf-8') == 'Ignored non-target chat'


@patch("web_app.send_whatsapp_message")
@patch("web_app.execute_assistant_actions")
def test_whatsapp_webhook_classifier_ignored(mock_execute_actions, mock_send_whatsapp, app_client, db):
    """Test webhook ignores non-business intents (banter) determined by classifier"""
    db.create_user(TEST_USER_ID, "mock_token", "1", "https://mock.joinposter.com/api")
    
    with patch("config.WHATSAPP_GROUP_ID", "group1@g.us"), \
         patch("config.WHATSAPP_USER_ID_MAPPING", TEST_USER_ID):
        
        # Image payload of random photo (e.g. food photo)
        payload = {
            "typeWebhook": "incomingMessageReceived",
            "senderData": {"chatId": "group1@g.us"},
            "messageData": {
                "typeMessage": "imageMessage",
                "imageMessageData": {
                    "downloadUrl": "https://example.com/jalapeno.jpg",
                    "fileName": "jalapeno.jpg",
                    "caption": "Халапеньо на кухне"
                }
            }
        }
        
        # Mock download media and check_message_intent returning False
        with patch("web_app.download_whatsapp_media", return_value="temp.jpg"), \
             patch("builtins.open", mock_open(read_data=b"fake image data")), \
             patch("parser_service.ParserService.check_message_intent", new_callable=AsyncMock, return_value=False) as mock_check_intent, \
             patch("parser_service.ParserService.call_gemini_assistant_agent") as mock_call_gemini, \
             patch("os.unlink") as mock_unlink:
            
            response = app_client.post('/api/whatsapp/webhook', json=payload)
            assert response.status_code == 200
            assert response.data.decode('utf-8') == 'OK'
            
            # Classifier should be called since it is a photo with no "Бот" prefix
            mock_check_intent.assert_called_once()
            
            # The main Gemini agent should NOT be called
            mock_call_gemini.assert_not_called()
            
            # No message should be sent to WhatsApp group
            mock_send_whatsapp.assert_not_called()


@patch("web_app.send_whatsapp_message")
@patch("web_app.execute_assistant_actions")
def test_whatsapp_webhook_classifier_allowed(mock_execute_actions, mock_send_whatsapp, app_client, db):
    """Test webhook processes business intents determined by classifier"""
    db.create_user(TEST_USER_ID, "mock_token", "1", "https://mock.joinposter.com/api")
    
    with patch("config.WHATSAPP_GROUP_ID", "group1@g.us"), \
         patch("config.WHATSAPP_USER_ID_MAPPING", TEST_USER_ID):
        
        # Image payload of a real invoice
        payload = {
            "typeWebhook": "incomingMessageReceived",
            "senderData": {"chatId": "group1@g.us"},
            "messageData": {
                "typeMessage": "imageMessage",
                "imageMessageData": {
                    "downloadUrl": "https://example.com/invoice.jpg",
                    "fileName": "invoice.jpg",
                    "caption": "Накладная от Япоша"
                }
            }
        }
        
        # Mock Gemini response
        mock_agent_response = {
            "response_text": "Поставка Япоша на 15000 зафиксирована.",
            "actions": [{"action": "create_expense", "amount": 15000, "description": "Япоша", "expense_type": "supply"}],
            "_model_used": "mock-gemini"
        }
        
        mock_execute_actions.return_value = ("Поставка Япоша зафиксирована.", ["Поставка: Япоша (15000₸)"])
        
        with patch("web_app.download_whatsapp_media", return_value="temp.jpg"), \
             patch("builtins.open", mock_open(read_data=b"fake image data")), \
             patch("shutil.copy2"), \
             patch("os.makedirs"), \
             patch("parser_service.ParserService.check_message_intent", new_callable=AsyncMock, return_value=True) as mock_check_intent, \
             patch("parser_service.ParserService.call_gemini_assistant_agent", new_callable=AsyncMock, return_value=mock_agent_response) as mock_call_gemini, \
             patch("os.unlink") as mock_unlink:
            
            response = app_client.post('/api/whatsapp/webhook', json=payload)
            assert response.status_code == 200
            assert response.data.decode('utf-8') == 'OK'
            
            # Classifier should be called
            mock_check_intent.assert_called_once()
            
            # The main Gemini agent should be called
            mock_call_gemini.assert_called_once()
            
            # Actions should be executed
            mock_execute_actions.assert_called_once()
            
            # Message should be sent to WhatsApp group since drafts were created
            mock_send_whatsapp.assert_called_once()


@patch("web_app.send_whatsapp_message")
@patch("web_app.execute_assistant_actions")
def test_whatsapp_webhook_text_no_prefix_business(mock_execute_actions, mock_send_whatsapp, app_client, db, mock_whatsapp_config):
    """Text message without prefix but with business intent is classified as business and processed"""
    db.create_user(TEST_USER_ID, "mock_token", "1", "https://mock.joinposter.com/api")
    
    mock_agent_response = {
        "response_text": "Расход сохранен.",
        "actions": [{"action": "create_expense", "amount": 500, "description": "Молоко"}],
        "_model_used": "mock-gemini"
    }
    mock_execute_actions.return_value = ("Расход сохранен.", ["Расход: Молоко (500₸)"])

    payload = {
        "typeWebhook": "incomingMessageReceived",
        "senderData": {"chatId": "120363000000000000@g.us"},
        "messageData": {
            "typeMessage": "textMessage",
            "textMessageData": {
                "textMessage": "Расход молоко 500"
            }
        }
    }

    with patch("parser_service.ParserService.check_message_intent", new_callable=AsyncMock, return_value=True) as mock_check_intent, \
         patch("parser_service.ParserService.call_gemini_assistant_agent", new_callable=AsyncMock, return_value=mock_agent_response) as mock_call_gemini:
        
        response = app_client.post('/api/whatsapp/webhook', json=payload)
        assert response.status_code == 200
        assert response.data.decode('utf-8') == 'OK'
        
        mock_check_intent.assert_called_once()
        mock_call_gemini.assert_called_once()
        mock_execute_actions.assert_called_once()
        mock_send_whatsapp.assert_called_once()


@patch("web_app.send_whatsapp_message")
@patch("web_app.execute_assistant_actions")
def test_whatsapp_webhook_text_no_prefix_banter(mock_execute_actions, mock_send_whatsapp, app_client, db, mock_whatsapp_config):
    """Text message without prefix and without business intent (banter) is classified as non-business and ignored"""
    db.create_user(TEST_USER_ID, "mock_token", "1", "https://mock.joinposter.com/api")

    payload = {
        "typeWebhook": "incomingMessageReceived",
        "senderData": {"chatId": "120363000000000000@g.us"},
        "messageData": {
            "typeMessage": "textMessage",
            "textMessageData": {
                "textMessage": "спасибо, хорошего дня"
            }
        }
    }

    with patch("parser_service.ParserService.check_message_intent", new_callable=AsyncMock, return_value=False) as mock_check_intent, \
         patch("parser_service.ParserService.call_gemini_assistant_agent") as mock_call_gemini:
        
        response = app_client.post('/api/whatsapp/webhook', json=payload)
        assert response.status_code == 200
        assert response.data.decode('utf-8') == 'OK'
        
        mock_check_intent.assert_called_once()
        mock_call_gemini.assert_not_called()
        mock_execute_actions.assert_not_called()
        mock_send_whatsapp.assert_not_called()


@patch("web_app.send_whatsapp_message")
@patch("web_app.execute_assistant_actions")
@patch("web_app.download_whatsapp_media")
@patch("web_app.transcribe_voice_file")
def test_whatsapp_webhook_success_audio(mock_transcribe, mock_download, mock_execute_actions, mock_send_whatsapp, app_client, db, mock_whatsapp_config, tmp_path):
    """Voice note message is downloaded, transcribed via Whisper, and then processed normally"""
    db.create_user(TEST_USER_ID, "mock_token", "1", "https://mock.joinposter.com/api")
    
    # Create a dummy audio file
    dummy_file = tmp_path / "voice.ogg"
    dummy_file.write_bytes(b"fake audio data")
    
    mock_download.return_value = str(dummy_file)
    mock_transcribe.return_value = "Бот Расход молоко 500"
    
    # Mocking Gemini response
    mock_agent_response = {
        "response_text": "Расход сохранен.",
        "actions": [{"action": "create_expense", "amount": 500, "description": "Молоко"}],
        "_model_used": "mock-gemini"
    }
    mock_execute_actions.return_value = ("Расход сохранен.", ["Расход: Молоко (500₸)"])

    payload = {
        "typeWebhook": "outgoingMessageReceived",
        "senderData": {
            "chatId": "120363000000000000@g.us"
        },
        "messageData": {
            "typeMessage": "audioMessage",
            "audioMessageData": {
                "fileUrl": "https://api.green-api.com/download/some_voice.ogg",
                "fileName": "voice.ogg"
            }
        }
    }

    with patch("parser_service.ParserService.call_gemini_assistant_agent", new_callable=AsyncMock, return_value=mock_agent_response) as mock_call_gemini:
        response = app_client.post(
            '/api/whatsapp/webhook',
            json=payload
        )
        
        assert response.status_code == 200
        assert response.data.decode('utf-8') == 'OK'
        
        # Verify media download was called
        from unittest.mock import ANY
        mock_download.assert_called_once_with("https://api.green-api.com/download/some_voice.ogg", ANY)
        
        # Verify Whisper transcription was called
        mock_transcribe.assert_called_once_with(str(dummy_file), TEST_USER_ID)
        
        # Verify Gemini agent was called with transcribed command (prefix stripped)
        mock_call_gemini.assert_called_once()
        assert mock_call_gemini.call_args.kwargs['user_message'] == "Расход молоко 500"
        
        # Verify WhatsApp message sent
        mock_send_whatsapp.assert_called_once()
        assert "Расход: Молоко" in mock_send_whatsapp.call_args[0][1]




