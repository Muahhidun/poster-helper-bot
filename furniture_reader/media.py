"""Bounded HTTPS downloads for the private furniture archive, without API credentials.

Only explicitly trusted DNS hostnames and publicly routable addresses are accepted.
The validated address is used directly for the connection, while TLS verifies the
original hostname. Proxy environment variables are never consulted. Files are not
executed, decoded or rendered. Magic-byte checks identify formats, not safety:
in particular a PDF signature is NOT a malware scan or proof of a harmless PDF.
"""

import http.client
import io
import ipaddress
import math
import queue
import re
import socket
import ssl
import threading
import time
from urllib.parse import urlsplit


_MIME_TYPES = frozenset({"image/jpeg", "image/png", "image/webp", "application/pdf"})
_DNS_SLOTS = threading.BoundedSemaphore(4)
_HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
# Reject translation/tunnel and special-purpose ranges independently of the
# Python version's ipaddress registry (including NAT64 encodings of private IPs).
_SPECIAL_NETWORKS = tuple(ipaddress.ip_network(value) for value in (
    "192.0.0.0/24", "192.88.99.0/24", "64:ff9b::/96", "64:ff9b:1::/48",
    "2001::/23", "2002::/16", "3fff::/20",
))


class MediaDownloadError(Exception):
    """A short operational code; never attach the source URL or network exception."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise MediaDownloadError("timeout")
    return remaining


def _parse_url(url: str, allowed_hosts: frozenset[str]) -> tuple[str, str]:
    if (not isinstance(url, str) or len(url) > 8192
            or any(ord(char) <= 32 or ord(char) >= 127 or char == "\\" for char in url)):
        raise MediaDownloadError("invalid_url")
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        port = parsed.port
    except ValueError:
        raise MediaDownloadError("invalid_url") from None
    if (parsed.scheme != "https" or parsed.username is not None or parsed.password is not None
            or port not in (None, 443) or parsed.fragment
            or parsed.netloc.lower() not in (host, host + ":443")):
        raise MediaDownloadError("invalid_url")
    if (not host or len(host) > 253 or "." not in host
            or not all(_HOST_LABEL.fullmatch(label) for label in host.split("."))):
        raise MediaDownloadError("invalid_host")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise MediaDownloadError("invalid_host")
    if host not in allowed_hosts:
        raise MediaDownloadError("host_not_allowed")
    target = parsed.path or "/"
    if parsed.query:
        target += "?" + parsed.query
    return host, target


def _public_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if (not address.is_global or address.is_reserved or address.is_multicast
            or address.is_loopback or address.is_link_local or address.is_unspecified):
        return False
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None or address.is_site_local or address.scope_id is not None:
            return False
    return not any(address in network for network in _SPECIAL_NETWORKS
                   if address.version == network.version)


def _resolve_addresses(host: str, deadline: float) -> list[tuple]:
    """Bound caller wait and resolver concurrency even if OS DNS resolution stalls."""
    slots = _DNS_SLOTS
    if not slots.acquire(blocking=False):
        raise MediaDownloadError("dns_busy")
    result = queue.Queue(maxsize=1)

    def resolve():
        try:
            addresses = socket.getaddrinfo(host, 443, socket.AF_UNSPEC,
                                           socket.SOCK_STREAM, socket.IPPROTO_TCP)
            result.put(addresses)
        except Exception:
            result.put(None)
        finally:
            slots.release()

    try:
        threading.Thread(target=resolve, daemon=True, name="furniture-media-dns").start()
    except RuntimeError:
        slots.release()
        raise MediaDownloadError("dns_failed") from None
    try:
        addresses = result.get(timeout=_remaining(deadline))
    except queue.Empty:
        raise MediaDownloadError("timeout") from None
    _remaining(deadline)
    if not addresses:
        raise MediaDownloadError("dns_failed")
    validated = []
    for family, socktype, proto, _canonname, sockaddr in addresses:
        if (family not in (socket.AF_INET, socket.AF_INET6)
                or socktype != socket.SOCK_STREAM or proto != socket.IPPROTO_TCP
                or sockaddr[1] != 443 or not _public_address(sockaddr[0])
                or (family == socket.AF_INET6 and sockaddr[3] != 0)):
            # Reject mixed public/private results instead of quietly selecting
            # the public entry from a suspicious DNS answer.
            raise MediaDownloadError("address_not_allowed")
        validated.append((family, socktype, proto, sockaddr))
    return validated


class _DeadlineReader(io.RawIOBase):
    def __init__(self, connection):
        super().__init__()
        self.connection = connection

    def readable(self):
        return True

    def readinto(self, buffer):
        tls_socket = self.connection.socket
        tls_socket.settimeout(_remaining(self.connection.deadline))
        count = tls_socket.recv_into(buffer)
        _remaining(self.connection.deadline)
        return count

    def close(self):
        if not self.closed:
            try:
                super().close()
            finally:
                self.connection.release_reader()


class _DeadlineSocket:
    """Refresh the absolute deadline for every recv, including slow HTTP headers."""

    def __init__(self, tls_socket, deadline):
        self.socket = tls_socket
        self.deadline = deadline
        self.readers = 0
        self.close_requested = False

    def sendall(self, data):
        self.socket.settimeout(_remaining(self.deadline))
        self.socket.sendall(data)
        _remaining(self.deadline)

    def makefile(self, mode):
        if mode != "rb":
            raise ValueError("unsupported socket file mode")
        self.readers += 1
        return io.BufferedReader(_DeadlineReader(self))

    def close(self):
        # HTTPConnection closes its ownership immediately on Connection: close.
        # Like socket.makefile(), keep the transport alive until the response's
        # file handle closes, otherwise reading that response would use a dead fd.
        self.close_requested = True
        if self.readers == 0:
            self.socket.close()

    def release_reader(self):
        self.readers -= 1
        if self.close_requested and self.readers == 0:
            self.socket.close()


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, addresses, deadline):
        super().__init__(host, port=443, timeout=_remaining(deadline),
                         context=ssl.create_default_context())
        self.addresses = addresses
        self.deadline = deadline

    def connect(self):
        # No create_connection(host), redirects, HTTP proxies or second DNS lookup.
        for family, socktype, proto, sockaddr in self.addresses:
            raw_socket = socket.socket(family, socktype, proto)
            try:
                raw_socket.settimeout(_remaining(self.deadline))
                raw_socket.connect(sockaddr)
                raw_socket.settimeout(_remaining(self.deadline))
                tls_socket = self._context.wrap_socket(raw_socket, server_hostname=self.host)
                self.sock = _DeadlineSocket(tls_socket, self.deadline)
                _remaining(self.deadline)
                return
            except (OSError, MediaDownloadError):
                raw_socket.close()
                if self.sock is not None:
                    self.sock.close()
                    self.sock = None
                _remaining(self.deadline)
        raise MediaDownloadError("connection_failed")


def _one_header(response, name):
    values = response.headers.get_all(name, [])
    if len(values) > 1:
        raise MediaDownloadError("invalid_headers")
    return values[0].strip() if values else None


def _response_metadata(response, max_bytes):
    if 300 <= response.status < 400:
        raise MediaDownloadError("redirect_not_allowed")
    if response.status != 200:
        raise MediaDownloadError("http_status")
    encoding = _one_header(response, "Content-Encoding")
    if encoding is not None and encoding.lower() != "identity":
        raise MediaDownloadError("encoding_not_allowed")
    content_type = (_one_header(response, "Content-Type") or "").split(";", 1)[0].strip().lower()
    if content_type not in _MIME_TYPES:
        raise MediaDownloadError("type_not_allowed")
    transfer_encoding = _one_header(response, "Transfer-Encoding")
    length = _one_header(response, "Content-Length")
    if transfer_encoding is not None:
        # Match http.client's exact framing decision: it does not strip trailing
        # whitespace before recognizing chunked encoding. Do not accept a header
        # our policy normalizes differently from the actual body parser.
        if response.headers["Transfer-Encoding"].lower() != "chunked" or length is not None:
            raise MediaDownloadError("invalid_headers")
    if length is not None:
        if len(length) > 20 or not re.fullmatch(r"[0-9]+", length):
            raise MediaDownloadError("invalid_headers")
        length = int(length)
        if length > max_bytes:
            raise MediaDownloadError("too_large")
    return content_type, length


def _verified_mime(data: bytes, declared: str) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        detected = "image/jpeg"
    elif data.startswith(b"\x89PNG\r\n\x1a\n"):
        detected = "image/png"
    elif len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        detected = "image/webp"
    elif re.match(rb"%PDF-[12]\.[0-9]", data):
        detected = "application/pdf"
    else:
        raise MediaDownloadError("invalid_signature")
    if detected != declared:
        raise MediaDownloadError("type_mismatch")
    return detected


def download_media(url: str, allowed_hosts: frozenset[str], max_bytes: int,
                   timeout_seconds: float = 10.0) -> tuple[bytes, str]:
    """Fetch bounded bytes plus canonical MIME, or a URL-free MediaDownloadError.

    Only JPEG, PNG, WebP and PDF with matching declared MIME/signature are accepted.
    Signature checks are not full format validation or malware scanning (including
    PDF active content). Callers must store bytes privately and serve as downloads
    behind authorization; this function never persists or exposes a public URL.
    """
    if (not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes <= 0
            or not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool)
            or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
        raise MediaDownloadError("invalid_limits")
    host, target = _parse_url(url, allowed_hosts)
    deadline = time.monotonic() + timeout_seconds
    connection = None
    response = None
    try:
        addresses = _resolve_addresses(host, deadline)
        connection = _PinnedHTTPSConnection(host, addresses, deadline)
        connection.request("GET", target, headers={
            "Host": host, "Accept-Encoding": "identity", "Connection": "close",
            "Accept": ", ".join(sorted(_MIME_TYPES)),
        })
        response = connection.getresponse()
        content_type, length = _response_metadata(response, max_bytes)
        body = bytearray()
        while True:
            _remaining(deadline)
            chunk = response.read1(min(65536, max_bytes - len(body) + 1))
            _remaining(deadline)
            if not chunk:
                break
            body.extend(chunk)
            if len(body) > max_bytes:
                raise MediaDownloadError("too_large")
        if length is not None and len(body) != length:
            raise MediaDownloadError("incomplete_download")
        data = bytes(body)
        return data, _verified_mime(data, content_type)
    except MediaDownloadError:
        raise
    except (TimeoutError, socket.timeout):
        raise MediaDownloadError("timeout") from None
    except (OSError, http.client.HTTPException, ValueError):
        raise MediaDownloadError("download_failed") from None
    finally:
        for resource in (response, connection):
            if resource is not None:
                try:
                    resource.close()
                except OSError:
                    # Cleanup must not replace the safe failure code with a
                    # low-level exception containing transport details.
                    pass
