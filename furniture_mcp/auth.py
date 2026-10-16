"""Auth0 access-token verification for the separate, read-only MCP resource server.

This module never logs tokens, issues credentials, or calls an Auth0 user API.
Its only outbound request is an unauthenticated GET of the configured public
JWKS. Cached keys expire after five minutes, even when Auth0 is unavailable.
Revoking an Auth0 refresh grant does not immediately revoke a signed access
token: use the local ``min_issued_at`` cutoff (and restart) or disable the server
for an immediate local withdrawal of access.
"""

from __future__ import annotations

import asyncio
import hmac
import ipaddress
import json
import re
import time
from typing import Any
from urllib.parse import urlsplit

import httpx2 as httpx
import jwt
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from mcp.server.auth.provider import AccessToken, TokenVerifier


_MAX_TOKEN_BYTES = 16 * 1024
_MAX_JWKS_BYTES = 1024 * 1024
_JWKS_TIMEOUT = 5.0
_KEY_TTL = 300.0
_REFRESH_COOLDOWN = 30.0
_KID = re.compile(r"[\x21-\x7e]{1,128}\Z")
_SCOPE = re.compile(r"[\x21\x23-\x5b\x5d-\x7e]+(?: [\x21\x23-\x5b\x5d-\x7e]+)*\Z")
_JWT_PART = re.compile(r"[A-Za-z0-9_-]+\Z")
_REQUIRED = ("iss", "aud", "sub", "exp", "iat", "azp", "scope")


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("Duplicate JSON member")
        result[name] = value
    return result


def _load_json(value: bytes) -> Any:
    return json.loads(value, object_pairs_hook=_object_without_duplicates)


def _same(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def _https_url(value: str, *, issuer: bool = False) -> bool:
    if not isinstance(value, str) or not value or len(value) > 2048:
        return False
    if any(ord(char) <= 32 or ord(char) >= 127 for char in value):
        return False
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname or ""
        if len(hostname) > 253 or "." not in hostname or any(
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in hostname.split(".")
        ):
            return False
        try:
            ipaddress.ip_address(hostname)
        except ValueError:
            pass
        else:
            return False
        return bool(
            value.startswith("https://")
            and parsed.scheme == "https"
            and parsed.hostname
            and parsed.username is None
            and parsed.password is None
            and parsed.port in (None, 443)
            and not parsed.query
            and not parsed.fragment
            and "?" not in value
            and "#" not in value
            and "\\" not in value
            and (not issuer or parsed.path == "/")
        )
    except ValueError:
        return False


class Auth0Verifier(TokenVerifier):
    """Validate one configured owner and one pre-registered OAuth client.

    ``client`` is a test seam for an AsyncClient using MockTransport. Production
    callers omit it: the owned client disables environment proxies and redirects.
    Required endpoint/tool scopes are enforced by the SDK and tool handlers;
    this verifier validates the shape and authenticity of the granted scopes.
    """

    def __init__(
        self,
        *,
        issuer: str,
        resource: str,
        owner_sub: str,
        client_id: str,
        max_token_lifetime: int = 900,
        client: httpx.AsyncClient | None = None,
        min_issued_at: int = 0,
    ) -> None:
        if not _https_url(issuer, issuer=True) or not _https_url(resource):
            raise ValueError("Trusted HTTPS issuer and resource URLs are required")
        if any(not isinstance(value, str) or not value or len(value) > 1024
               for value in (owner_sub, client_id)):
            raise ValueError("Explicit owner subject and OAuth client ID are required")
        if type(max_token_lifetime) is not int or not 1 <= max_token_lifetime <= 900:
            raise ValueError("Access-token lifetime must be between 1 and 900 seconds")
        if type(min_issued_at) is not int or min_issued_at < 0:
            raise ValueError("Local token cutoff must be a nonnegative integer")
        self._issuer = issuer
        self._resource = resource
        self._owner_sub = owner_sub
        self._client_id = client_id
        self._max_token_lifetime = max_token_lifetime
        self._min_issued_at = min_issued_at
        self._jwks_url = issuer + ".well-known/jwks.json"
        self._client = client
        self._owns_client = client is None
        self._closed = False
        self._keys: dict[str, RSAPublicKey] = {}
        self._keys_expires_at = 0.0
        self._last_refresh_attempt = float("-inf")
        self._refresh_lock = asyncio.Lock()

    async def aclose(self) -> None:
        """Close only the verifier-owned HTTP client; never an injected client."""
        self._closed = True
        self._keys.clear()
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    def _allowed_claims(self, claims: Any) -> bool:
        if not isinstance(claims, dict) or any(name not in claims for name in _REQUIRED):
            return False
        if any(not isinstance(claims[name], str) for name in ("iss", "sub", "azp", "scope")):
            return False
        if not (_same(claims["iss"], self._issuer)
                and _same(claims["sub"], self._owner_sub)
                and _same(claims["azp"], self._client_id)):
            return False
        audience = claims["aud"]
        if isinstance(audience, str):
            audiences = [audience]
        elif isinstance(audience, list) and audience and all(isinstance(x, str) for x in audience):
            audiences = audience
        else:
            return False
        if self._resource not in audiences or not _SCOPE.fullmatch(claims["scope"]):
            return False
        for name in ("iat", "exp", "nbf"):
            if name in claims and (type(claims[name]) is not int or claims[name] < 0):
                return False
        lifetime = claims["exp"] - claims["iat"]
        return (0 < lifetime <= self._max_token_lifetime
                and claims["iat"] >= self._min_issued_at)

    async def verify_token(self, token: str) -> AccessToken | None:
        """Fail closed without returning details or logging attacker-controlled data."""
        if self._closed or not isinstance(token, str) or not 0 < len(token) <= _MAX_TOKEN_BYTES:
            return None
        try:
            parts = token.split(".")
            if len(parts) != 3 or any(not _JWT_PART.fullmatch(part) for part in parts):
                return None
            header = _load_json(jwt.utils.base64url_decode(parts[0]))
            claims = _load_json(jwt.utils.base64url_decode(parts[1]))
            if not isinstance(header, dict) or header.get("alg") != "RS256":
                return None
            if any(name in header for name in ("jwk", "jku", "x5u", "x5c", "crit", "b64")):
                return None
            if header.get("typ", "JWT") not in ("JWT", "at+jwt"):
                return None
            kid = header.get("kid")
            if not isinstance(kid, str) or not _KID.fullmatch(kid) or not self._allowed_claims(claims):
                return None
            key = await self._key_for(kid)
            if key is None:
                return None
            verified = jwt.decode(
                token,
                key=key,
                algorithms=["RS256"],
                issuer=self._issuer,
                audience=self._resource,
                leeway=0,
                options={"require": list(_REQUIRED)},
            )
            if not self._allowed_claims(verified):
                return None
            return AccessToken(
                token=token,
                client_id=self._client_id,
                subject=self._owner_sub,
                scopes=verified["scope"].split(" "),
                expires_at=verified["exp"],
                resource=self._resource,
                claims={"iss": self._issuer},
            )
        except (jwt.PyJWTError, ValueError, TypeError, KeyError, OverflowError, RecursionError):
            return None

    async def _key_for(self, kid: str) -> RSAPublicKey | None:
        now = time.monotonic()
        if now < self._keys_expires_at and kid in self._keys:
            return self._keys[kid]
        async with self._refresh_lock:
            now = time.monotonic()
            if now < self._keys_expires_at and kid in self._keys:
                return self._keys[kid]
            if now - self._last_refresh_attempt < _REFRESH_COOLDOWN:
                return None
            self._last_refresh_attempt = now
            try:
                keys = await self._fetch_keys()
            except (httpx.HTTPError, TimeoutError, ValueError, TypeError, KeyError,
                    OverflowError, RecursionError):
                # Do not extend the original TTL on any network/parse failure.
                return None
            self._keys = keys
            self._keys_expires_at = time.monotonic() + _KEY_TTL
            return self._keys.get(kid)

    async def _fetch_keys(self) -> dict[str, RSAPublicKey]:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=_JWKS_TIMEOUT,
                trust_env=False,
                follow_redirects=False,
                limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
            )
        # A raw request avoids injected clients' default auth, cookies and headers.
        request = httpx.Request(
            "GET",
            self._jwks_url,
            headers={"Accept": "application/json", "Accept-Encoding": "identity"},
            extensions={"timeout": dict.fromkeys(("connect", "read", "write", "pool"), _JWKS_TIMEOUT)},
        )
        async with asyncio.timeout(_JWKS_TIMEOUT):
            response = await self._client.send(request, stream=True, auth=None, follow_redirects=False)
            try:
                if response.status_code != 200:
                    raise ValueError("JWKS response rejected")
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise ValueError("Encoded JWKS response rejected")
                content_length = response.headers.get("content-length")
                if content_length is not None and (not content_length.isdecimal()
                                                    or int(content_length) > _MAX_JWKS_BYTES):
                    raise ValueError("JWKS size rejected")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(data) + len(chunk) > _MAX_JWKS_BYTES:
                        raise ValueError("JWKS size rejected")
                    data.extend(chunk)
            finally:
                await response.aclose()
        document = _load_json(bytes(data))
        if not isinstance(document, dict) or not isinstance(document.get("keys"), list):
            raise ValueError("Invalid JWKS")
        if not 1 <= len(document["keys"]) <= 32:
            raise ValueError("Invalid JWKS key count")
        keys: dict[str, RSAPublicKey] = {}
        for entry in document["keys"]:
            if not isinstance(entry, dict):
                raise ValueError("Invalid JWK")
            kid = entry.get("kid")
            if not isinstance(kid, str) or not _KID.fullmatch(kid) or kid in keys:
                raise ValueError("Invalid JWK identifier")
            if entry.get("kty") != "RSA" or entry.get("use") != "sig" or entry.get("alg") != "RS256":
                raise ValueError("Unsupported JWK")
            if "key_ops" in entry and entry["key_ops"] != ["verify"]:
                raise ValueError("Unsupported JWK operations")
            if any(name in entry for name in ("d", "p", "q", "dp", "dq", "qi", "oth", "jku", "x5u")):
                raise ValueError("Unsupported JWK material")
            for name, max_size in (("n", 1366), ("e", 8)):
                if (not isinstance(entry.get(name), str) or len(entry[name]) > max_size
                        or not _JWT_PART.fullmatch(entry[name])):
                    raise ValueError("Unsupported RSA public numbers")
            key = jwt.algorithms.RSAAlgorithm.from_jwk(entry)
            if not isinstance(key, RSAPublicKey) or not 2048 <= key.key_size <= 8192:
                raise ValueError("Unsupported RSA key size")
            keys[kid] = key
        return keys
