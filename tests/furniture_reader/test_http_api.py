"""Real Flask routing + private synthetic files; no financial application import."""
import base64
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from flask import Flask
import pytest

from furniture_reader.config import ReaderSettings
from furniture_reader.http_contract import QUERY_HEADER, SCOPE_HEADER, encode_query, encode_scope
from furniture_reader.routes import create_blueprint
from furniture_reader.service import FurnitureReader
from furniture_reader.store import ArchiveStore

CHAT, HIDDEN = 'furniture-synthetic@g.us', 'hidden-synthetic@g.us'
INSTANCE = 'synthetic-instance'
READ = 'synthetic-reader-' + 'r' * 40
INGEST = 'synthetic-ingest-' + 'i' * 40
ATTACHMENT = 'a' * 32
PDF = b'%PDF-1.7\nsynthetic-invoice'


def application(root):
    settings = ReaderSettings(enabled=True, allowed_chat_ids=frozenset({CHAT, HIDDEN}),
                              read_chat_ids=frozenset({CHAT, HIDDEN}), instance_id=INSTANCE,
                              storage_dir=str(root.resolve()),
                              read_token_sha256=hashlib.sha256(READ.encode()).hexdigest(),
                              webhook_token_sha256=hashlib.sha256(INGEST.encode()).hexdigest())
    service = FurnitureReader(settings, lambda: 'accounting-synthetic@g.us', str(root.parent / 'application'))
    app = Flask(__name__)
    app.extensions['furniture_reader'] = service
    app.register_blueprint(create_blueprint())
    return app, service


def save(root, chat=CHAT, instance=INSTANCE, name='first', attachment=False):
    store = ArchiveStore(root)
    store.save_message({'instance_id': instance, 'chat_id': chat, 'message_id': name,
                        'direction': 'incoming', 'sent_at': 1700000000,
                        'received_at': '2026-10-16T00:00:00+00:00',
                        'message_type': 'textMessage', 'text': 'synthetic invoice'},
                       {'id': ATTACHMENT, 'filename': 'invoice.pdf', 'declared_mime_type': 'application/pdf',
                        'state': 'pending', 'download_url': 'https://media.example.invalid/private?signature=hidden'}
                       if attachment else None)
    return store


@pytest.fixture
def source(tmp_path):
    root = (tmp_path / 'persistent-archive').resolve()
    app, service = application(root)
    return root, app.test_client(), service


def get(client, path, *, token=READ, scope=None, query=None):
    headers = {'Authorization': 'Bearer ' + token,
               SCOPE_HEADER: encode_scope(INSTANCE, frozenset({CHAT})) if scope is None else scope}
    if query is not None:
        headers[QUERY_HEADER] = encode_query(query)
    return client.get('/api/furniture-reader/mcp' + path, headers=headers)


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_new_process_objects_reopen_committed_messages_and_media_without_accounting(source):
    root, client, _ = source
    store = save(root, attachment=True)
    job = store.claim_attachment(frozenset({CHAT}))
    name = ATTACHMENT + '-' + job['claim_token'] + '.bin'
    path = store.media_root / name
    path.write_bytes(PDF)
    path.chmod(0o600)
    assert store.finish_attachment(ATTACHMENT, local_name=name, mime_type='application/pdf', size=len(PDF),
                                   sha256=hashlib.sha256(PDF).hexdigest(), claim_token=job['claim_token'])
    save(root, chat=HIDDEN, name='hidden')
    save(root, instance='other-instance', name='foreign')
    before = snapshot(root)
    # New service + Flask objects, same persistent directory; no memory cache.
    restarted, _ = application(root)
    client = restarted.test_client()
    chats = get(client, '/chats').json
    assert chats['chat_ids'] == [CHAT] and chats['result']['chats'][0]['message_count'] == 1
    assert len(get(client, '/search', query='synthetic').json['result']['messages']) == 1
    assert get(client, '/messages/1').json['result']['message_id'] == 'first'
    assert get(client, '/messages/2').status_code == get(client, '/messages/3').status_code == 404
    attachment = get(client, '/attachments/' + ATTACHMENT).json['result']
    assert base64.b64decode(attachment['data_base64']) == PDF
    assert 'download_url' not in json.dumps(attachment) and 'local_name' not in attachment
    assert snapshot(root) == before
    # Persisted deduplication also survives recreation of ArchiveStore.
    again = save(root, attachment=False)
    assert again.status(frozenset({CHAT}))['message_count'] == 2  # includes the distinct foreign instance


def test_empty_archive_reads_do_not_create_storage_or_claim_media(source, monkeypatch):
    root, client, service = source
    monkeypatch.setattr(service, 'media_tick', lambda: pytest.fail('GET must not run workers'))
    assert get(client, '/chats').json['result']['chats'] == []
    assert get(client, '/search', query='synthetic').json['result']['messages'] == []
    assert get(client, '/messages/1').status_code == 404
    assert not root.exists()
    save(root, attachment=True)
    before = snapshot(root)
    item = get(client, '/attachments/' + ATTACHMENT).json['result']
    assert item['state'] == 'pending' and item['attempts'] == 0 and 'data_base64' not in item
    assert snapshot(root) == before


@pytest.mark.parametrize('token', ['', INGEST, 'incorrect'])
def test_http_api_never_uses_owner_sessions_or_webhook_credentials(source, token):
    root, client, _ = source
    assert get(client, '/chats', token=token).status_code == 401
    assert not root.exists()


@pytest.mark.parametrize('scope,status', [
    ('', 400), ('{}', 400),
    ('{"instance_id":"synthetic-instance","instance_id":"other","chat_ids":[]}', 400),
    ('{"instance_id":"synthetic-instance","chat_ids":[]}', 400),
    ('{"instance_id":"synthetic-instance","chat_ids":["*"]}', 400),
    (encode_scope('wrong-instance', frozenset({CHAT})), 404),
    (encode_scope(INSTANCE, frozenset({'not-authorized@g.us'})), 404),
])
def test_scope_header_may_narrow_but_cannot_grant_access(source, scope, status):
    root, client, _ = source
    assert get(client, '/chats', scope=scope).status_code == status
    assert not root.exists()


@pytest.mark.parametrize('path', ['/chats?limit=51', '/chats?limit=1&limit=2',
                                 '/chats?token=hidden', '/search?query=x', '/search?query=synthetic&after_id=-1',
                                 '/messages/0', '/messages/1?query=synthetic', '/attachments/invalid'])
def test_invalid_queries_never_open_or_create_the_archive(source, path):
    root, client, _ = source
    assert get(client, path).status_code == 400
    assert not root.exists()


@pytest.mark.parametrize('method', ['POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'])
def test_http_archive_has_no_write_methods(source, method):
    root, client, _ = source
    response = client.open('/api/furniture-reader/mcp/chats', method=method,
                           headers={'Authorization': 'Bearer ' + READ,
                                    SCOPE_HEADER: encode_scope(INSTANCE, frozenset({CHAT}))})
    assert response.status_code == 405 and not root.exists()


def test_exclusive_sqlite_lock_returns_bounded_sanitized_failure_then_recovers(source):
    root, client, _ = source
    store = save(root)
    with sqlite3.connect(store.db_path) as lock:
        lock.execute('BEGIN EXCLUSIVE')
        started = time.monotonic()
        response = get(client, '/search', query='synthetic')
        elapsed = time.monotonic() - started
        assert response.status_code == 503 and response.json == {'error': 'Archive unavailable'}
        assert str(root) not in response.text and elapsed < 2
        lock.rollback()
    assert get(client, '/messages/1').status_code == 200


@pytest.mark.parametrize('fault', ['unrelated_database', 'corrupt_database', 'unsafe_permissions'])
def test_bad_persistent_storage_is_not_repaired_or_served_by_get(source, fault):
    root, client, _ = source
    store = save(root)
    if fault == 'unrelated_database':
        with sqlite3.connect(store.db_path) as connection:
            connection.execute('PRAGMA application_id = 0')
    elif fault == 'corrupt_database':
        store.db_path.write_bytes(b'synthetic-corrupt-database')
    else:
        store.db_path.chmod(0o644)
    before = snapshot(root)
    assert get(client, '/chats').status_code == 503
    assert snapshot(root) == before


def test_revocation_and_privacy_headers_apply_to_all_new_routes(source):
    root, client, service = source
    save(root)
    for path in ('/chats', '/search', '/messages/1', '/attachments/' + ATTACHMENT):
        response = get(client, path, query='synthetic' if path == '/search' else None)
        assert response.headers['Cache-Control'] == 'no-store'
        assert response.headers['X-Content-Type-Options'] == 'nosniff'
        assert response.headers['Referrer-Policy'] == 'no-referrer'
    service.settings = replace(service.settings, read_chat_ids=frozenset({HIDDEN}))
    assert get(client, '/messages/1').status_code == 404
    service.settings = replace(service.settings, enabled=False)
    assert get(client, '/chats').status_code == 404
