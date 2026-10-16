"""Offline verifier tests: ephemeral fixture keys and .invalid JWKS transport only."""

import asyncio
import json
import time

import httpx2 as httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from furniture_mcp import auth
from furniture_mcp.auth import Auth0Verifier


ISSUER = "https://fixture-auth.invalid/"
RESOURCE = "https://fixture-mcp.invalid/mcp"
OWNER = "auth0|fixture-owner"
CLIENT_ID = "fixture-chatgpt-client"
MESSAGE_SCOPE = "furniture.messages:read"
ATTACHMENT_SCOPE = "furniture.attachments:read"


def run(awaitable):
    return asyncio.run(awaitable)


@pytest.fixture(scope="module")
def private_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def second_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def jwk(key, kid="fixture-key"):
    entry = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    return {**entry, "kid": kid, "alg": "RS256", "use": "sig"}


def claims(**updates):
    issued = int(time.time()) - 1
    return {
        "iss": ISSUER, "aud": RESOURCE, "sub": OWNER, "azp": CLIENT_ID,
        "iat": issued, "exp": issued + 300, "scope": MESSAGE_SCOPE,
        **updates,
    }


def signed(key, *, payload=None, headers=None):
    # JWS signs arbitrary fixture JSON so malformed-claim cases reach the verifier.
    return jwt.api_jws.encode(
        json.dumps(claims() if payload is None else payload).encode(), key, algorithm="RS256",
        headers={"kid": "fixture-key", **(headers or {})},
    )


@pytest.fixture
def factory(private_key):
    clients = []

    def create(*, handler=None, entries=None, **kwargs):
        requests = []

        async def handle(request):
            requests.append(request)
            if handler is not None:
                result = handler(request)
                return await result if hasattr(result, "__await__") else result
            return httpx.Response(200, json={"keys": entries if entries is not None else [jwk(private_key)]})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handle), trust_env=False)
        clients.append(client)
        verifier = Auth0Verifier(
            issuer=ISSUER, resource=RESOURCE, owner_sub=OWNER, client_id=CLIENT_ID,
            client=client, **kwargs,
        )
        return verifier, requests, client

    yield create
    for client in clients:
        run(client.aclose())


def test_valid_owner_token_and_minimal_access_context(factory, private_key):
    verifier, requests, _ = factory()
    payload = claims(scope=MESSAGE_SCOPE + " " + ATTACHMENT_SCOPE,
                     email="private@example.invalid", phone_number="+10000000000")
    token = signed(private_key, payload=payload)
    access = run(verifier.verify_token(token))
    assert access is not None
    assert access.token == token
    assert access.subject == OWNER
    assert access.client_id == CLIENT_ID
    assert access.resource == RESOURCE
    assert access.expires_at == payload["exp"]
    assert access.scopes == [MESSAGE_SCOPE, ATTACHMENT_SCOPE]
    assert access.claims == {"iss": ISSUER}
    assert len(requests) == 1


@pytest.mark.parametrize("audience", [RESOURCE, [RESOURCE], [ISSUER + "userinfo", RESOURCE]])
def test_auth0_resource_may_share_audience_with_userinfo(factory, private_key, audience):
    verifier, _, _ = factory()
    assert run(verifier.verify_token(signed(private_key, payload=claims(aud=audience)))) is not None


@pytest.mark.parametrize("scope", [MESSAGE_SCOPE, ATTACHMENT_SCOPE, "openid profile email", "offline_access"])
def test_valid_scope_syntax_is_returned_for_endpoint_authorization(factory, private_key, scope):
    verifier, _, _ = factory()
    access = run(verifier.verify_token(signed(private_key, payload=claims(scope=scope))))
    assert access is not None
    assert access.scopes == scope.split(" ")


@pytest.mark.parametrize("name", ["iss", "aud", "sub", "azp", "iat", "exp", "scope"])
def test_missing_required_claim_fails_before_jwks(factory, private_key, name):
    verifier, requests, _ = factory()
    payload = claims()
    del payload[name]
    assert run(verifier.verify_token(signed(private_key, payload=payload))) is None
    assert requests == []


@pytest.mark.parametrize(("name", "value"), [
    ("iss", "https://attacker.invalid/"), ("iss", ISSUER.rstrip("/")), ("iss", [ISSUER]),
    ("sub", "auth0|other-owner"), ("sub", [OWNER]), ("sub", None),
    ("azp", "another-client"), ("azp", [CLIENT_ID]), ("azp", None),
    ("aud", "https://other-resource.invalid/mcp"), ("aud", RESOURCE + "/"),
    ("aud", []), ("aud", [RESOURCE, 4]), ("aud", {"aud": RESOURCE}), ("aud", None),
    ("scope", []), ("scope", None), ("scope", ""), ("scope", " "),
    ("scope", MESSAGE_SCOPE + "  " + ATTACHMENT_SCOPE), ("scope", MESSAGE_SCOPE + "\n"),
    ("scope", " scope"), ("scope", "scope "), ("scope", 'scope"read'),
    ("scope", "scope\\read"), ("scope", "scope\tread"), ("scope", "чтение"),
    ("iat", True), ("iat", "1"), ("iat", 1.5), ("iat", None), ("iat", -1),
    ("exp", True), ("exp", "1"), ("exp", 1.5), ("exp", None), ("exp", -1),
    ("nbf", True), ("nbf", "1"), ("nbf", 1.5), ("nbf", None), ("nbf", -1),
])
def test_wrong_identity_audience_or_claim_types_fail_closed(factory, private_key, name, value):
    verifier, requests, _ = factory()
    assert run(verifier.verify_token(signed(private_key, payload=claims(**{name: value})))) is None
    assert requests == []


@pytest.mark.parametrize("case", ["expired", "future_iat", "future_nbf", "too_long", "backwards", "zero"])
def test_token_time_boundaries(factory, private_key, case):
    verifier, _, _ = factory()
    now = int(time.time())
    values = {
        "expired": {"iat": now - 301, "exp": now - 1},
        "future_iat": {"iat": now + 60, "exp": now + 120},
        "future_nbf": {"nbf": now + 60},
        "too_long": {"iat": now - 1, "exp": now + 900},
        "backwards": {"iat": now - 1, "exp": now - 2},
        "zero": {"iat": now - 1, "exp": now - 1},
    }
    assert run(verifier.verify_token(signed(private_key, payload=claims(**values[case])))) is None


def test_shorter_configured_lifetime_and_local_revocation_cutoff(factory, private_key):
    issued = int(time.time()) - 2
    verifier, _, _ = factory(max_token_lifetime=60, min_issued_at=issued)
    assert run(verifier.verify_token(signed(private_key, payload=claims(iat=issued, exp=issued + 60)))) is not None
    assert run(verifier.verify_token(signed(private_key, payload=claims(iat=issued, exp=issued + 61)))) is None
    assert run(verifier.verify_token(signed(private_key, payload=claims(iat=issued - 1, exp=issued + 59)))) is None


def test_wrong_signature_and_payload_tampering(factory, private_key, second_key):
    verifier, _, _ = factory()
    assert run(verifier.verify_token(signed(second_key))) is None
    token = signed(private_key)
    header, payload, signature = token.split(".")
    tampered_payload = jwt.utils.base64url_encode(json.dumps(claims(scope=ATTACHMENT_SCOPE)).encode()).decode()
    assert run(verifier.verify_token(".".join((header, tampered_payload, signature)))) is None


@pytest.mark.parametrize("token", [None, b"bytes", "", "opaque-token", "a.b.c", "a.b", "a.b.c.d", "a" * 16385,
                                    "a=.b.c", " a.b.c", "a.b.c\n", "é.b.c"])
def test_malformed_token_does_not_fetch_jwks(factory, token):
    verifier, requests, _ = factory()
    assert run(verifier.verify_token(token)) is None
    assert requests == []


@pytest.mark.parametrize("headers", [
    {"kid": None}, {"kid": 1}, {"kid": ""}, {"kid": "x" * 129}, {"kid": "line\nbreak"},
    {"kid": "é"}, {"jwk": {}}, {"jku": "https://attacker.invalid/jwks"},
    {"x5u": "https://attacker.invalid/key"}, {"x5c": []}, {"crit": []},
    {"b64": True}, {"typ": "not-a-token"},
])
def test_untrusted_header_material_is_rejected_before_network(factory, private_key, headers):
    verifier, requests, _ = factory()
    token = signed(private_key)
    _, payload, signature = token.split(".")
    header = jwt.utils.base64url_encode(json.dumps({"alg": "RS256", "kid": "fixture-key", **headers}).encode()).decode()
    assert run(verifier.verify_token(".".join((header, payload, signature)))) is None
    assert requests == []


@pytest.mark.parametrize("algorithm", ["none", "HS256", "RS512"])
def test_only_rs256_accepted(factory, private_key, algorithm):
    verifier, requests, _ = factory()
    key = None if algorithm == "none" else "fixture-secret-with-adequate-length-123456" if algorithm == "HS256" else private_key
    token = jwt.encode(claims(), key, algorithm=algorithm, headers={"kid": "fixture-key"})
    assert run(verifier.verify_token(token)) is None
    assert requests == []


@pytest.mark.parametrize("part", ["header", "payload"])
def test_duplicate_json_members_are_rejected(factory, private_key, part):
    verifier, requests, _ = factory()
    header, payload, signature = signed(private_key).split(".")
    if part == "header":
        header = jwt.utils.base64url_encode(b'{"alg":"RS256","kid":"fixture-key","kid":"other"}').decode()
    else:
        raw = json.dumps(claims()).rstrip("}") + ', "sub": "auth0|other-owner"}'
        payload = jwt.utils.base64url_encode(raw.encode()).decode()
    assert run(verifier.verify_token(".".join((header, payload, signature)))) is None
    assert requests == []


def test_cache_rotation_and_unknown_kid_cooldown(factory, private_key, second_key, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(auth.time, "monotonic", lambda: clock[0])
    entries = [jwk(private_key)]
    verifier, requests, _ = factory(entries=entries)
    old = signed(private_key)
    new = signed(second_key, headers={"kid": "rotated-key"})
    assert run(verifier.verify_token(old)) is not None
    assert run(verifier.verify_token(old)) is not None
    assert len(requests) == 1
    entries[:] = [jwk(private_key), jwk(second_key, "rotated-key")]
    assert run(verifier.verify_token(new)) is None
    assert len(requests) == 1
    clock[0] += 30
    assert run(verifier.verify_token(new)) is not None
    assert len(requests) == 2
    for n in range(8):
        assert run(verifier.verify_token(signed(second_key, headers={"kid": f"missing-{n}"}))) is None
    assert len(requests) == 2
    clock[0] += 30
    assert run(verifier.verify_token(signed(second_key, headers={"kid": "unknown"}))) is None
    assert len(requests) == 3


def test_expired_jwks_cache_does_not_survive_network_outage(factory, private_key, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(auth.time, "monotonic", lambda: clock[0])
    failing = [False]

    def handler(request):
        if failing[0]:
            raise httpx.ConnectError("offline", request=request)
        return httpx.Response(200, json={"keys": [jwk(private_key)]})

    verifier, requests, _ = factory(handler=handler)
    token = signed(private_key)
    assert run(verifier.verify_token(token)) is not None
    failing[0] = True
    clock[0] += 299
    assert run(verifier.verify_token(token)) is not None
    clock[0] += 1
    assert run(verifier.verify_token(token)) is None
    assert len(requests) == 2
    assert run(verifier.verify_token(token)) is None
    assert len(requests) == 2
    clock[0] += 30
    failing[0] = False
    assert run(verifier.verify_token(token)) is not None
    assert len(requests) == 3


def test_concurrent_requests_share_one_key_refresh(factory, private_key):
    async def handler(_request):
        await asyncio.sleep(0)
        return httpx.Response(200, json={"keys": [jwk(private_key)]})

    verifier, requests, _ = factory(handler=handler)
    token = signed(private_key)

    async def exercise():
        results = await asyncio.gather(*(verifier.verify_token(token) for _ in range(20)))
        assert all(result is not None for result in results)

    run(exercise())
    assert len(requests) == 1


@pytest.mark.parametrize("response", [
    (301, {}, b""), (302, {"location": "https://attacker.invalid/"}, b""),
    (429, {}, b""), (500, {}, b""), (200, {}, b"not-json"), (200, {}, b"[]"),
    (200, {}, b'{"keys":[]}'), (200, {}, b'{"keys":{}}'), (200, {}, b'{"keys":[null]}'),
    (200, {}, b'{"keys":[],"keys":[]}'),
    (200, {"content-encoding": "gzip"}, b""),
    (200, {"content-length": "1048577"}, b""), (200, {"content-length": "bad"}, b""),
    (200, {}, b"x" * 1048577),
])
def test_bad_jwks_http_responses_fail_closed(factory, private_key, response):
    status, headers, body = response
    verifier, requests, _ = factory(handler=lambda _: httpx.Response(status, headers=headers, content=body))
    assert run(verifier.verify_token(signed(private_key))) is None
    assert len(requests) == 1
    assert str(requests[0].url) == ISSUER + ".well-known/jwks.json"


@pytest.mark.parametrize("update", [
    {"alg": "HS256"}, {"alg": None}, {"use": "enc"}, {"use": None},
    {"kty": "EC"}, {"kid": ""}, {"kid": "x" * 129}, {"kid": None},
    {"key_ops": ["sign"]}, {"key_ops": ["verify", "sign"]},
    {"d": "private-material"}, {"p": "private-material"}, {"x5u": "https://attacker.invalid/"},
    {"n": ""}, {"n": "x" * 1367}, {"n": "invalid!"}, {"n": None},
    {"e": ""}, {"e": "x" * 9}, {"e": None},
])
def test_bad_jwk_key_properties_fail_closed(factory, private_key, update):
    verifier, _, _ = factory(entries=[{**jwk(private_key), **update}])
    assert run(verifier.verify_token(signed(private_key))) is None


def test_duplicate_key_ids_and_excess_keys_are_rejected(factory, private_key):
    for entries in ([jwk(private_key), jwk(private_key)], [jwk(private_key, f"key-{n}") for n in range(33)]):
        verifier, _, _ = factory(entries=entries)
        assert run(verifier.verify_token(signed(private_key))) is None


def test_rsa_keys_under_2048_bits_are_rejected(factory, private_key):
    weak = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    verifier, _, _ = factory(entries=[jwk(weak)])
    assert run(verifier.verify_token(signed(private_key))) is None


def test_failed_refresh_is_rate_limited(factory, private_key):
    verifier, requests, _ = factory(handler=lambda _: httpx.Response(503))

    async def exercise():
        results = await asyncio.gather(*(verifier.verify_token(signed(private_key)) for _ in range(20)))
        assert results == [None] * 20

    run(exercise())
    assert len(requests) == 1


def test_streamed_jwks_body_is_bounded_even_with_false_content_length(factory, private_key):
    class OversizedStream(httpx.AsyncByteStream):
        def __init__(self):
            self.closed = False

        async def __aiter__(self):
            yield b" " * 1048576
            yield b"x"

        async def aclose(self):
            self.closed = True

    stream = OversizedStream()
    verifier, _, _ = factory(handler=lambda _: httpx.Response(
        200, headers={"content-length": "1"}, stream=stream,
    ))
    assert run(verifier.verify_token(signed(private_key))) is None
    assert stream.closed


def test_jwks_total_request_timeout_fails_closed(factory, private_key, monkeypatch):
    monkeypatch.setattr(auth, "_JWKS_TIMEOUT", 0.01)

    async def slow_handler(_request):
        await asyncio.sleep(1)
        return httpx.Response(200, json={"keys": [jwk(private_key)]})

    verifier, requests, _ = factory(handler=slow_handler)
    start = time.monotonic()
    assert run(verifier.verify_token(signed(private_key))) is None
    assert time.monotonic() - start < 0.5
    assert len(requests) == 1


def test_token_errors_do_not_log_token_or_claims(factory, private_key, caplog):
    verifier, _, _ = factory(handler=lambda _: httpx.Response(503))
    token = signed(private_key, payload=claims(phone_number="+10000000000", message="private furniture text"))
    with caplog.at_level("DEBUG"):
        assert run(verifier.verify_token(token)) is None
    assert token not in caplog.text
    assert OWNER not in caplog.text
    assert CLIENT_ID not in caplog.text
    assert "+10000000000" not in caplog.text
    assert "private furniture text" not in caplog.text


def test_jwks_request_cannot_contain_token_or_private_identity(factory, private_key):
    verifier, requests, client = factory()
    client.headers.update({"Authorization": "Bearer not-for-auth0", "X-Private": "private-default"})
    client.cookies.set("session", "private-cookie")
    client.auth = httpx.BasicAuth("private-user", "private-password")
    token = signed(private_key, payload=claims(phone_number="+10000000000", message="private furniture text"))
    assert run(verifier.verify_token(token)) is not None
    request = requests[0]
    assert request.method == "GET"
    assert str(request.url) == ISSUER + ".well-known/jwks.json"
    assert request.content == b""
    assert set(request.headers) == {"host", "accept", "accept-encoding"}
    serialized = str(request.url) + str(dict(request.headers)) + str(request.content)
    for private in (token, OWNER, CLIENT_ID, "+10000000000", "private furniture text", "private-default", "private-cookie"):
        assert private not in serialized


def test_owned_http_client_disables_proxies_redirects_and_closes(private_key, monkeypatch):
    original_client = httpx.AsyncClient
    calls = []
    clients = []

    def create(**kwargs):
        calls.append(kwargs)
        client = original_client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"keys": [jwk(private_key)]})), **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(auth.httpx, "AsyncClient", create)
    verifier = Auth0Verifier(issuer=ISSUER, resource=RESOURCE, owner_sub=OWNER, client_id=CLIENT_ID)
    assert calls == []
    assert run(verifier.verify_token("bad-token")) is None
    assert calls == []
    assert run(verifier.verify_token(signed(private_key))) is not None
    assert calls[0]["trust_env"] is False
    assert calls[0]["follow_redirects"] is False
    assert calls[0]["timeout"] == 5.0
    run(verifier.aclose())
    assert clients[0].is_closed
    assert run(verifier.verify_token(signed(private_key))) is None
    assert len(calls) == 1


def test_close_does_not_close_injected_client(factory):
    verifier, _, client = factory()
    run(verifier.aclose())
    assert not client.is_closed


@pytest.mark.parametrize(("setting", "value"), [
    ("issuer", "http://fixture-auth.invalid/"), ("issuer", "https://fixture-auth.invalid"),
    ("issuer", "https://fixture-auth.invalid/path/"), ("issuer", "https://user:pass@fixture-auth.invalid/"),
    ("issuer", "https://fixture-auth.invalid/?query"), ("issuer", "https://fixture-auth.invalid/#fragment"),
    ("issuer", "https://fixture-auth.invalid:444/"), ("issuer", "https://127.0.0.1/"),
    ("issuer", "https://localhost/"), ("issuer", "https://fixture-auth.invalid\\@attacker.invalid/"),
    ("resource", "http://fixture-mcp.invalid/mcp"), ("resource", "https://fixture-mcp.invalid/mcp?query"),
    ("owner_sub", ""), ("client_id", ""), ("owner_sub", None),
    ("max_token_lifetime", 901), ("max_token_lifetime", 0), ("max_token_lifetime", True),
    ("min_issued_at", -1), ("min_issued_at", True), ("min_issued_at", "0"),
])
def test_unsafe_configuration_rejected_without_network(setting, value):
    values = {"issuer": ISSUER, "resource": RESOURCE, "owner_sub": OWNER, "client_id": CLIENT_ID, setting: value}
    with pytest.raises(ValueError):
        Auth0Verifier(**values)
