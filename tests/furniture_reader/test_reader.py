"""Contract tests using fake events, private temp archives and patched accounting edges."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3
from unittest.mock import Mock

import pytest

from furniture_reader.config import ReaderSettings
from furniture_reader.service import FurnitureReader, normalize_event

READ_TOKEN = 'test-reader-token-' + 'r' * 32
INGEST_TOKEN = 'test-ingest-token-' + 'i' * 32
A = 'accounting-test@g.us'
F = 'furniture-test@g.us'
OTHER = 'other-test@g.us'


@pytest.fixture
def settings(tmp_path):
    return ReaderSettings(enabled=True, allowed_chat_ids=frozenset({F}),
                          read_chat_ids=frozenset({F}),
                          read_token_sha256=hashlib.sha256(READ_TOKEN.encode()).hexdigest(),
                          webhook_token_sha256=hashlib.sha256(INGEST_TOKEN.encode()).hexdigest(),
                          instance_id='test-instance', storage_dir=str(tmp_path / 'private'))


def event(chat=F, message_id='message-test-1', outgoing=False, media=False):
    payload = {'typeWebhook': 'outgoingMessageReceived' if outgoing else 'incomingMessageReceived',
               'instanceData': {'idInstance': 'test-instance'}, 'timestamp': 1700000000,
               'senderData': {'chatId': chat, 'senderName': 'not copied'}, 'idMessage': message_id,
               'messageData': {'typeMessage': 'textMessage',
                               'textMessageData': {'textMessage': 'Мебельная накладная'}}}
    if media:
        payload['typeWebhook'] = 'outgoingFileMessageReceived' if outgoing else 'incomingFileMessageReceived'
        payload['messageData'] = {'typeMessage': 'imageMessage', 'fileMessageData': {
            'downloadUrl': 'https://media.example.test/private?signature=not-for-reader',
            'fileName': '../../invoice.png', 'mimeType': 'image/png', 'caption': 'Накладная'}}
    return payload


@pytest.fixture
def reader(settings, tmp_path):
    return FurnitureReader(settings, lambda: A, str(tmp_path / 'application'))


@pytest.fixture
def client(reader, monkeypatch):
    import web_app
    web_app.app.config.update(TESTING=True, RATELIMIT_ENABLED=False)
    monkeypatch.setitem(web_app.app.extensions, 'furniture_reader', reader)
    monkeypatch.setattr(web_app.config, 'WHATSAPP_GROUP_ID', A)
    monkeypatch.setattr(web_app.config, 'WHATSAPP_USER_ID_MAPPING', 999999)
    for name in ('get_database', 'execute_assistant_actions', 'send_whatsapp_message',
                 'process_whatsapp_queue', 'download_whatsapp_media'):
        monkeypatch.setattr(web_app, name, Mock(side_effect=AssertionError('Accounting side effect forbidden')))
    return web_app.app.test_client()


def ingest(client, payload=None, token=INGEST_TOKEN):
    return client.post('/api/whatsapp/webhook', json=event() if payload is None else payload,
                       headers={'Authorization': f'Bearer {token}'})


def get(client, path='/messages', token=READ_TOKEN):
    return client.get('/api/furniture-reader' + path, headers={'Authorization': f'Bearer {token}'})


def test_defaults_are_closed_without_io(tmp_path):
    cfg = ReaderSettings.from_env({})
    assert not cfg.enabled and not cfg.allowed_chat_ids and not cfg.read_chat_ids
    service = FurnitureReader(cfg, lambda: '', str(tmp_path))
    assert service.route_webhook(event(), '') is None
    assert service.media_tick() == 0
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('outgoing,media', [(False, False), (True, False), (False, True), (True, True)])
def test_furniture_archives_without_accounting_or_send(client, reader, outgoing, media):
    response = ingest(client, event(outgoing=outgoing, media=media))
    assert response.status_code == 200 and response.text == 'Archived'
    rows = get(client).json['messages']
    assert len(rows) == 1 and rows[0]['direction'] == ('outgoing' if outgoing else 'incoming')
    if media:
        assert rows[0]['attachment']['state'] == 'disabled'
    serialized = json.dumps(rows)
    for forbidden in ('download_url', 'signature', 'senderName', 'not copied', 'local_name'):
        assert forbidden not in serialized


def test_unknown_chat_is_not_saved(client, settings):
    assert ingest(client, event(OTHER)).text == 'Ignored non-target chat'
    assert not Path(settings.storage_dir).exists()


def test_dedup_and_stable_pagination(client):
    assert ingest(client).text == 'Archived'
    assert ingest(client).text == 'Duplicate'
    assert ingest(client, event(message_id='message-2')).text == 'Archived'
    first = get(client, '/messages?limit=1').json
    second = get(client, f"/messages?after_id={first['next_after_id']}").json
    assert len(first['messages']) == len(second['messages']) == 1
    assert first['messages'][0]['message_id'] != second['messages'][0]['message_id']


@pytest.mark.parametrize('token', ['', READ_TOKEN, 'wrong'])
def test_ingest_auth_fail_closed(client, settings, token):
    assert ingest(client, token=token).status_code == 401
    assert not Path(settings.storage_dir).exists()


def test_wrong_instance_and_missing_message_id(client, settings):
    payload = event()
    payload['instanceData']['idInstance'] = 'wrong'
    assert ingest(client, payload).status_code == 403
    payload = event()
    del payload['idMessage']
    assert ingest(client, payload).status_code == 400
    assert not Path(settings.storage_dir).exists()


@pytest.mark.parametrize('bad_type', ['', 'x' * 65, 'text\x00Message', None])
def test_malformed_message_type_is_rejected_before_storage(client, settings, bad_type):
    payload = event()
    payload['messageData']['typeMessage'] = bad_type
    assert ingest(client, payload).status_code == 400
    assert not Path(settings.storage_dir).exists()


@pytest.mark.parametrize('media_enabled', [False, True])
def test_attachment_without_declared_mime_is_archived(client, reader, media_enabled):
    reader.settings = replace(reader.settings, media_enabled=media_enabled)
    payload = event(media=True)
    del payload['messageData']['fileMessageData']['mimeType']
    payload['messageData']['fileMessageData']['fileName'] = '..'
    assert ingest(client, payload).text == 'Archived'
    attachment = get(client).json['messages'][0]['attachment']
    assert attachment['declared_mime_type'] == ''
    assert attachment['filename'] == 'attachment'
    assert attachment['state'] == ('pending' if media_enabled else 'disabled')


def test_archive_error_never_falls_back(client, reader, monkeypatch):
    monkeypatch.setattr(reader, 'store', Mock(side_effect=OSError('sensitive example')))
    response = ingest(client)
    assert response.status_code == 503 and 'sensitive' not in response.text


@pytest.mark.parametrize('failure', [OSError('synthetic disk full marker'),
                                   sqlite3.OperationalError('database is locked: synthetic marker')])
def test_archive_failure_cannot_claim_accounting_jobs_or_prevent_accounting_routing(client, reader, monkeypatch, failure):
    import web_app
    broken = Mock()
    broken.save_message.side_effect = failure
    monkeypatch.setattr(reader, 'store', lambda: broken)
    response = ingest(client)
    assert response.status_code == 503 and response.text == 'Furniture archive unavailable'
    assert 'marker' not in response.text
    db = Mock()
    db.get_user.return_value = {'telegram_user_id': 999999}
    db.enqueue_whatsapp_job.return_value = {'duplicate': False, 'job_id': 1, 'batch_id': 1}
    monkeypatch.setattr(web_app, 'get_database', lambda: db)
    assert ingest(client, event(A), token='legacy-accountant-auth').text == 'Queued'
    assert broken.save_message.call_count == 1
    db.enqueue_whatsapp_job.assert_called_once()


def test_accounting_retains_exact_legacy_queue_behavior(client, reader, monkeypatch):
    import web_app
    db = Mock()
    db.get_user.return_value = {'telegram_user_id': 999999}
    db.enqueue_whatsapp_job.return_value = {'duplicate': False, 'job_id': 1, 'batch_id': 1}
    monkeypatch.setattr(web_app, 'get_database', lambda: db)
    payload = event(A)
    response = ingest(client, payload, token='accountant-does-not-use-reader-auth')
    assert response.status_code == 200 and response.text == 'Queued'
    db.enqueue_whatsapp_job.assert_called_once()
    assert json.loads(db.enqueue_whatsapp_job.call_args.kwargs['payload_json']) == payload
    assert not Path(reader.settings.storage_dir).exists()
    db.enqueue_whatsapp_job.return_value['duplicate'] = True
    assert ingest(client, payload).text == 'Duplicate'


def test_disabled_feature_keeps_legacy_filter(client, reader):
    reader.settings = replace(reader.settings, enabled=False)
    assert ingest(client).text == 'Ignored non-target chat'
    assert get(client).status_code == 404
    assert not Path(reader.settings.storage_dir).exists()


@pytest.mark.parametrize('accounting', ['', A + ',' + F])
def test_invalid_group_configuration_blocks_archive(reader, accounting):
    reader.accounting_chat_ids = lambda: accounting
    assert not reader.configured()
    if accounting:
        # Explicit accountant groups keep their original route; never dual-write.
        assert reader.route_webhook(event(A), '') is None
    else:
        assert reader.route_webhook(event(), f'Bearer {INGEST_TOKEN}')[1] == 503
    assert not Path(reader.settings.storage_dir).exists()


@pytest.mark.parametrize('patch', [dict(read_token_sha256=''), dict(webhook_token_sha256='bad'),
                                   dict(read_chat_ids=frozenset()), dict(read_chat_ids=frozenset({OTHER}))])
def test_read_configuration_is_explicit(client, reader, patch):
    reader.settings = replace(reader.settings, **patch)
    assert get(client).status_code == 503
    assert not Path(reader.settings.storage_dir).exists()


def test_credential_separation(client, reader):
    reader.settings = replace(reader.settings, read_token_sha256=reader.settings.webhook_token_sha256)
    assert get(client, token=INGEST_TOKEN).status_code == 503
    assert ingest(client).status_code == 503


def test_only_bearer_authorizes_read(client):
    ingest(client)
    for token in ('', INGEST_TOKEN, 'wrong'):
        assert get(client, token=token).status_code == 401
    with client.session_transaction() as session:
        session.update(role='owner', telegram_user_id=999999, web_user_id=1)
    assert client.get('/api/furniture-reader/messages').status_code == 401
    assert client.get('/api/furniture-reader/messages?token=' + READ_TOKEN).status_code == 401
    assert client.get('/api/furniture-reader/messages', headers={'X-Telegram-Init-Data': 'fake'}).status_code == 401
    assert get(client).status_code == 200


def test_reader_token_does_not_authorize_accounting(client):
    response = client.post('/api/assistant/message', json={'message': 'no'},
                           headers={'Authorization': f'Bearer {READ_TOKEN}'})
    assert response.status_code in (302, 401, 403)


@pytest.mark.parametrize('method', ['POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'])
def test_no_write_methods(client, method):
    response = client.open('/api/furniture-reader/messages', method=method,
                           headers={'Authorization': f'Bearer {READ_TOKEN}'})
    assert response.status_code in (401, 405)


def test_reads_never_initialize_or_download(client, reader, monkeypatch):
    monkeypatch.setattr(reader, 'media_tick', Mock(side_effect=AssertionError('no work on GET')))
    assert get(client).json['messages'] == []
    assert get(client, '/status').json['message_count'] == 0
    assert not Path(reader.settings.storage_dir).exists()
    assert get(client, '/messages?chat_id=' + OTHER).status_code == 404
    for suffix in ('?limit=101', '?limit=0', '?after_id=-1', '?limit=x'):
        assert get(client, '/messages' + suffix).status_code == 400


def test_scope_revocation_hides_existing_rows(client, reader):
    ingest(client)
    reader.settings = replace(reader.settings, allowed_chat_ids=frozenset({OTHER}), read_chat_ids=frozenset({OTHER}))
    assert get(client).json['messages'] == []
    assert get(client, '/messages?chat_id=' + F).status_code == 404


@pytest.mark.parametrize('subdir', ['static/archive', 'private/archive', '.'])
def test_reject_public_or_symlink_storage(client, reader, tmp_path, subdir):
    public = Path(reader.app_root) / subdir
    reader.settings = replace(reader.settings, storage_dir=str(public))
    assert ingest(client).status_code == 503
    assert not public.exists()
    symlink = tmp_path / 'link'
    symlink.symlink_to(tmp_path / 'other')
    reader.settings = replace(reader.settings, storage_dir=str(symlink / 'archive'))
    assert ingest(client).status_code == 503


def test_private_media_worker_and_protected_download(client, reader, monkeypatch):
    import furniture_reader.media as media_module
    content = b'\x89PNG\r\n\x1a\n' + b'fake-fixture'
    download = Mock(return_value=(content, 'image/png'))
    monkeypatch.setattr(media_module, 'download_media', download)
    reader.settings = replace(reader.settings, media_enabled=True,
                              media_hosts=frozenset({'media.example.test'}))
    assert ingest(client, event(media=True)).status_code == 200
    item = get(client).json['messages'][0]['attachment']
    assert get(client, '/attachments/' + item['id']).status_code == 404
    assert download.call_count == 0
    assert reader.media_tick() == 1
    response = get(client, '/attachments/' + item['id'])
    assert response.data == content
    assert response.headers['Content-Disposition'].startswith('attachment;')
    assert response.headers['Cache-Control'] == 'no-store'
    assert response.headers['X-Content-Type-Options'] == 'nosniff'
    assert response.headers['Content-Type'] == 'application/octet-stream'
    assert client.get('/api/furniture-reader/attachments/' + item['id']).status_code == 401
    reader.settings = replace(reader.settings, allowed_chat_ids=frozenset({OTHER}), read_chat_ids=frozenset({OTHER}))
    assert get(client, '/attachments/' + item['id']).status_code == 404
    assert download.call_count == 1


@pytest.mark.parametrize('tamper', ['symlink', 'hardlink', 'size', 'permissions'])
def test_attachment_refuses_unsafe_local_file(client, reader, monkeypatch, tmp_path, tamper):
    import furniture_reader.media as media_module
    monkeypatch.setattr(media_module, 'download_media', Mock(return_value=(b'fixture', 'image/png')))
    reader.settings = replace(reader.settings, media_enabled=True,
                              media_hosts=frozenset({'media.example.test'}))
    assert ingest(client, event(media=True)).text == 'Archived'
    assert reader.media_tick() == 1
    item = get(client).json['messages'][0]['attachment']
    path = next((Path(reader.settings.storage_dir) / 'media').iterdir())
    if tamper == 'symlink':
        target = tmp_path / 'other-file'
        target.write_bytes(b'fixture')
        path.unlink()
        path.symlink_to(target)
    elif tamper == 'hardlink':
        (tmp_path / 'other-link').hardlink_to(path)
    elif tamper == 'size':
        path.write_bytes(b'different-size')
    else:
        path.chmod(0o644)
    assert get(client, '/attachments/' + item['id']).status_code == 404


def test_disabled_or_revoked_media_never_downloads(client, reader, monkeypatch):
    import furniture_reader.media as media_module
    download = Mock(side_effect=AssertionError('must not download'))
    monkeypatch.setattr(media_module, 'download_media', download)
    reader.settings = replace(reader.settings, media_enabled=True,
                              media_hosts=frozenset({'media.example.test'}))
    ingest(client, event(media=True))
    original = reader.settings
    reader.settings = replace(original, enabled=False)
    assert reader.media_tick() == 0
    reader.settings = replace(original, media_enabled=False)
    assert reader.media_tick() == 0
    reader.settings = replace(original, allowed_chat_ids=frozenset({OTHER}))
    assert reader.media_tick() == 0
    download.assert_not_called()
