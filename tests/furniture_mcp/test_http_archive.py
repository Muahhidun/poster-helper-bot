"""HTTPS boundary tests: synthetic data, MockTransport, no external sockets."""
import asyncio
import base64
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import httpx2 as httpx
import pytest

from furniture_mcp.archive import ArchiveReader
from furniture_mcp.config import ConfigurationError, MCPSettings
from furniture_mcp.http_archive import HTTPArchiveReader
from furniture_reader.config import ReaderSettings
from furniture_reader.http_contract import QUERY_HEADER, SCOPE_HEADER, decode_query, decode_scope, envelope
from furniture_reader.store import ArchiveStore

CHAT, OTHER = 'synthetic-owner@g.us', 'synthetic-hidden@g.us'
INSTANCE = 'synthetic-instance'
TOKEN = 'synthetic-read-token-' + 'r' * 40
BASE = 'https://source.example.invalid/api/furniture-reader/mcp'
ATTACHMENT = 'a' * 32
PNG = b'\x89PNG\r\n\x1a\nsynthetic-file'


def settings():
    return MCPSettings(enabled=True, issuer='https://tenant.example.invalid/',
                       resource='https://mcp.example.invalid/mcp', owner_sub='auth0|synthetic-owner',
                       client_id='synthetic-client', instance_id=INSTANCE,
                       allowed_chat_ids=frozenset({CHAT, OTHER}), read_chat_ids=frozenset({CHAT, OTHER}),
                       owner_chat_ids=frozenset({CHAT}), archive_backend='http',
                       archive_url=BASE, archive_token=TOKEN)


@pytest.fixture
def archive(tmp_path):
    root = (tmp_path / 'persisted').resolve()
    store = ArchiveStore(root)
    for number, (chat, instance) in enumerate(((CHAT, INSTANCE), (OTHER, INSTANCE), (CHAT, 'foreign-instance'))):
        store.save_message({'instance_id': instance, 'chat_id': chat, 'message_id': f'fixture-{number}',
                            'direction': 'incoming', 'sent_at': 1700000000,
                            'received_at': '2026-10-16T00:00:00+00:00',
                            'message_type': 'textMessage', 'text': 'synthetic invoice'},
                           {'id': ATTACHMENT, 'filename': 'invoice.png', 'declared_mime_type': 'image/png',
                            'download_url': 'https://media.example.invalid/private?signature=never-expose',
                            'state': 'pending'} if number == 0 else None)
    job = store.claim_attachment(frozenset({CHAT}))
    name = ATTACHMENT + '-' + job['claim_token'] + '.bin'
    path = store.media_root / name
    path.write_bytes(PNG)
    path.chmod(0o600)
    assert store.finish_attachment(ATTACHMENT, local_name=name, mime_type='image/png', size=len(PNG),
                                   sha256=hashlib.sha256(PNG).hexdigest(), claim_token=job['claim_token'])
    return root


def local_transport(root, *, mutate=None):
    requests = []

    async def handler(request):
        requests.append(request)
        assert request.method == 'GET'
        assert str(request.url).startswith(BASE + '/')
        assert request.headers['Authorization'] == 'Bearer ' + TOKEN
        assert not ({'cookie', 'proxy-authorization'} & set(request.headers))
        instance, chats = decode_scope(request.headers[SCOPE_HEADER])
        assert instance == INSTANCE and chats == frozenset({CHAT})
        local = ArchiveReader(root, instance, chats)
        suffix = request.url.path.removeprefix('/api/furniture-reader/mcp')
        p = request.url.params
        if suffix == '/chats':
            result = local.list_chats(p['after_chat_id'], int(p['limit']))
        elif suffix == '/search':
            assert 'query' not in p
            result = local.search_messages(decode_query(request.headers[QUERY_HEADER]), p.get('chat_id'), int(p['after_id']), int(p['limit']))
        elif suffix.startswith('/messages/'):
            result = local.read_message(int(suffix.rsplit('/', 1)[1]))
        else:
            result = local.read_attachment(suffix.rsplit('/', 1)[1])
            if result is not None and 'data' in result:
                result = dict(result, data_base64=base64.b64encode(result['data']).decode('ascii'))
                result.pop('data')
        if result is None:
            return httpx.Response(404)
        document = envelope(instance, chats, result)
        if mutate:
            mutate(document)
        return httpx.Response(200, json=document)
    return httpx.MockTransport(handler), requests


def test_all_four_reads_use_only_the_remote_narrowed_archive(archive, tmp_path):
    before = {p.relative_to(archive): p.read_bytes() for p in archive.rglob('*') if p.is_file()}
    transport, requests = local_transport(archive)

    async def scenario():
        reader = HTTPArchiveReader(settings(), transport=transport)
        try:
            assert [c['chat_id'] for c in (await reader.list_chats())['chats']] == [CHAT]
            result = await reader.search_messages('SYNTHETIC')
            assert len(result['messages']) == 1
            assert (await reader.read_message(1))['instance_id'] == INSTANCE
            assert await reader.read_message(2) is None
            item = await reader.read_attachment(ATTACHMENT)
            assert item['data'] == PNG and 'data_base64' not in item
            assert await reader.read_attachment('b' * 32) is None
            assert await reader.search_messages('synthetic', OTHER) == {'messages': [], 'next_after_id': None}
        finally:
            await reader.aclose()
    asyncio.run(scenario())
    assert len(requests) == 6
    assert {p.relative_to(archive): p.read_bytes() for p in archive.rglob('*') if p.is_file()} == before


def test_committed_archive_and_ready_file_survive_a_new_python_process(archive, tmp_path):
    script = '''
import hashlib,json,socket,sys
def forbidden(*args,**kwargs): raise AssertionError('No networking')
socket.create_connection=forbidden
socket.socket.connect=forbidden
sys.path.insert(0,sys.argv[1])
sys.modules['database']=None
sys.modules['user_database']=None
from pathlib import Path
from furniture_mcp.archive import ArchiveReader
reader=ArchiveReader(Path(sys.argv[2]),'synthetic-instance',frozenset({'synthetic-owner@g.us'}))
item=reader.read_attachment('a'*32)
print(json.dumps({'chats':len(reader.list_chats()['chats']), 'message':reader.read_message(1)['message_id'],
                  'digest':hashlib.sha256(item['data']).hexdigest()}))
'''
    project = str(Path(__file__).resolve().parents[2])
    result = subprocess.run([sys.executable, '-I', '-B', '-c', script, project, str(archive)],
                            cwd=tmp_path, env={}, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'chats': 1, 'message': 'fixture-0',
                                        'digest': hashlib.sha256(PNG).hexdigest()}


@pytest.mark.parametrize('mutate', [
    lambda d: d.update(instance_id='foreign-instance'),
    lambda d: d.update(chat_ids=[CHAT, OTHER]),
    lambda d: d.update(version=True),
    lambda d: d.update(download_url='synthetic-secret-marker'),
    lambda d: d['result']['messages'][0].update(chat_id=OTHER),
    lambda d: d['result']['messages'][0].update(instance_id='foreign-instance'),
    lambda d: d['result']['messages'][0].update(id=True),
    lambda d: d['result']['messages'][0].update(download_url='synthetic-secret-marker'),
    lambda d: d['result'].update(next_after_id=9999),
])
def test_remote_scope_and_schema_violations_fail_closed(archive, mutate):
    transport, _ = local_transport(archive, mutate=mutate)

    async def scenario():
        reader = HTTPArchiveReader(settings(), transport=transport)
        try:
            with pytest.raises(RuntimeError, match='^Archive unavailable$') as error:
                await reader.search_messages('synthetic')
            assert 'synthetic-secret-marker' not in str(error.value)
            assert error.value.__suppress_context__
        finally:
            await reader.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize('mutate', [
    lambda d: d['result'].update(id='b' * 32),
    lambda d: d['result'].update(chat_id=OTHER),
    lambda d: d['result'].update(size=True),
    lambda d: d['result'].update(sha256='0' * 64),
    lambda d: d['result'].update(data_base64='invalid@base64'),
    lambda d: d['result'].update(mime_type='text/html'),
    lambda d: d['result'].update(filename='../escape.png'),
    lambda d: d['result'].update(state='pending'),
])
def test_attachment_response_requires_scope_safe_metadata_and_exact_digest(archive, mutate):
    transport, _ = local_transport(archive, mutate=mutate)

    async def scenario():
        reader = HTTPArchiveReader(settings(), transport=transport)
        try:
            with pytest.raises(RuntimeError, match='^Archive unavailable$'):
                await reader.read_attachment(ATTACHMENT)
        finally:
            await reader.aclose()
    asyncio.run(scenario())


class Stream(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks, self.closed = chunks, False

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk

    async def aclose(self):
        self.closed = True


@pytest.mark.parametrize('status,headers,body', [
    (302, {'location': 'https://evil.example.invalid/leak'}, b''),
    (401, {}, b'synthetic-secret-marker'), (403, {}, b'synthetic-secret-marker'),
    (500, {}, b'synthetic-secret-marker'),
    (200, {'content-type': 'text/html'}, b'<html>synthetic-secret-marker</html>'),
    (200, {'content-type': 'application/json', 'content-encoding': 'gzip'}, b'bad'),
    (200, {'content-type': 'application/json', 'content-length': '99999999'}, b'{}'),
    (200, {'content-type': 'application/json', 'content-length': '1,1'}, b'{}'),
    (200, {'content-type': 'application/json'}, b'x' * 40000),
    (200, {'content-type': 'application/json'}, b'{"version":1,"version":1}'),
    (200, {'content-type': 'application/json'}, b'NaN'),
])
def test_http_failures_are_bounded_sanitized_closed_and_never_redirected(status, headers, body):
    stream, requests = Stream([body]), []

    async def handler(request):
        requests.append(request)
        return httpx.Response(status, headers=headers, stream=stream)

    async def scenario():
        reader = HTTPArchiveReader(settings(), transport=httpx.MockTransport(handler))
        try:
            with pytest.raises(RuntimeError, match='^Archive unavailable$') as error:
                await reader.list_chats()
            assert TOKEN not in str(error.value) and 'synthetic-secret-marker' not in str(error.value)
        finally:
            await reader.aclose()
    asyncio.run(scenario())
    assert len(requests) == 1 and stream.closed


def test_total_deadline_and_network_failures_never_become_empty_success(monkeypatch):
    from furniture_mcp import http_archive
    monkeypatch.setattr(http_archive, 'REQUEST_SECONDS', 0.03)

    async def handler(request):
        await asyncio.Event().wait()

    async def scenario():
        reader = HTTPArchiveReader(settings(), transport=httpx.MockTransport(handler))
        try:
            with pytest.raises(RuntimeError, match='^Archive unavailable$'):
                await asyncio.wait_for(reader.list_chats(), timeout=1)
        finally:
            await reader.aclose()
        with pytest.raises(RuntimeError, match='^Archive unavailable$'):
            await reader.list_chats()
    asyncio.run(scenario())


@pytest.mark.parametrize('value', ['http://source.example.invalid/api/furniture-reader/mcp',
                                 BASE + '/', BASE + '?token=synthetic-secret-marker',
                                 'https://127.0.0.1/api/furniture-reader/mcp',
                                 'https://owner:secret@source.example.invalid/api/furniture-reader/mcp'])
def test_http_source_is_one_exact_credential_free_https_url(value):
    with pytest.raises(ConfigurationError):
        replace(settings(), archive_url=value).validate()


def test_http_configuration_never_inspects_a_local_or_financial_database(monkeypatch):
    def forbidden(*args):
        raise AssertionError('No local filesystem access for the HTTP backend')
    monkeypatch.setattr(ReaderSettings, 'valid_storage', forbidden)
    replace(settings(), storage_dir='/synthetic/not-opened/accounting.sqlite3').validate()
    assert TOKEN not in repr(settings())
    with pytest.raises(ConfigurationError):
        replace(settings(), archive_backend='typo').validate()
    with pytest.raises(ConfigurationError):
        replace(settings(), archive_token='short').validate()
    chats = frozenset('x' * 240 + str(n) for n in range(100))
    with pytest.raises(ConfigurationError):
        replace(settings(), allowed_chat_ids=chats, read_chat_ids=chats, owner_chat_ids=chats).validate()
