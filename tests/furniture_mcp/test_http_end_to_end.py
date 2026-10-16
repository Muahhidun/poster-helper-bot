"""OAuth MCP -> HTTP transport -> real Flask routes -> private SQLite/files.

Install Flask==3.0.0 in the separate test environment for this integration file.
Neither application binds a socket or imports the financial application.
"""
import base64
from dataclasses import replace
import hashlib

from flask import Flask
import httpx2 as httpx
import pytest

from furniture_reader.config import ReaderSettings
from furniture_reader.routes import create_blueprint
from furniture_reader.service import FurnitureReader
from test_server import (CHAT_A, CHAT_B, CLIENT_ID, FOREIGN_ID, INGEST_CHAT, INSTANCE,
                         PDF_BYTES, PDF_ID, PNG_BYTES, PNG_ID, READER_CHAT,
                         environment, running_app, saved_archive, signing_key,
                         snapshot, tokens, tool_data, tool_result)

READ = 'synthetic-reader-' * 4


def source(root):
    cfg = ReaderSettings(enabled=True, allowed_chat_ids=frozenset({CHAT_A, CHAT_B, READER_CHAT, INGEST_CHAT}),
                         read_chat_ids=frozenset({CHAT_A, CHAT_B, READER_CHAT}), instance_id=INSTANCE,
                         storage_dir=str(root), read_token_sha256=hashlib.sha256(READ.encode()).hexdigest(),
                         webhook_token_sha256=hashlib.sha256(b'synthetic-ingest-token-' * 4).hexdigest())
    app = Flask('isolated-furniture-source')
    service = FurnitureReader(cfg, lambda: 'accounting-synthetic@g.us', '/synthetic/application')
    app.extensions['furniture_reader'] = service
    app.register_blueprint(create_blueprint())
    requests = []

    async def handler(request):
        requests.append(request)
        with app.test_client() as client:
            response = client.open(request.url.raw_path.decode('ascii'), method=request.method,
                                   headers=dict(request.headers))
            return httpx.Response(response.status_code, headers=dict(response.headers), content=response.data)
    return service, httpx.MockTransport(handler), requests


def http_env(root):
    env = environment(root)
    env.pop('FURNITURE_STORAGE_DIR')
    env.update(FURNITURE_MCP_ARCHIVE_BACKEND='http',
               FURNITURE_MCP_ARCHIVE_URL='https://source.example.invalid/api/furniture-reader/mcp',
               FURNITURE_MCP_ARCHIVE_TOKEN=READ)
    return env


def test_real_http_boundary_preserves_oauth_scope_files_and_read_only_storage(saved_archive, signing_key, tokens):
    root, rows, _ = saved_archive
    before = snapshot(root)
    service, transport, requests = source(root)
    with running_app(http_env(root), signing_key, tokens, archive_transport=transport) as wire:
        assert wire.tool('list_chats', token=None).status_code == 401
        assert wire.tool('list_chats', token=tokens(sub='auth0|wrong-owner')).status_code == 401
        assert requests == []
        assert len(tool_data(wire.tool('list_chats'))['chats']) == 2
        messages = tool_data(wire.tool('search_messages', {'query': 'invoice'}))['messages']
        assert {row['chat_id'] for row in messages} == {CHAT_A, CHAT_B}
        assert tool_data(wire.tool('read_message', {'message_id': rows['visible-image']}))['id'] == rows['visible-image']
        for attachment, data in ((PNG_ID, PNG_BYTES), (PDF_ID, PDF_BYTES)):
            result = tool_result(wire.tool('read_attachment', {'attachment_id': attachment}))
            content = result['content'][1]
            encoded = content['data'] if content['type'] == 'image' else content['resource']['blob']
            assert base64.b64decode(encoded) == data
        assert wire.tool('read_attachment', {'attachment_id': FOREIGN_ID}).json()['result']['isError'] is True
        # Server-side revocation applies immediately on the next request.
        service.settings = replace(service.settings, read_chat_ids=frozenset({READER_CHAT}))
        denied = wire.tool('list_chats').json()['result']
        assert denied['isError'] and denied['structuredContent'] == {'error': 'Archive unavailable'}
    assert snapshot(root) == before
    assert all(request.method == 'GET' and 'query=' not in str(request.url) for request in requests)


@pytest.mark.parametrize('failure', ['bad_read_token', 'disabled_source', 'missing_directory'])
def test_source_failures_are_mcp_errors_without_empty_success_or_financial_fallback(saved_archive, signing_key, tokens, failure):
    root, _rows, _ = saved_archive
    service, transport, _requests = source(root)
    env = http_env(root)
    if failure == 'bad_read_token':
        env['FURNITURE_MCP_ARCHIVE_TOKEN'] = 'synthetic-wrong-token-' * 4
    elif failure == 'disabled_source':
        service.settings = replace(service.settings, enabled=False)
    else:
        service.settings = replace(service.settings, storage_dir=str(root / 'missing'))
        # An absent archive returns empty (a documented first-run state), but
        # the existing path replaced with a file is an actual storage failure.
        (root / 'missing').write_bytes(b'synthetic-not-a-directory')
    with running_app(env, signing_key, tokens, archive_transport=transport) as wire:
        result = wire.tool('list_chats').json()['result']
        assert result['isError'] and result['structuredContent'] == {'error': 'Archive unavailable'}
        assert READ not in str(result)
