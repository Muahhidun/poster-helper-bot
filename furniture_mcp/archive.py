"""Bounded read-only views of a single instance's private furniture archive.

The caller must supply server-derived effective chat permissions. This module
never accepts database paths, arbitrary SQL, URLs, or chat permissions from an
MCP tool argument, and never imports the accounting or ingestion services.
"""

from contextlib import contextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time

from furniture_reader.store import ArchiveStore


# Payload budgets reserve room for MCP's compatibility representation: the same
# result appears in structuredContent and in a JSON text block. JSON escaping
# can increase that text block again. A conservative 3x payload plus envelope
# stays below the 64 KiB search / 128 KiB single-message wire limits.
SEARCH_MAX_BYTES = 20 * 1024
MESSAGE_MAX_BYTES = 40 * 1024
ATTACHMENT_MAX_BYTES = 2 * 1024 * 1024
QUERY_SECONDS = 1.0
QUERY_MAX_STEPS = 2_000_000
_PROGRESS_INTERVAL = 1000
_SNIPPET_CHARS = 1000
_MAX_ROW_ID = (1 << 63) - 1
_ATTACHMENT_FIELDS = (
    "id", "filename", "declared_mime_type", "state", "mime_type", "size",
    "sha256", "error_code", "attempts",
)
_MESSAGE_FIELDS = (
    "id", "instance_id", "chat_id", "message_id", "direction", "sent_at",
    "received_at", "message_type", "text",
)
_MEDIA_TYPES = frozenset({"image/jpeg", "image/png", "image/webp", "application/pdf"})


def _invalid():
    raise ValueError("Invalid archive request")


def _string(value, maximum, *, minimum=0):
    if (not isinstance(value, str) or not minimum <= len(value) <= maximum
            or "\x00" in value):
        _invalid()
    return value


def _integer(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        _invalid()
    return value


def _json_size(value):
    # ASCII escaping is a conservative budget even when the transport chooses
    # UTF-8 JSON. Count the actual envelope, not only character lengths.
    return len(json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("utf-8"))


def _fit_message(item, budget):
    if _json_size(item) <= budget:
        return item
    text = item["text"]
    item = dict(item, text="", text_truncated=True)
    if _json_size(item) > budget:
        raise RuntimeError("Archive unavailable")
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        item["text"] = text[:middle]
        if _json_size(item) <= budget:
            low = middle
        else:
            high = middle - 1
    item["text"] = text[:low]
    return item


class ArchiveReader:
    """Read-only instance and chat boundary; construction performs no I/O.

    A maximum of 256 server-configured chat grants bounds SQL placeholders.
    Query work is capped by both time and SQLite VM steps; exceeding either
    fails the request rather than returning misleading incomplete results.
    """

    def __init__(self, root: Path, instance_id: str, chat_ids: frozenset[str]):
        self.instance_id = _string(instance_id, 256, minimum=1)
        if not isinstance(chat_ids, frozenset) or len(chat_ids) > 256:
            _invalid()
        self.chat_ids = tuple(sorted(_string(chat_id, 256, minimum=1) for chat_id in chat_ids))
        self._store = ArchiveStore(root)

    @contextmanager
    def _connection(self):
        deadline = time.monotonic() + QUERY_SECONDS
        steps = 0

        def expired():
            return time.monotonic() >= deadline or steps >= QUERY_MAX_STEPS

        def progress():
            nonlocal steps
            steps += _PROGRESS_INTERVAL
            return int(expired())

        def contains(text, needle):
            if expired():
                raise sqlite3.OperationalError("query budget exceeded")
            return int(needle in text.casefold())

        try:
            with self._store._connection(timeout=0.25) as connection:
                if connection is None:
                    yield None
                    return
                if expired():
                    raise RuntimeError("Archive unavailable")
                connection.execute("PRAGMA busy_timeout = 250")
                connection.set_progress_handler(progress, _PROGRESS_INTERVAL)
                connection.create_function("archive_contains", 2, contains, deterministic=True)
                connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1_000_000)
                try:
                    yield connection
                    if expired():
                        raise RuntimeError("Archive unavailable")
                finally:
                    connection.set_progress_handler(None, 0)
        except (sqlite3.Error, OSError, ValueError):
            raise RuntimeError("Archive unavailable") from None

    def _scope(self, chat_id=None):
        chats = self.chat_ids if chat_id is None else (chat_id,)
        slots = ",".join("?" for _ in chats)
        return f"m.instance_id = ? AND m.chat_id IN ({slots})", (self.instance_id, *chats)

    @staticmethod
    def _columns(text_length):
        columns = [f"m.{key}" for key in _MESSAGE_FIELDS if key != "text"]
        columns.extend((f"substr(m.text,1,{text_length}) AS text", "length(m.text) AS text_length"))
        columns.extend(f"a.{key} AS attachment_{key}" for key in _ATTACHMENT_FIELDS)
        return ",".join(columns)

    @staticmethod
    def _message(row, text_limit):
        item = {key: row[key] for key in _MESSAGE_FIELDS}
        item["text"] = item["text"][:text_limit]
        item["text_length"] = row["text_length"]
        item["text_truncated"] = row["text_length"] > len(item["text"])
        item["attachment"] = ({key: row[f"attachment_{key}"] for key in _ATTACHMENT_FIELDS}
                              if row["attachment_id"] else None)
        return item

    def list_chats(self, after_chat_id: str = "", limit: int = 50) -> dict:
        _string(after_chat_id, 256)
        _integer(limit, 1, 50)
        empty = {"chats": [], "next_after_chat_id": None}
        if not self.chat_ids:
            return empty
        scope, params = self._scope()
        with self._connection() as connection:
            if connection is None:
                return empty
            rows = connection.execute(f"""
                SELECT m.chat_id,COUNT(*) AS message_count,MAX(m.received_at) AS last_received_at
                FROM messages m WHERE {scope} AND m.chat_id > ?
                GROUP BY m.chat_id ORDER BY m.chat_id LIMIT ?
            """, (*params, after_chat_id, limit + 1)).fetchall()
        chats = [dict(row) for row in rows[:limit]]
        return {"chats": chats,
                "next_after_chat_id": chats[-1]["chat_id"] if len(rows) > limit else None}

    def search_messages(self, query: str, chat_id: str | None = None,
                        after_id: int = 0, limit: int = 20) -> dict:
        _string(query, 200, minimum=2)
        if len(query.strip()) < 2:
            _invalid()
        _integer(after_id, 0, _MAX_ROW_ID)
        _integer(limit, 1, 50)
        if chat_id is not None:
            _string(chat_id, 256, minimum=1)
        empty = {"messages": [], "next_after_id": None}
        if not self.chat_ids or (chat_id is not None and chat_id not in self.chat_ids):
            return empty
        scope, params = self._scope(chat_id)
        with self._connection() as connection:
            if connection is None:
                return empty
            rows = connection.execute(f"""
                SELECT {self._columns(_SNIPPET_CHARS)} FROM messages m
                LEFT JOIN attachments a ON a.message_row_id=m.id
                WHERE {scope} AND m.id > ? AND archive_contains(m.text,?)
                ORDER BY m.id LIMIT ?
            """, (*params, after_id, query.casefold(), limit + 1)).fetchall()
        messages = []
        for row in rows[:limit]:
            item = self._message(row, _SNIPPET_CHARS)
            if not messages:
                # Even a maximally long metadata row must make progress.
                item = _fit_message(item, SEARCH_MAX_BYTES - 128)
            candidate = {"messages": [*messages, item], "next_after_id": _MAX_ROW_ID}
            if _json_size(candidate) > SEARCH_MAX_BYTES:
                break
            messages.append(item)
        has_more = len(rows) > len(messages)
        return {"messages": messages,
                "next_after_id": messages[-1]["id"] if has_more and messages else None}

    def read_message(self, message_id: int) -> dict | None:
        _integer(message_id, 1, _MAX_ROW_ID)
        if not self.chat_ids:
            return None
        scope, params = self._scope()
        with self._connection() as connection:
            if connection is None:
                return None
            row = connection.execute(f"""
                SELECT {self._columns(100_000)} FROM messages m
                LEFT JOIN attachments a ON a.message_row_id=m.id
                WHERE {scope} AND m.id=?
            """, (*params, message_id)).fetchone()
        return _fit_message(self._message(row, 100_000), MESSAGE_MAX_BYTES) if row else None

    def read_attachment(self, attachment_id: str) -> dict | None:
        if not isinstance(attachment_id, str) or not re.fullmatch(r"[a-f0-9]{32}", attachment_id):
            _invalid()
        if not self.chat_ids:
            return None
        scope, params = self._scope()
        columns = ",".join(f"a.{key}" for key in (*_ATTACHMENT_FIELDS, "local_name"))
        with self._connection() as connection:
            if connection is None:
                return None
            row = connection.execute(f"""
                SELECT {columns},m.chat_id,m.instance_id,m.id AS message_row_id
                FROM attachments a JOIN messages m ON m.id=a.message_row_id
                WHERE {scope} AND a.id=?
            """, (*params, attachment_id)).fetchone()
        if row is None:
            return None
        result = {key: row[key] for key in (*_ATTACHMENT_FIELDS, "chat_id", "instance_id", "message_row_id")}
        if result["state"] != "ready":
            return result
        size = result["size"]
        if type(size) is int and size > ATTACHMENT_MAX_BYTES:
            return dict(result, state="too_large")
        name = row["local_name"]
        if (type(size) is not int or size < 0 or result["mime_type"] not in _MEDIA_TYPES
                or not isinstance(result["sha256"], str)
                or not re.fullmatch(r"[a-f0-9]{64}", result["sha256"])
                or not isinstance(name, str)
                or not re.fullmatch(r"[a-f0-9]{32}-[a-f0-9]{32}\.bin", name)
                or not name.startswith(attachment_id + "-")):
            return dict(result, state="unavailable")
        data = self._read_private_file(name, size, result["sha256"])
        if data is None:
            return dict(result, state="unavailable")
        return dict(result, data=data)

    @staticmethod
    def _private(info, *, directory=False):
        correct_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        return (correct_type and info.st_uid == os.getuid() and not info.st_mode & 0o077
                and (directory or info.st_nlink == 1))

    def _read_private_file(self, name, size, expected_hash):
        descriptors = []
        try:
            directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            root_fd = os.open(self._store.root, directory_flags)
            descriptors.append(root_fd)
            if not self._private(os.fstat(root_fd), directory=True):
                return None
            media_fd = os.open("media", directory_flags, dir_fd=root_fd)
            descriptors.append(media_fd)
            if not self._private(os.fstat(media_fd), directory=True):
                return None
            # Nonblocking open prevents an attacker-controlled FIFO from stalling
            # before fstat. Reads are only allowed after verifying a regular file.
            file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=media_fd)
            descriptors.append(file_fd)
            before = os.fstat(file_fd)
            if not self._private(before) or before.st_size != size:
                return None
            content = bytearray()
            while len(content) <= size:
                chunk = os.read(file_fd, min(65536, size + 1 - len(content)))
                if not chunk:
                    break
                content.extend(chunk)
            after = os.fstat(file_fd)
            if (len(content) != size or not self._private(after)
                    or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                    != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                    or not hmac.compare_digest(hashlib.sha256(content).hexdigest(), expected_hash)):
                return None
            return bytes(content)
        except OSError:
            return None
        finally:
            for fd in reversed(descriptors):
                os.close(fd)
