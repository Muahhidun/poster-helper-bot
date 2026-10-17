"""In-process MCP HTTP contracts with local RSA/JWKS and a private fake archive.

No live sockets, Auth0 tenant, application runtime, financial database or service
credentials are used. The injectable seam is HTTP transport, not token validation.
"""

import asyncio
import base64
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

from cryptography.hazmat.primitives.asymmetric import rsa
import httpx2 as httpx
import jwt
from mcp_types import CLIENT_CAPABILITIES_META_KEY, PROTOCOL_VERSION_META_KEY
import pytest
from starlette.testclient import TestClient

from furniture_mcp.config import ATTACHMENT_SCOPE, MESSAGE_SCOPE
from furniture_mcp.archive import ArchiveReader
from furniture_mcp.server import create_app
from furniture_reader.http_contract import QUERY_HEADER, SCOPE_HEADER, decode_query, decode_scope, envelope
from furniture_reader.store import ArchiveStore


ISSUER = "https://tenant.example.invalid/"
RESOURCE = "https://archive.example.invalid/mcp"
ORIGIN = "https://archive.example.invalid"
OWNER = "auth0|owner"
CLIENT_ID = "fakeclient"
INSTANCE = "fixture-instance"
CHAT_A, CHAT_B = "owner-a@g.us", "owner-b@g.us"
READER_CHAT, INGEST_CHAT = "reader-only@g.us", "ingest-only@g.us"
LEGACY = "2025-11-25"
MODERN = "2026-07-28"
PNG_ID, PDF_ID, PENDING_ID, FOREIGN_ID = "a" * 32, "b" * 32, "c" * 32, "d" * 32
PNG_BYTES = b"\x89PNG\r\n\x1a\nlocal-test-fixture"
PDF_BYTES = b"%PDF-1.7\nlocal-test-fixture"
SIGNED_URL = "https://media.example.invalid/file?signature=never-return-this"
DEFAULT_TOKEN = object()


def environment(root):
    return {
        "FURNITURE_MCP_ENABLED": "true",
        "FURNITURE_MCP_AUTH0_ISSUER": ISSUER,
        "FURNITURE_MCP_RESOURCE_URL": RESOURCE,
        "FURNITURE_MCP_OWNER_SUB": OWNER,
        "FURNITURE_MCP_AUTH0_CLIENT_ID": CLIENT_ID,
        "FURNITURE_STORAGE_DIR": str(root.resolve()),
        "FURNITURE_INSTANCE_ID": INSTANCE,
        "FURNITURE_CHAT_IDS": ",".join((CHAT_A, CHAT_B, READER_CHAT, INGEST_CHAT)),
        "FURNITURE_READ_CHAT_IDS": ",".join((CHAT_A, CHAT_B, READER_CHAT)),
        "FURNITURE_MCP_CHAT_IDS": ",".join((CHAT_A, CHAT_B)),
    }


def snapshot(root):
    return {str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob("*") if path.is_file()}


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def tokens(signing_key):
    def encode(*, scopes=(MESSAGE_SCOPE, ATTACHMENT_SCOPE), omit=(), **overrides):
        now = int(time.time())
        claims = {"iss": ISSUER, "aud": RESOURCE, "sub": OWNER, "azp": CLIENT_ID,
                  "iat": now - 1, "exp": now + 600, "scope": " ".join(scopes)}
        claims.update(overrides)
        for name in omit:
            claims.pop(name, None)
        return jwt.encode(claims, signing_key, algorithm="RS256", headers={"kid": "fixture-key"})
    return encode


@pytest.fixture
def saved_archive(tmp_path):
    root = (tmp_path / "private-archive").resolve()
    store = ArchiveStore(root)
    rows = {}

    def insert(name, *, chat=CHAT_A, instance=INSTANCE, text=None, attachment_id=None, data=None, mime=None):
        message = {"instance_id": instance, "chat_id": chat, "message_id": name,
                   "direction": "incoming", "sent_at": 1780000000,
                   "received_at": "2026-06-01T12:00:00+00:00", "message_type": "textMessage",
                   "text": text or ("invoice " + name)}
        attachment = None
        if attachment_id:
            attachment = {"id": attachment_id, "filename": "invoice.png" if mime != "application/pdf" else "invoice.pdf",
                          "declared_mime_type": mime or "image/png", "state": "pending", "download_url": SIGNED_URL}
        store.save_message(message, attachment)
        with sqlite3.connect(store.db_path) as connection:
            rows[name] = connection.execute("SELECT id FROM messages WHERE message_id=?", (name,)).fetchone()[0]
            if data is not None:
                local_name = attachment_id + "-" + "e" * 32 + ".bin"
                path = store.media_root / local_name
                path.write_bytes(data)
                path.chmod(0o600)
                connection.execute("""
                    UPDATE attachments SET state='ready',local_name=?,mime_type=?,size=?,sha256=?,download_url=NULL
                    WHERE id=?
                """, (local_name, mime, len(data), hashlib.sha256(data).hexdigest(), attachment_id))

    insert("visible-image", attachment_id=PNG_ID, data=PNG_BYTES, mime="image/png")
    insert("visible-pdf", chat=CHAT_B, attachment_id=PDF_ID, data=PDF_BYTES, mime="application/pdf")
    insert("pending-image", attachment_id=PENDING_ID)
    insert("foreign-instance", instance="other-instance", attachment_id=FOREIGN_ID,
           data=b"foreign-binary-data", mime="image/png")
    insert("reader-only-hidden", chat=READER_CHAT)
    insert("ingest-only-hidden", chat=INGEST_CHAT)
    return root, rows, insert


class WireClient:
    def __init__(self, client, tokens, jwks_requests):
        self.client, self.tokens, self.jwks_requests = client, tokens, jwks_requests

    def rpc(self, method, params=None, *, token=DEFAULT_TOKEN, version=LEGACY, headers=None, path="/mcp", request_id=1):
        params = dict(params or {})
        wire_headers = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": version}
        if token is DEFAULT_TOKEN:
            token = self.tokens()
        if token is not None:
            wire_headers["Authorization"] = "Bearer " + token
        if version == MODERN:
            params["_meta"] = {PROTOCOL_VERSION_META_KEY: MODERN, CLIENT_CAPABILITIES_META_KEY: {}}
            wire_headers["MCP-Method"] = method
            if method == "tools/call":
                wire_headers["MCP-Name"] = params.get("name", "")
        if headers:
            wire_headers.update(headers)
        return self.client.post(path, headers=wire_headers,
                                json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})

    def tool(self, name, arguments=None, **kwargs):
        return self.rpc("tools/call", {"name": name, "arguments": arguments or {}}, **kwargs)


@contextmanager
def running_app(env, signing_key, tokens, archive_transport=None):
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key(), as_dict=True)
    jwk.update(kid="fixture-key", use="sig", alg="RS256")
    jwks_requests = []

    def jwks(request):
        jwks_requests.append(request)
        assert str(request.url) == ISSUER + ".well-known/jwks.json"
        assert request.method == "GET"
        assert not ({"authorization", "cookie", "proxy-authorization"} & set(request.headers))
        return httpx.Response(200, json={"keys": [jwk]})

    jwks_client = httpx.AsyncClient(transport=httpx.MockTransport(jwks), trust_env=False)
    try:
        with TestClient(create_app(env, jwks_client=jwks_client, archive_transport=archive_transport), base_url=ORIGIN) as client:
            yield WireClient(client, tokens, jwks_requests)
    finally:
        asyncio.run(jwks_client.aclose())


def remote_archive_transport(root):
    async def handler(request):
        assert request.method == 'GET'
        assert request.url.host == 'source.example.invalid'
        assert request.headers['Authorization'] == 'Bearer ' + 'synthetic-reader-' * 4
        instance, chats = decode_scope(request.headers[SCOPE_HEADER])
        assert instance == INSTANCE and chats == frozenset({CHAT_A, CHAT_B})
        archive = ArchiveReader(root, instance, chats)
        path, p = request.url.path.removeprefix('/api/furniture-reader/mcp'), request.url.params
        try:
            if path == '/chats':
                value = archive.list_chats(p['after_chat_id'], int(p['limit']))
            elif path == '/search':
                assert 'query' not in p
                value = archive.search_messages(decode_query(request.headers[QUERY_HEADER]), p.get('chat_id'), int(p['after_id']), int(p['limit']))
            elif path.startswith('/messages/'):
                value = archive.read_message(int(path.rsplit('/', 1)[1]))
            else:
                value = archive.read_attachment(path.rsplit('/', 1)[1])
                if value is not None and 'data' in value:
                    value = dict(value, data_base64=base64.b64encode(value['data']).decode('ascii'))
                    value.pop('data')
            if value is None:
                return httpx.Response(404)
            return httpx.Response(200, json=envelope(instance, chats, value))
        except ValueError:
            return httpx.Response(400)
        except RuntimeError:
            return httpx.Response(503)
    return httpx.MockTransport(handler)


@pytest.fixture(params=('sqlite', 'http'))
def wire(saved_archive, signing_key, tokens, request):
    root, _rows, _insert = saved_archive
    before = snapshot(root)
    env, transport = environment(root), None
    if request.param == 'http':
        env.update(FURNITURE_MCP_ARCHIVE_BACKEND='http',
                   FURNITURE_MCP_ARCHIVE_URL='https://source.example.invalid/api/furniture-reader/mcp',
                   FURNITURE_MCP_ARCHIVE_TOKEN='synthetic-reader-' * 4)
        env.pop('FURNITURE_STORAGE_DIR')
        transport = remote_archive_transport(root)
    with running_app(env, signing_key, tokens, archive_transport=transport) as client:
        yield client
    assert snapshot(root) == before, "MCP calls must not mutate the archive or attachment jobs"


def tool_result(response):
    assert response.status_code == 200, response.text
    body = response.json()
    assert "error" not in body, body
    result = body["result"]
    assert result.get("isError") is not True, result
    return result


def tool_data(response):
    result = tool_result(response)
    parsed = json.loads(result["content"][0]["text"])
    assert result["structuredContent"] == parsed
    return parsed


@pytest.mark.parametrize("version", [LEGACY, MODERN])
def test_discovery_list_and_call_wire_contracts(wire, version):
    if version == LEGACY:
        response = wire.rpc("initialize", {"protocolVersion": LEGACY, "capabilities": {},
                                           "clientInfo": {"name": "isolated-fixture", "version": "1"}})
        assert response.status_code == 200, response.text
        assert response.json()["result"]["protocolVersion"] == LEGACY
    else:
        response = wire.rpc("server/discover", version=MODERN)
        assert response.status_code == 200, response.text
        discovery = response.json()["result"]
        assert MODERN in discovery["supportedVersions"]
        assert discovery["resultType"] == "complete"
        assert "tools" in discovery["capabilities"]
    assert "mcp-session-id" not in response.headers
    listed = wire.rpc("tools/list", version=version)
    assert listed.status_code == 200, listed.text
    tools = listed.json()["result"]["tools"]
    assert {tool["name"] for tool in tools} == {"list_chats", "search_messages", "read_message", "read_attachment"}
    for tool in tools:
        scopes = [MESSAGE_SCOPE, ATTACHMENT_SCOPE] if tool["name"] == "read_attachment" else [MESSAGE_SCOPE]
        assert tool["securitySchemes"] == [{"type": "oauth2", "scopes": scopes}]
        assert tool["_meta"]["securitySchemes"] == tool["securitySchemes"]
        assert tool["annotations"] == {"readOnlyHint": True, "destructiveHint": False,
                                       "idempotentHint": True, "openWorldHint": False}
        assert tool["inputSchema"]["type"] == "object"
    chats = tool_data(wire.tool("list_chats", version=version))
    assert {chat["chat_id"] for chat in chats["chats"]} == {CHAT_A, CHAT_B}


def test_public_resource_metadata_advertises_read_and_offline_scopes_without_archive_access(wire):
    response = wire.client.get("/.well-known/oauth-protected-resource/mcp")
    assert response.status_code == 200, response.text
    metadata = response.json()
    assert metadata["resource"] == RESOURCE
    assert metadata["authorization_servers"] == [ISSUER]
    assert metadata["scopes_supported"] == [MESSAGE_SCOPE, ATTACHMENT_SCOPE, "offline_access"]
    assert metadata["bearer_methods_supported"] == ["header"]
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert wire.jwks_requests == []
    assert "invoice" not in response.text


@pytest.mark.parametrize("token", [None, "not-a-token", "a.b.c"])
def test_missing_and_invalid_bearer_are_401(wire, token):
    response = wire.rpc("tools/list", token=token)
    assert response.status_code == 401
    assert "Bearer " in response.headers["www-authenticate"]
    assert "resource_metadata=" in response.headers["www-authenticate"]
    assert "invalid_token" in response.headers["www-authenticate"]
    assert response.headers["cache-control"] == "no-store"
    assert wire.jwks_requests == []


def test_cookies_and_reader_shared_secret_never_substitute_for_oauth(wire, tokens):
    token = tokens()
    response = wire.rpc("tools/list", token=None, headers={
        "Cookie": "session=owner; access_token=" + token,
        "X-API-Key": "fake-read-only-shared-secret"})
    assert response.status_code == 401
    response = wire.rpc("tools/list", token="fake-read-only-shared-secret")
    assert response.status_code == 401
    assert wire.jwks_requests == []


@pytest.mark.parametrize("overrides", [
    {"sub": "auth0|other-owner"}, {"azp": "other-client"},
    {"iss": "https://other.example.invalid/"}, {"aud": "https://other.example.invalid/mcp"},
    {"exp": 1, "iat": 0}, {"iat": 1, "exp": 10000},
])
def test_wrong_identity_audience_issuer_and_lifetime_are_denied_over_http(wire, tokens, overrides):
    response = wire.tool("list_chats", token=tokens(**overrides))
    assert response.status_code == 401
    assert "chats" not in response.json()


@pytest.mark.parametrize("missing", ["iss", "aud", "sub", "exp", "iat", "azp", "scope"])
def test_all_required_jwt_claims_are_enforced_over_http(wire, tokens, missing):
    assert wire.rpc("tools/list", token=tokens(omit=(missing,))).status_code == 401


def test_invalid_rsa_signature_is_denied(wire, signing_key, tokens):
    token = tokens()
    claims = jwt.decode(token, options={"verify_signature": False})
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = jwt.encode(claims, other_key, algorithm="RS256", headers={"kid": "fixture-key"})
    assert wire.rpc("tools/list", token=forged).status_code == 401
    assert len(wire.jwks_requests) == 1


@pytest.mark.parametrize("scopes", [
    (ATTACHMENT_SCOPE,), ("offline_access",), (ATTACHMENT_SCOPE, "offline_access"),
])
def test_missing_message_scope_is_http_403(wire, tokens, scopes):
    response = wire.rpc("tools/list", token=tokens(scopes=scopes))
    assert response.status_code == 403
    assert "insufficient_scope" in response.headers["www-authenticate"]
    assert MESSAGE_SCOPE in response.text


def test_offline_scope_preserves_read_access_without_granting_attachment_access(wire, tokens):
    token = tokens(scopes=(MESSAGE_SCOPE, "offline_access"))
    assert tool_data(wire.tool("list_chats", token=token))["chats"]
    response = wire.tool("read_attachment", {"attachment_id": PNG_ID}, token=token)
    result = response.json()["result"]
    assert result["isError"] is True
    assert "insufficient_scope" in result["_meta"]["mcp/www_authenticate"][0]
    assert ATTACHMENT_SCOPE in result["_meta"]["mcp/www_authenticate"][0]
    assert PNG_ID not in response.text


@pytest.mark.parametrize("version", [LEGACY, MODERN])
def test_attachment_scope_challenge_is_tool_metadata(wire, tokens, version):
    token = tokens(scopes=(MESSAGE_SCOPE,))
    assert tool_data(wire.tool("list_chats", token=token, version=version))["chats"]
    response = wire.tool("read_attachment", {"attachment_id": PNG_ID}, token=token, version=version)
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["isError"] is True
    challenges = result["_meta"]["mcp/www_authenticate"]
    assert len(challenges) == 1 and challenges[0].startswith("Bearer ")
    assert "insufficient_scope" in challenges[0]
    assert MESSAGE_SCOPE in challenges[0] and ATTACHMENT_SCOPE in challenges[0]
    assert ORIGIN + "/.well-known/oauth-protected-resource/mcp" in challenges[0]
    assert len(result["content"]) == 1
    assert PNG_ID not in response.text


def test_tools_restrict_instance_owner_grants_and_safe_metadata(wire, saved_archive):
    _root, rows, _insert = saved_archive
    results = tool_data(wire.tool("search_messages", {"query": "invoice"}))
    assert {row["message_id"] for row in results["messages"]} == {"visible-image", "visible-pdf", "pending-image"}
    visible = tool_data(wire.tool("read_message", {"message_id": rows["visible-image"]}))
    assert visible["chat_id"] == CHAT_A and visible["instance_id"] == INSTANCE
    for hidden in ("foreign-instance", "reader-only-hidden", "ingest-only-hidden"):
        response = wire.tool("read_message", {"message_id": rows[hidden]})
        assert response.status_code == 200
        assert response.json()["result"]["isError"] is True
        assert hidden not in response.text
    for chat in (READER_CHAT, INGEST_CHAT, "unknown@g.us"):
        assert tool_data(wire.tool("search_messages", {"query": "invoice", "chat_id": chat}))["messages"] == []
    foreign = wire.tool("read_attachment", {"attachment_id": FOREIGN_ID})
    assert foreign.json()["result"]["isError"] is True
    pending = tool_data(wire.tool("read_attachment", {"attachment_id": PENDING_ID}))
    assert pending["state"] == "pending" and pending["attempts"] == 0
    serialized = json.dumps((results, visible, pending))
    for forbidden in (SIGNED_URL, "download_url", "local_name", "claim_token", "lease_until"):
        assert forbidden not in serialized


def test_bounded_cursor_pagination(wire):
    first = tool_data(wire.tool("list_chats", {"limit": 1}))
    assert len(first["chats"]) == 1 and first["next_after_chat_id"] == CHAT_A
    second = tool_data(wire.tool("list_chats", {"limit": 1, "after_chat_id": first["next_after_chat_id"]}))
    assert [row["chat_id"] for row in second["chats"]] == [CHAT_B]
    assert second["next_after_chat_id"] is None
    first = tool_data(wire.tool("search_messages", {"query": "invoice", "limit": 1}))
    second = tool_data(wire.tool("search_messages", {"query": "invoice", "limit": 1, "after_id": first["next_after_id"]}))
    assert first["messages"][0]["id"] != second["messages"][0]["id"]


@pytest.mark.parametrize("attachment_id,mime,expected", [(PNG_ID, "image/png", PNG_BYTES), (PDF_ID, "application/pdf", PDF_BYTES)])
def test_attachments_are_authorized_mcp_content_without_public_urls(wire, attachment_id, mime, expected):
    result = tool_result(wire.tool("read_attachment", {"attachment_id": attachment_id}))
    assert result["structuredContent"]["mime_type"] == mime
    assert len(result["content"]) == 2
    attachment = result["content"][1]
    if mime == "application/pdf":
        assert attachment["type"] == "resource"
        assert attachment["resource"]["uri"] == "furniture-attachment:" + attachment_id
        encoded = attachment["resource"]["blob"]
    else:
        assert attachment["type"] == "image" and attachment["mimeType"] == mime
        encoded = attachment["data"]
    assert base64.b64decode(encoded) == expected
    assert "https://" not in json.dumps(result)


@pytest.mark.parametrize("name", ["send_message", "create_transaction", "create_supply", "delete_message", "unknown_tool"])
def test_only_four_read_tools_exist_and_unknown_calls_are_rejected(wire, name):
    response = wire.tool(name)
    assert response.status_code == 200
    body = response.json()
    assert "error" in body or body["result"].get("isError") is True


@pytest.mark.parametrize("name,arguments", [
    ("list_chats", {"limit": 51}), ("list_chats", {"limit": True}),
    ("search_messages", {"query": "x"}), ("search_messages", {"query": "  "}),
    ("search_messages", {"query": "x" * 201}), ("search_messages", {"query": "invoice", "after_id": -1}),
    ("read_message", {"message_id": True}), ("read_message", {"message_id": 0}),
    ("read_attachment", {"attachment_id": "../../archive.sqlite3"}),
])
def test_invalid_tool_parameters_are_rejected(wire, name, arguments):
    response = wire.tool(name, arguments)
    assert response.status_code == 200
    body = response.json()
    assert "error" in body or body["result"].get("isError") is True


@pytest.mark.parametrize("path", ["/", "/health", "/archive.sqlite3", "/media/" + PNG_ID,
                                   "/static/" + PNG_ID, "/api/furniture/messages", "/sse", "/mcp/assets"])
def test_no_public_data_routes(wire, tokens, path):
    response = wire.client.get(path, headers={"Authorization": "Bearer " + tokens()})
    assert response.status_code == 404
    assert "invoice" not in response.text


@pytest.mark.parametrize("headers,status", [({"Host": "attacker.example.invalid"}, 421),
                                          ({"Origin": "https://attacker.example.invalid"}, 403)])
def test_host_and_origin_are_checked(wire, headers, status):
    assert wire.rpc("tools/list", headers=headers).status_code == status


def test_query_credentials_and_duplicate_authorization_are_rejected(wire, tokens):
    assert wire.rpc("tools/list", path="/mcp?access_token=" + tokens()).status_code == 400
    assert wire.rpc("tools/list", path="/mcp?unrelated=true").status_code == 400
    token = "Bearer " + tokens()
    response = wire.client.post("/mcp", headers=[("Authorization", token), ("authorization", token),
                                               ("Accept", "application/json, text/event-stream")],
                                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    assert response.status_code == 400
    assert wire.jwks_requests == []


def test_modern_request_routing_headers_must_match_body(wire):
    response = wire.rpc("tools/list", version=MODERN, headers={"MCP-Method": "tools/call"})
    assert response.status_code == 400, response.text
    response = wire.tool("list_chats", version=MODERN, headers={"MCP-Name": "send_message"})
    assert response.status_code == 400, response.text


def test_request_body_over_32_kib_is_rejected(wire, tokens):
    content = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"padding": "x" * 32768}})
    response = wire.client.post("/mcp", content=content, headers={
        "Authorization": "Bearer " + tokens(), "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": LEGACY})
    assert response.status_code == 413, response.text


@pytest.mark.parametrize("text", ["invoice " + "ж\"\\\n" * 12000, "invoice " + "🙂" * 20000])
def test_full_http_message_and_search_payloads_are_bounded(saved_archive, signing_key, tokens, text):
    root, rows, insert = saved_archive
    insert("long-visible", text=text)
    before = snapshot(root)
    with running_app(environment(root), signing_key, tokens) as wire:
        response = wire.tool("read_message", {"message_id": rows["long-visible"]})
        assert len(response.content) <= 128 * 1024
        assert tool_data(response)["text_truncated"] is True
        response = wire.tool("search_messages", {"query": "invoice", "limit": 50})
        assert len(response.content) <= 64 * 1024
        assert any(row["message_id"] == "long-visible" for row in tool_data(response)["messages"])
    assert snapshot(root) == before


def test_disabled_app_is_404_without_jwks_or_archive_creation(tmp_path, signing_key, tokens):
    root = tmp_path / "must-not-exist"
    env = environment(root)
    env["FURNITURE_MCP_ENABLED"] = "false"
    env["FURNITURE_MCP_AUTH0_ISSUER"] = "invalid-disabled-setting"
    with running_app(env, signing_key, tokens) as wire:
        for path in ("/mcp", "/.well-known/oauth-protected-resource/mcp", "/archive.sqlite3"):
            assert wire.client.get(path).status_code == 404
        assert wire.rpc("tools/list").status_code == 404
        assert wire.jwks_requests == []
    assert not root.exists()


def test_enabled_reads_do_not_create_missing_archive(tmp_path, signing_key, tokens):
    root = (tmp_path / "missing-private-archive").resolve()
    with running_app(environment(root), signing_key, tokens) as wire:
        assert tool_data(wire.tool("list_chats"))["chats"] == []
        assert tool_data(wire.tool("search_messages", {"query": "invoice"}))["messages"] == []
    assert not root.exists()


def test_standalone_import_subprocess_excludes_accounting_and_ingestion_modules(tmp_path):
    project = str(Path(__file__).resolve().parents[2])
    script = r'''
import pathlib, socket, sys
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
def blocked(*args, **kwargs):
    raise AssertionError("No network in import isolation check")
socket.create_connection = blocked
socket.getaddrinfo = blocked
socket.socket.connect = blocked
socket.socket.connect_ex = blocked
from furniture_mcp.server import create_app
create_app({"FURNITURE_MCP_ENABLED": "false"})
for prefix in ("web_app", "database", "poster_client", "finance", "financial", "gemini",
               "google.genai", "google.generativeai", "requests", "telegram",
               "furniture_reader.service", "furniture_reader.media"):
    assert not any(name == prefix or name.startswith(prefix + ".") for name in sys.modules), prefix
assert not list(pathlib.Path.cwd().iterdir())
print("isolated-import-ok")
'''
    result = subprocess.run([sys.executable, "-I", "-c", script, project], cwd=tmp_path,
                            env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1",
                                 "PYTHON_DOTENV_DISABLED": "1"}, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "isolated-import-ok"
