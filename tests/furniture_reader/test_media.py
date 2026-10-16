"""No external network, credentials, application imports or production databases."""

import io
import socket
import threading
from email.message import Message

import pytest

from furniture_reader import media


HOSTS = frozenset({"media.example.com"})
URL = "https://media.example.com/invoice?signature=do-not-log"
PNG = b"\x89PNG\r\n\x1a\nfictional-test-image"
PUBLIC_V4 = (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 443))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("media unit tests must not use the network")
    monkeypatch.setattr(socket, "socket", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)


class FakeResponse:
    def __init__(self, data=PNG, headers=None, status=200):
        self.status = status
        self.headers = Message()
        for key, value in headers or [("Content-Type", "image/png")]:
            self.headers[key] = value
        self.body = io.BytesIO(data)
        self.closed = False

    def read1(self, size):
        return self.body.read(size)

    def close(self):
        self.closed = True


@pytest.fixture
def transport(monkeypatch):
    state = {"response": FakeResponse(), "requests": [], "closed": False}

    class Connection:
        def __init__(self, host, addresses, deadline):
            state["host"] = host
            state["addresses"] = addresses

        def request(self, method, target, headers):
            state["requests"].append((method, target, headers))

        def getresponse(self):
            return state["response"]

        def close(self):
            state["closed"] = True

    monkeypatch.setattr(media, "_PinnedHTTPSConnection", Connection)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args: [PUBLIC_V4])
    return state


@pytest.mark.parametrize("url", [
    "http://media.example.com/file", "https://media.example.com:444/file",
    "https://user:pass@media.example.com/file", "https://media.example.com:/file",
    "https://media.example.com.evil.test/file", "https://sub.media.example.com/file",
    "https://media.example.com./file", "https://media.example.com/file#fragment",
    " https://media.example.com/file", "https://media.example.com/file\r\nX: secret",
    "https://media.example.com\\@evil.test/file", "https://média.example.com/file",
    "https://[::1]/file", "https://93.184.216.34/file",
])
def test_url_policy_rejects_before_network(url):
    with pytest.raises(media.MediaDownloadError):
        media.download_media(url, HOSTS, 1024)


def test_empty_allowlist_is_closed():
    with pytest.raises(media.MediaDownloadError, match="^host_not_allowed$"):
        media.download_media(URL, frozenset(), 1024)


@pytest.mark.parametrize("ip", [
    "127.0.0.1", "10.0.0.1", "172.16.0.1", "192.168.1.1", "169.254.169.254",
    "0.0.0.0", "100.64.0.1", "224.0.0.1", "255.255.255.255", "192.0.0.8",
    "192.88.99.1", "198.18.0.1", "192.0.2.1", "::1", "::", "fe80::1", "fc00::1",
    "ff02::1", "fec0::1", "3fff::1", "2606:4700:4700::1111%eth0",
    "2001:db8::1", "::ffff:127.0.0.1", "::ffff:93.184.216.34",
    "64:ff9b::7f00:1", "64:ff9b:1::1", "2002:7f00:1::1", "2001::1",
])
def test_special_ip_addresses_are_rejected(ip):
    assert not media._public_address(ip)


@pytest.mark.parametrize("ip", ["93.184.216.34", "2606:4700:4700::1111"])
def test_public_ip_addresses_are_accepted(ip):
    assert media._public_address(ip)


def test_dns_mixed_answers_are_rejected(monkeypatch):
    private = (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 443))
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args: [PUBLIC_V4, private])
    with pytest.raises(media.MediaDownloadError, match="^address_not_allowed$"):
        media.download_media(URL, HOSTS, 1024)


def test_dns_failure_is_sanitized(monkeypatch):
    def fail(*args):
        raise OSError("sensitive host and signed URL")
    monkeypatch.setattr(socket, "getaddrinfo", fail)
    with pytest.raises(media.MediaDownloadError, match="^dns_failed$") as caught:
        media.download_media(URL, HOSTS, 1024)
    assert "sensitive" not in str(caught.value)
    assert caught.value.__cause__ is None


def test_dns_wait_is_bounded_and_stalled_resolvers_are_capped(monkeypatch):
    release = threading.Event()
    finished = threading.Event()
    monkeypatch.setattr(media, "_DNS_SLOTS", threading.BoundedSemaphore(1))

    def stalled_dns(*args):
        try:
            release.wait(timeout=1)
            return [PUBLIC_V4]
        finally:
            finished.set()

    monkeypatch.setattr(socket, "getaddrinfo", stalled_dns)
    try:
        with pytest.raises(media.MediaDownloadError, match="^timeout$"):
            media._resolve_addresses("media.example.com", media.time.monotonic() + 0.01)
        with pytest.raises(media.MediaDownloadError, match="^dns_busy$"):
            media._resolve_addresses("media.example.com", media.time.monotonic() + 0.01)
    finally:
        release.set()
        assert finished.wait(timeout=1)


def test_no_proxy_credentials_or_cookie_headers_are_sent(transport, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9999")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9999")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:9999")
    data, mime = media.download_media(URL, HOSTS, 1024)
    assert (data, mime) == (PNG, "image/png")
    method, target, headers = transport["requests"][0]
    assert method == "GET"
    assert target == "/invoice?signature=do-not-log"
    assert headers["Host"] == "media.example.com"
    assert headers["Accept-Encoding"] == "identity"
    assert not {"Authorization", "Cookie", "Proxy-Authorization"} & headers.keys()
    assert transport["closed"] and transport["response"].closed


def test_validated_ip_is_pinned_while_tls_verifies_original_hostname(monkeypatch):
    calls = []

    class RawSocket:
        def settimeout(self, seconds):
            assert seconds > 0

        def connect(self, address):
            calls.append(("connect", address))

        def close(self):
            calls.append(("close",))

    class TLSContext:
        def wrap_socket(self, raw, server_hostname):
            calls.append(("tls", server_hostname))
            return raw

    monkeypatch.setattr(socket, "socket", lambda *args: RawSocket())
    context = TLSContext()
    context.verify_mode = media.ssl.CERT_REQUIRED
    context.check_hostname = True
    context.post_handshake_auth = True
    monkeypatch.setattr(media.ssl, "create_default_context", lambda: context)
    address = PUBLIC_V4
    connection = media._PinnedHTTPSConnection("media.example.com", [
        (address[0], address[1], address[2], address[4])
    ], media.time.monotonic() + 10)
    connection.connect()
    connection.close()
    assert calls == [("connect", ("93.184.216.34", 443)), ("tls", "media.example.com"), ("close",)]


def test_default_tls_context_requires_certificates_and_hostname():
    connection = media._PinnedHTTPSConnection("media.example.com", [], media.time.monotonic() + 10)
    assert connection._context.verify_mode == media.ssl.CERT_REQUIRED
    assert connection._context.check_hostname is True
    connection.close()


@pytest.mark.parametrize("transfer_encoding", [None, "chunked", "chunked ", "chunked\t"])
def test_real_http_parser_reads_connection_close_response_from_mock_socket(monkeypatch, transfer_encoding):
    """Exercise HTTPResponse file ownership as well as the pinned transport."""
    class WireSocket:
        def __init__(self):
            self.closed = False
            self.sent = []
            prefix = b"HTTP/1.1 200 OK\r\nContent-Type: image/png\r\nConnection: close\r\n"
            if transfer_encoding is None:
                wire = prefix + b"Content-Length: " + str(len(PNG)).encode() + b"\r\n\r\n" + PNG
            else:
                wire = (prefix + b"Transfer-Encoding: " + transfer_encoding.encode() + b"\r\n\r\n"
                        + format(len(PNG), "x").encode() + b"\r\n" + PNG + b"\r\n0\r\n\r\n")
            self.body = io.BytesIO(wire)

        def settimeout(self, seconds):
            assert seconds > 0

        def connect(self, address):
            assert address == ("93.184.216.34", 443)

        def sendall(self, data):
            self.sent.append(data)

        def recv_into(self, buffer):
            assert not self.closed, "response must retain its file ownership"
            return self.body.readinto(buffer)

        def close(self):
            self.closed = True

    class TLSContext:
        verify_mode = media.ssl.CERT_REQUIRED
        check_hostname = True
        post_handshake_auth = True

        def wrap_socket(self, raw, server_hostname):
            assert server_hostname == "media.example.com"
            return raw

    wire_socket = WireSocket()
    monkeypatch.setattr(socket, "socket", lambda *args: wire_socket)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args: [PUBLIC_V4])
    monkeypatch.setattr(media.ssl, "create_default_context", TLSContext)
    if transfer_encoding in ("chunked ", "chunked\t"):
        with pytest.raises(media.MediaDownloadError, match="^invalid_headers$"):
            media.download_media(URL, HOSTS, 1024)
    else:
        assert media.download_media(URL, HOSTS, 1024) == (PNG, "image/png")
    assert wire_socket.closed
    assert b"Host: media.example.com\r\n" in b"".join(wire_socket.sent)


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_redirects_are_never_followed(transport, status):
    transport["response"] = FakeResponse(status=status, headers=[("Location", "http://169.254.169.254/")])
    with pytest.raises(media.MediaDownloadError, match="^redirect_not_allowed$"):
        media.download_media(URL, HOSTS, 1024)
    assert len(transport["requests"]) == 1
    assert transport["closed"]


@pytest.mark.parametrize("headers,error", [
    ([("Content-Type", "image/png"), ("Content-Length", "1025")], "too_large"),
    ([("Content-Type", "image/png"), ("Content-Length", "-1")], "invalid_headers"),
    ([("Content-Type", "image/png"), ("Content-Length", "1, 2")], "invalid_headers"),
    ([("Content-Type", "image/png"), ("Content-Length", "1"), ("Content-Length", "1")], "invalid_headers"),
    ([("Content-Type", "image/png"), ("Content-Encoding", "gzip")], "encoding_not_allowed"),
    ([("Content-Type", "image/png"), ("Transfer-Encoding", "gzip")], "invalid_headers"),
    ([("Content-Type", "image/png"), ("Transfer-Encoding", "chunked ")], "invalid_headers"),
    ([("Content-Type", "image/png"), ("Transfer-Encoding", "chunked\t")], "invalid_headers"),
    ([("Content-Type", "image/png"), ("Transfer-Encoding", "chunked"), ("Content-Length", "2")], "invalid_headers"),
    ([("Content-Type", "text/html")], "type_not_allowed"),
    ([("Content-Type", "image/svg+xml")], "type_not_allowed"),
    ([("Content-Type", "application/msword")], "type_not_allowed"),
    ([("Content-Type", "audio/ogg")], "type_not_allowed"),
    ([("Content-Type", "application/octet-stream")], "type_not_allowed"),
])
def test_rejected_headers_are_not_read(transport, headers, error):
    transport["response"] = FakeResponse(headers=headers)
    with pytest.raises(media.MediaDownloadError, match="^" + error + "$"):
        media.download_media(URL, HOSTS, 1024)
    assert transport["response"].body.tell() == 0


@pytest.mark.parametrize("headers", [
    [("Content-Type", "image/png")],
    [("Content-Type", "image/png"), ("Transfer-Encoding", "chunked")],
])
def test_actual_stream_size_is_bounded_without_content_length(transport, headers):
    transport["response"] = FakeResponse(data=PNG + b"x" * 100, headers=headers)
    with pytest.raises(media.MediaDownloadError, match="^too_large$"):
        media.download_media(URL, HOSTS, 40)
    assert transport["response"].body.tell() == 41


def test_truncated_response_is_rejected(transport):
    transport["response"] = FakeResponse(headers=[("Content-Type", "image/png"), ("Content-Length", "100")])
    with pytest.raises(media.MediaDownloadError, match="^incomplete_download$"):
        media.download_media(URL, HOSTS, 1024)


@pytest.mark.parametrize("data,mime", [
    (PNG, "image/png"), (b"\xff\xd8\xfftest", "image/jpeg"),
    (b"RIFFxxxxWEBPtest", "image/webp"), (b"%PDF-1.7\nfixture", "application/pdf"),
])
def test_verified_mime_is_returned(transport, data, mime):
    transport["response"] = FakeResponse(data, [("Content-Type", mime + "; charset=binary")])
    assert media.download_media(URL, HOSTS, 1024) == (data, mime)


@pytest.mark.parametrize("data,mime,error", [
    (b"<html>fake image</html>", "image/png", "invalid_signature"),
    (b"<svg>fake image</svg>", "image/png", "invalid_signature"),
    (PNG, "image/jpeg", "type_mismatch"),
    (b"not a PDF", "application/pdf", "invalid_signature"),
    (b"", "image/png", "invalid_signature"),
])
def test_declared_type_is_verified_against_magic(transport, data, mime, error):
    transport["response"] = FakeResponse(data, [("Content-Type", mime)])
    with pytest.raises(media.MediaDownloadError, match="^" + error + "$"):
        media.download_media(URL, HOSTS, 1024)


def test_slow_drip_headers_cannot_reset_total_deadline(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(media.time, "monotonic", lambda: clock[0])

    class SlowSocket:
        def settimeout(self, seconds):
            assert seconds <= 10

        def recv_into(self, buffer):
            clock[0] += 1
            buffer[0] = ord("H")
            return 1

    response = media.http.client.HTTPResponse(media._DeadlineSocket(SlowSocket(), 10))
    with pytest.raises(media.MediaDownloadError, match="^timeout$"):
        response.begin()
    assert clock[0] == 10
    response.close()


def test_stream_total_deadline(transport, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(media.time, "monotonic", lambda: clock[0])

    def slow_read(size):
        clock[0] += 4
        return b"x"

    transport["response"].read1 = slow_read
    with pytest.raises(media.MediaDownloadError, match="^timeout$"):
        media.download_media(URL, HOSTS, 1024, timeout_seconds=10)
    assert transport["closed"]


def test_network_failure_never_exposes_signed_url(transport):
    def failed_read(size):
        raise OSError("failed to fetch " + URL)

    transport["response"].read1 = failed_read
    with pytest.raises(media.MediaDownloadError, match="^download_failed$") as caught:
        media.download_media(URL, HOSTS, 1024)
    assert URL not in str(caught.value)
    assert caught.value.__suppress_context__
    assert transport["closed"] and transport["response"].closed


@pytest.mark.parametrize("max_bytes,timeout", [(0, 10), (-1, 10), (True, 10), (1024, 0),
                                             (1024, float("inf")), (1024, float("nan"))])
def test_invalid_limits_fail_before_network(max_bytes, timeout):
    with pytest.raises(media.MediaDownloadError, match="^invalid_limits$"):
        media.download_media(URL, HOSTS, max_bytes, timeout)
