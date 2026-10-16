"""Explicit archive configuration; never reads financial credentials or dotenv."""
from dataclasses import dataclass, field
import ipaddress
import os
from pathlib import Path
import re
from typing import Mapping
from urllib.parse import urlsplit

from furniture_reader.config import ReaderSettings, chat_ids

MESSAGE_SCOPE = "furniture.messages:read"
ATTACHMENT_SCOPE = "furniture.attachments:read"
SCOPES = (MESSAGE_SCOPE, ATTACHMENT_SCOPE)
APP_ROOT = Path(__file__).resolve().parents[1]


class ConfigurationError(ValueError):
    """Deliberately does not include supplied settings in an error message."""


def _https_url(value: str, path: str) -> bool:
    try:
        parsed = urlsplit(value)
        try:
            ipaddress.ip_address(parsed.hostname or "")
        except ValueError:
            pass
        else:
            return False
        return (parsed.scheme == "https" and parsed.path == path
                and parsed.port in (None, 443) and parsed.username is None
                and parsed.password is None and not parsed.query and not parsed.fragment
                and bool(parsed.hostname) and parsed.hostname == parsed.netloc
                and bool(re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", parsed.hostname))
                and "." in parsed.hostname and not parsed.hostname.endswith(".local")
                and value == f"https://{parsed.hostname}{path}")
    except ValueError:
        return False


def _opaque(value: str, maximum: int = 256) -> bool:
    return (isinstance(value, str) and 1 <= len(value) <= maximum
            and all(32 < ord(c) < 127 for c in value) and value != "*")


@dataclass(frozen=True)
class MCPSettings:
    enabled: bool = False
    issuer: str = ""
    resource: str = ""
    owner_sub: str = ""
    client_id: str = ""
    storage_dir: str = ""
    instance_id: str = ""
    allowed_chat_ids: frozenset[str] = frozenset()
    read_chat_ids: frozenset[str] = frozenset()
    owner_chat_ids: frozenset[str] = frozenset()
    min_issued_at: int = 0
    archive_backend: str = "sqlite"
    archive_url: str = ""
    archive_token: str = field(default="", repr=False)

    @property
    def chat_ids(self) -> frozenset[str]:
        return self.allowed_chat_ids & self.read_chat_ids & self.owner_chat_ids

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None):
        env = os.environ if env is None else env
        # A disabled process never validates paths, contacts Auth0 or opens files.
        if env.get("FURNITURE_MCP_ENABLED", "").lower() != "true":
            return cls()
        try:
            settings = cls(
                enabled=True,
                issuer=env.get("FURNITURE_MCP_AUTH0_ISSUER", ""),
                resource=env.get("FURNITURE_MCP_RESOURCE_URL", ""),
                owner_sub=env.get("FURNITURE_MCP_OWNER_SUB", ""),
                client_id=env.get("FURNITURE_MCP_AUTH0_CLIENT_ID", ""),
                storage_dir=env.get("FURNITURE_STORAGE_DIR", ""),
                instance_id=env.get("FURNITURE_INSTANCE_ID", ""),
                allowed_chat_ids=chat_ids(env.get("FURNITURE_CHAT_IDS", "")),
                read_chat_ids=chat_ids(env.get("FURNITURE_READ_CHAT_IDS", "")),
                owner_chat_ids=chat_ids(env.get("FURNITURE_MCP_CHAT_IDS", "")),
                min_issued_at=int(env.get("FURNITURE_MCP_MIN_ISSUED_AT", "0")),
                archive_backend=env.get("FURNITURE_MCP_ARCHIVE_BACKEND", "sqlite"),
                archive_url=env.get("FURNITURE_MCP_ARCHIVE_URL", ""),
                archive_token=env.get("FURNITURE_MCP_ARCHIVE_TOKEN", ""),
            )
            settings.validate()
            return settings
        except (ValueError, TypeError, OSError):
            raise ConfigurationError("Furniture MCP configuration is incomplete or invalid") from None

    def validate(self):
        if not self.enabled:
            return
        valid = (_https_url(self.issuer, "/") and _https_url(self.resource, "/mcp")
                 and _opaque(self.owner_sub) and _opaque(self.client_id)
                 and _opaque(self.instance_id) and type(self.min_issued_at) is int
                 and 0 <= self.min_issued_at <= 253402300799)
        if self.archive_backend == "sqlite":
            valid = valid and ReaderSettings(storage_dir=self.storage_dir).valid_storage(str(APP_ROOT))
        elif self.archive_backend == "http":
            valid = (valid and _https_url(self.archive_url, "/api/furniture-reader/mcp")
                     and len(self.archive_url) <= 2048
                     and _opaque(self.archive_token, 512) and len(self.archive_token) >= 32)
            from furniture_reader.http_contract import encode_scope
            try:
                encode_scope(self.instance_id, self.chat_ids)
            except ValueError:
                valid = False
        else:
            valid = False
        for scope in (self.allowed_chat_ids, self.read_chat_ids, self.owner_chat_ids):
            valid = valid and 1 <= len(scope) <= 100 and all(_opaque(c) for c in scope)
        # Prevent a typo or widened owner list from silently changing the policy.
        valid = valid and bool(self.chat_ids) and self.owner_chat_ids <= self.read_chat_ids
        valid = valid and self.read_chat_ids <= self.allowed_chat_ids
        if not valid:
            raise ConfigurationError("Furniture MCP configuration is incomplete or invalid")
