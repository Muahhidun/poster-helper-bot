"""Explicit, fail-closed configuration; never loads dotenv or accounting credentials."""
from dataclasses import dataclass
import hashlib
import hmac
import os
from pathlib import Path
import re
from typing import Mapping


def chat_ids(value: str) -> frozenset[str]:
    return frozenset(item.strip() for item in value.split(',') if item.strip())


def valid_hash(value: str) -> bool:
    return bool(re.fullmatch(r'[a-f0-9]{64}', value))


def authorized(header: str, expected_hash: str) -> bool:
    if not valid_hash(expected_hash) or not header.startswith('Bearer '):
        return False
    token = header[7:]
    if not 32 <= len(token) <= 512 or any(char.isspace() for char in token):
        return False
    return hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), expected_hash)


@dataclass(frozen=True)
class ReaderSettings:
    enabled: bool = False
    allowed_chat_ids: frozenset[str] = frozenset()
    read_chat_ids: frozenset[str] = frozenset()
    read_token_sha256: str = ''
    webhook_token_sha256: str = ''
    instance_id: str = ''
    storage_dir: str = ''
    media_enabled: bool = False
    media_hosts: frozenset[str] = frozenset()
    media_max_bytes: int = 10 * 1024 * 1024

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None):
        env = os.environ if env is None else env
        try:
            media_max_bytes = int(env.get('FURNITURE_MEDIA_MAX_BYTES', str(10 * 1024 * 1024)))
        except ValueError:
            media_max_bytes = 0
        return cls(
            enabled=env.get('FURNITURE_READER_ENABLED', '').lower() == 'true',
            allowed_chat_ids=chat_ids(env.get('FURNITURE_CHAT_IDS', '')),
            read_chat_ids=chat_ids(env.get('FURNITURE_READ_CHAT_IDS', '')),
            read_token_sha256=env.get('FURNITURE_READ_TOKEN_SHA256', '').strip(),
            webhook_token_sha256=env.get('FURNITURE_WEBHOOK_TOKEN_SHA256', '').strip(),
            instance_id=env.get('FURNITURE_INSTANCE_ID', '').strip(),
            storage_dir=env.get('FURNITURE_STORAGE_DIR', '').strip(),
            media_enabled=env.get('FURNITURE_MEDIA_ENABLED', '').lower() == 'true',
            media_hosts=chat_ids(env.get('FURNITURE_MEDIA_HOSTS', '').lower()),
            media_max_bytes=media_max_bytes,
        )

    def valid_storage(self, app_root: str) -> bool:
        root = Path(self.storage_dir)
        if not self.storage_dir or not root.is_absolute() or root == Path('/'):
            return False
        if any(part.is_symlink() for part in (root, *root.parents)):
            return False
        # Keep the archive outside the application tree and all its public files.
        resolved = root.resolve()
        return not resolved.is_relative_to(Path(app_root).resolve())

    def valid_credentials(self) -> bool:
        return (valid_hash(self.read_token_sha256)
                and valid_hash(self.webhook_token_sha256)
                and self.read_token_sha256 != self.webhook_token_sha256)
