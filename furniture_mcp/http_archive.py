"""Async HTTPS reader of an explicitly configured furniture archive.

No GREEN-API calls, cookies, proxies, redirects, writes or local storage. HTTP
responses are untrusted: verify their scope, schema, ordering and file digest
before any content can enter an MCP result. Inject only transport in tests.
"""
import asyncio
import base64
import hashlib
import hmac
import re
from datetime import datetime

import httpx2 as httpx

from furniture_reader.http_contract import QUERY_HEADER, SCOPE_HEADER, VERSION, encode_query, encode_scope, load_json
from .archive import ATTACHMENT_MAX_BYTES, MESSAGE_MAX_BYTES, SEARCH_MAX_BYTES

REQUEST_SECONDS = 5.0
_MAX_ID = (1 << 63) - 1
_ATTACHMENT_FIELDS = {'id', 'filename', 'declared_mime_type', 'state', 'mime_type',
                      'size', 'sha256', 'error_code', 'attempts'}
_STATES = {'pending', 'processing', 'failed', 'ready', 'disabled', 'unsupported'}
_MIMES = {'image/jpeg', 'image/png', 'image/webp', 'application/pdf'}
_MESSAGE_FIELDS = {'id', 'instance_id', 'chat_id', 'message_id', 'direction', 'sent_at',
                   'received_at', 'message_type', 'text', 'text_length', 'text_truncated',
                   'attachment'}


def _check(condition):
    if not condition:
        raise ValueError('Invalid archive request')


def _integer(value, minimum=0, maximum=_MAX_ID):
    _check(type(value) is int and minimum <= value <= maximum)


def _text(value, maximum, minimum=0, nullable=False):
    if value is None and nullable:
        return
    _check(isinstance(value, str) and minimum <= len(value) <= maximum and '\x00' not in value)


def _date(value, nullable=False):
    if value is None and nullable:
        return
    _text(value, 64, 1)
    _check(datetime.fromisoformat(value.replace('Z', '+00:00')).tzinfo is not None)


def _metadata(value, *, standalone=False):
    _check(isinstance(value, dict) and set(value) == _ATTACHMENT_FIELDS)
    _check(isinstance(value['id'], str) and re.fullmatch(r'[a-f0-9]{32}', value['id']))
    _text(value['filename'], 255, 1)
    _check(value['filename'] not in ('.', '..') and not any(
        ord(c) < 32 or ord(c) == 127 or c in '/\\' for c in value['filename']))
    for key in ('declared_mime_type', 'mime_type'):
        _text(value[key], 128, nullable=True)
    _check(value['state'] in (_STATES | {'too_large', 'unavailable'} if standalone else _STATES))
    if value['size'] is not None:
        _integer(value['size'])
    if value['sha256'] is not None:
        _check(isinstance(value['sha256'], str) and re.fullmatch(r'[a-f0-9]{64}', value['sha256']))
    if value['error_code'] is not None:
        _check(isinstance(value['error_code'], str)
               and re.fullmatch(r'[a-z][a-z0-9_]{0,63}', value['error_code']))
    _integer(value['attempts'])


class HTTPArchiveReader:
    def __init__(self, settings, *, transport=None):
        settings.validate()
        _check(settings.archive_backend == 'http')
        self._base = settings.archive_url
        self._token = settings.archive_token
        self._scope = encode_scope(settings.instance_id, settings.chat_ids)
        self._instance, self._chats = settings.instance_id, settings.chat_ids
        self._transport, self._client, self._closed = transport, None, False

    async def aclose(self):
        self._closed = True
        if self._client is not None:
            await self._client.aclose()

    async def _get(self, path, params, maximum, *, missing=False, query=None):
        try:
            _check(not self._closed)
            if self._client is None:
                self._client = httpx.AsyncClient(
                    transport=self._transport, trust_env=False, follow_redirects=False,
                    timeout=REQUEST_SECONDS,
                    limits=httpx.Limits(max_connections=2, max_keepalive_connections=1))
            headers = {'Authorization': 'Bearer ' + self._token, SCOPE_HEADER: self._scope,
                       'Accept': 'application/json', 'Accept-Encoding': 'identity'}
            if query is not None:
                headers[QUERY_HEADER] = encode_query(query)
            request = httpx.Request(
                'GET', self._base + path, params=params,
                headers=headers,
                extensions={'timeout': dict.fromkeys(('connect', 'read', 'write', 'pool'), REQUEST_SECONDS)})
            async with asyncio.timeout(REQUEST_SECONDS):
                response = await self._client.send(request, stream=True, auth=None, follow_redirects=False)
                try:
                    if missing and response.status_code == 404:
                        return None
                    _check(response.status_code == 200)
                    _check(response.headers.get('content-type', '').split(';')[0].lower() == 'application/json')
                    _check(response.headers.get('content-encoding', 'identity').lower() == 'identity')
                    length = response.headers.get('content-length')
                    if length is not None:
                        _check(length.isdecimal() and int(length) <= maximum)
                    body = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        _check(len(body) + len(chunk) <= maximum)
                        body.extend(chunk)
                    if length is not None:
                        _check(len(body) == int(length))
                finally:
                    await response.aclose()
            value = load_json(bytes(body).decode('utf-8'))
            _check(isinstance(value, dict) and set(value) == {'version', 'instance_id', 'chat_ids', 'result'})
            _check(type(value['version']) is int and value['version'] == VERSION)
            _check(value['instance_id'] == self._instance and value['chat_ids'] == sorted(self._chats))
            return value['result']
        except Exception:
            # No upstream response, URL, header, credential or exception chain.
            raise RuntimeError('Archive unavailable') from None

    def _message(self, value):
        _check(isinstance(value, dict) and set(value) == _MESSAGE_FIELDS)
        _integer(value['id'], 1)
        _check(value['instance_id'] == self._instance and value['chat_id'] in self._chats)
        _text(value['message_id'], 512, 1)
        _check(value['direction'] in ('incoming', 'outgoing'))
        _integer(value['sent_at'], 0, 253402300799)
        _date(value['received_at'])
        _text(value['message_type'], 64, 1)
        _text(value['text'], 100000)
        _integer(value['text_length'], len(value['text']), 100000)
        _check(type(value['text_truncated']) is bool
               and value['text_truncated'] == (value['text_length'] > len(value['text'])))
        if value['attachment'] is not None:
            _metadata(value['attachment'])

    async def list_chats(self, after_chat_id='', limit=50):
        _text(after_chat_id, 256)
        _integer(limit, 1, 50)
        value = await self._get('/chats', {'after_chat_id': after_chat_id, 'limit': limit}, SEARCH_MAX_BYTES + 8192)
        try:
            _check(isinstance(value, dict) and set(value) == {'chats', 'next_after_chat_id'})
            rows = value['chats']
            _check(isinstance(rows, list) and len(rows) <= limit)
            previous = after_chat_id
            for item in rows:
                _check(isinstance(item, dict) and set(item) == {'chat_id', 'message_count', 'last_received_at'})
                _check(item['chat_id'] in self._chats and item['chat_id'] > previous)
                _integer(item['message_count'], 1)
                _date(item['last_received_at'])
                previous = item['chat_id']
            cursor = value['next_after_chat_id']
            _check(cursor is None or (len(rows) == limit and cursor == previous))
            return value
        except Exception:
            raise RuntimeError('Archive unavailable') from None

    async def search_messages(self, query, chat_id=None, after_id=0, limit=20):
        _text(query, 200, 2)
        _check(len(query.strip()) >= 2)
        _integer(after_id)
        _integer(limit, 1, 50)
        if chat_id is not None:
            _text(chat_id, 256, 1)
            if chat_id not in self._chats:
                return {'messages': [], 'next_after_id': None}
        params = {'after_id': after_id, 'limit': limit}
        if chat_id is not None:
            params['chat_id'] = chat_id
        value = await self._get('/search', params, SEARCH_MAX_BYTES + 8192, query=query)
        try:
            _check(isinstance(value, dict) and set(value) == {'messages', 'next_after_id'})
            rows = value['messages']
            _check(isinstance(rows, list) and len(rows) <= limit)
            previous = after_id
            for row in rows:
                self._message(row)
                _check(row['id'] > previous and (chat_id is None or row['chat_id'] == chat_id))
                previous = row['id']
            cursor = value['next_after_id']
            _check(cursor is None or (rows and type(cursor) is int and cursor == previous))
            return value
        except Exception:
            raise RuntimeError('Archive unavailable') from None

    async def read_message(self, message_id):
        _integer(message_id, 1)
        value = await self._get('/messages/' + str(message_id), {}, MESSAGE_MAX_BYTES + 8192, missing=True)
        if value is None:
            return None
        try:
            self._message(value)
            _check(value['id'] == message_id)
            return value
        except Exception:
            raise RuntimeError('Archive unavailable') from None

    async def read_attachment(self, attachment_id):
        _check(isinstance(attachment_id, str) and re.fullmatch(r'[a-f0-9]{32}', attachment_id))
        maximum = 4 * ((ATTACHMENT_MAX_BYTES + 2) // 3) + 16384
        value = await self._get('/attachments/' + attachment_id, {}, maximum, missing=True)
        if value is None:
            return None
        try:
            _check(isinstance(value, dict))
            extra = {'chat_id', 'instance_id', 'message_row_id'}
            data_keys = {'data_base64'} if 'data_base64' in value else set()
            _check(set(value) == _ATTACHMENT_FIELDS | extra | data_keys)
            _metadata({key: value[key] for key in _ATTACHMENT_FIELDS}, standalone=True)
            _check(value['id'] == attachment_id and value['instance_id'] == self._instance
                   and value['chat_id'] in self._chats)
            _integer(value['message_row_id'], 1)
            _check(bool(data_keys) == (value['state'] == 'ready'))
            if data_keys:
                _check(value['mime_type'] in _MIMES)
                _integer(value['size'], 0, ATTACHMENT_MAX_BYTES)
                encoded = value.pop('data_base64')
                _text(encoded, 4 * ((ATTACHMENT_MAX_BYTES + 2) // 3))
                data = base64.b64decode(encoded, validate=True)
                _check(base64.b64encode(data).decode('ascii') == encoded and len(data) == value['size'])
                _check(isinstance(value['sha256'], str)
                       and hmac.compare_digest(hashlib.sha256(data).hexdigest(), value['sha256']))
                value['data'] = data
            return value
        except Exception:
            raise RuntimeError('Archive unavailable') from None
