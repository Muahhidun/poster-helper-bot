"""Private furniture archive; deliberately independent of the accounting database.

Construction and all read methods are side-effect free. Only ingestion creates
the archive; only the dedicated media worker claims or changes attachment jobs.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
import uuid


_APPLICATION_ID = 0x4655524E  # FURN; reject an unrelated SQLite database.
_SCHEMA_VERSION = 1
_STATES = ("pending", "processing", "failed", "ready", "disabled", "unsupported")
_SAFE_ATTACHMENT_FIELDS = (
    "id", "filename", "declared_mime_type", "state", "mime_type", "size",
    "sha256", "error_code", "attempts",
)
_RETRY_DELAY = 30
_LEASE_SECONDS = 300


class ArchiveStore:
    """SQLite archive rooted at a private, non-public directory.

    Every call owns its connection. BEGIN IMMEDIATE serializes writers across
    both threads and processes, including duplicate delivery and worker claims.
    The directory must not be writable by untrusted local users. Symlink and
    hard-link checks are defense in depth, not a sandbox against the OS owner.
    """

    def __init__(self, root: Path):
        self.root = Path(os.path.abspath(root))
        self.db_path = self.root / "archive.sqlite3"
        self.media_root = self.root / "media"

    @staticmethod
    def _inspect(path: Path, *, directory: bool, private: bool = False):
        try:
            info = path.lstat()
        except FileNotFoundError:
            return None
        valid_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        if not valid_type or stat.S_ISLNK(info.st_mode):
            raise ValueError("unsafe_archive_path")
        if not directory and info.st_nlink != 1:
            raise ValueError("unsafe_archive_hardlink")
        if private and (info.st_uid != os.getuid() or info.st_mode & 0o077):
            raise ValueError("archive_permissions_not_private")
        return info

    def _check_paths(self):
        for ancestor in reversed((self.root, *self.root.parents)):
            self._inspect(ancestor, directory=True)
        self._inspect(self.root, directory=True, private=True)
        self._inspect(self.media_root, directory=True, private=True)
        self._inspect(self.db_path, directory=False, private=True)
        # SQLite itself may open these names when recovering a transaction.
        for suffix in ("-journal", "-wal", "-shm"):
            self._inspect(Path(str(self.db_path) + suffix), directory=False, private=True)

    def _create_paths(self):
        # Do not chmod a caller's existing directory or database. Reject unsafe
        # permissions instead of silently changing an unrelated location.
        for ancestor in reversed((self.root, *self.root.parents)):
            if self._inspect(ancestor, directory=True) is None:
                try:
                    ancestor.mkdir(mode=0o700)
                except FileExistsError:  # another ingest created it
                    self._inspect(ancestor, directory=True)
        self._inspect(self.root, directory=True, private=True)
        try:
            self.media_root.mkdir(mode=0o700)
        except FileExistsError:
            self._inspect(self.media_root, directory=True, private=True)
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(self.db_path, flags, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
        self._check_paths()

    @staticmethod
    def _verify_schema(connection, *, initialize: bool):
        app_id = connection.execute("PRAGMA application_id").fetchone()[0]
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if initialize and app_id == 0 and version == 0:
            if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' LIMIT 1").fetchone():
                raise ValueError("not_a_furniture_archive")
            connection.execute(f"PRAGMA application_id = {_APPLICATION_ID}")
            connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            connection.execute("""
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    instance_id TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    direction TEXT NOT NULL CHECK(direction IN ('incoming','outgoing')),
                    sent_at INTEGER NOT NULL,
                    received_at TEXT NOT NULL,
                    message_type TEXT NOT NULL,
                    text TEXT NOT NULL,
                    UNIQUE(instance_id, chat_id, message_id)
                )
            """)
            connection.execute("CREATE INDEX messages_chat_cursor ON messages(chat_id, id)")
            connection.execute("""
                CREATE TABLE attachments (
                    id TEXT PRIMARY KEY,
                    message_row_id INTEGER NOT NULL UNIQUE REFERENCES messages(id),
                    filename TEXT NOT NULL,
                    declared_mime_type TEXT,
                    download_url TEXT,
                    state TEXT NOT NULL CHECK(state IN
                        ('pending','processing','failed','ready','disabled','unsupported')),
                    local_name TEXT,
                    mime_type TEXT,
                    size INTEGER,
                    sha256 TEXT,
                    error_code TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at REAL NOT NULL DEFAULT 0,
                    lease_until REAL,
                    claim_token TEXT
                )
            """)
            connection.execute("CREATE INDEX attachments_work ON attachments(state, next_attempt_at)")
        elif app_id != _APPLICATION_ID or version != _SCHEMA_VERSION:
            raise ValueError("not_a_supported_furniture_archive")

    @contextmanager
    def _connection(self, *, write: bool = False, initialize: bool = False, timeout: float = 5):
        if initialize:
            self._create_paths()
        else:
            self._check_paths()
            if not self.db_path.exists():
                yield None
                return
        mode = "rw" if write else "ro"
        connection = sqlite3.connect(f"{self.db_path.as_uri()}?mode={mode}", uri=True, timeout=timeout)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA trusted_schema = OFF")
            if write:
                connection.execute("BEGIN IMMEDIATE")
            else:
                connection.execute("PRAGMA query_only = ON")
            self._verify_schema(connection, initialize=initialize)
            yield connection
            if write:
                connection.commit()
        except Exception:
            if write:
                connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _text(value, *, maximum: int, empty: bool = False):
        if not isinstance(value, str) or len(value) > maximum or (not empty and not value) or "\x00" in value:
            raise ValueError("invalid_archive_field")
        return value

    @classmethod
    def _scope(cls, chat_ids):
        return tuple(sorted({cls._text(chat_id, maximum=256) for chat_id in chat_ids}))

    def save_message(self, message: dict, attachment: dict | None = None) -> bool:
        """Atomically store one admitted event; never overwrite a duplicate.

        The service must authorize chat/instance before calling this method.
        Raw event payloads and accounting credentials are never stored here.
        """
        values = {
            key: self._text(message[key], maximum=maximum, empty=(key == "text"))
            for key, maximum in (("instance_id", 256), ("chat_id", 256), ("message_id", 512),
                                 ("direction", 16), ("received_at", 64), ("message_type", 64),
                                 ("text", 100_000))
        }
        if values["direction"] not in ("incoming", "outgoing"):
            raise ValueError("invalid_message_direction")
        sent_at = message["sent_at"]
        if type(sent_at) is not int or not 0 <= sent_at <= 253402300799:
            raise ValueError("invalid_message_timestamp")
        received_at = datetime.fromisoformat(values["received_at"].replace("Z", "+00:00"))
        if received_at.tzinfo is None:
            raise ValueError("received_at_must_include_timezone")
        values["received_at"] = received_at.astimezone(timezone.utc).isoformat()
        values["sent_at"] = sent_at
        attachment_values = None
        if attachment is not None:
            attachment_id = self._text(attachment["id"], maximum=32)
            if not re.fullmatch(r"[0-9a-f]{32}", attachment_id):
                raise ValueError("invalid_attachment_id")
            filename = self._text(attachment["filename"], maximum=255)
            if any(ord(c) < 32 or ord(c) == 127 or c in "/\\" for c in filename) or filename in (".", ".."):
                raise ValueError("invalid_attachment_filename")
            state = attachment["state"]
            if state not in ("pending", "disabled", "unsupported"):
                raise ValueError("invalid_initial_attachment_state")
            declared_mime = attachment.get("declared_mime_type")
            if declared_mime is not None:
                declared_mime = self._text(declared_mime, maximum=128, empty=True)
            url = self._text(attachment.get("download_url"), maximum=8192) if state == "pending" else None
            attachment_values = (attachment_id, filename, declared_mime, url, state)
        with self._connection(write=True, initialize=True) as connection:
            cursor = connection.execute("""
                INSERT INTO messages(instance_id,chat_id,message_id,direction,sent_at,received_at,message_type,text)
                VALUES(:instance_id,:chat_id,:message_id,:direction,:sent_at,:received_at,:message_type,:text)
                ON CONFLICT(instance_id,chat_id,message_id) DO NOTHING
            """, values)
            if not cursor.rowcount:
                return False
            if attachment_values:
                attachment_id, filename, declared_mime, url, state = attachment_values
                connection.execute("""
                    INSERT INTO attachments(id,message_row_id,filename,declared_mime_type,download_url,state)
                    VALUES(?,?,?,?,?,?)
                """, (attachment_id, cursor.lastrowid, filename, declared_mime, url, state))
        return True

    def list_messages(self, chat_ids: frozenset[str], chat_id: str | None = None,
                      after_id: int = 0, limit: int = 50) -> list[dict]:
        scopes = self._scope(chat_ids)
        if type(after_id) is not int or after_id < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("invalid_message_pagination")
        if not scopes or (chat_id is not None and chat_id not in scopes):
            return []
        if chat_id is not None:
            scopes = (chat_id,)
        slots = ",".join("?" for _ in scopes)
        attachment_columns = ",".join(f"a.{key} AS attachment_{key}" for key in _SAFE_ATTACHMENT_FIELDS)
        with self._connection() as connection:
            if connection is None:
                return []
            rows = connection.execute(f"""
                SELECT m.*, {attachment_columns} FROM messages m
                LEFT JOIN attachments a ON a.message_row_id=m.id
                WHERE m.chat_id IN ({slots}) AND m.id > ? ORDER BY m.id LIMIT ?
            """, (*scopes, after_id, limit)).fetchall()
        result = []
        for row in rows:
            item = {key: row[key] for key in row.keys() if not key.startswith("attachment_")}
            item["attachment"] = ({key: row[f"attachment_{key}"] for key in _SAFE_ATTACHMENT_FIELDS}
                                  if row["attachment_id"] else None)
            result.append(item)
        return result

    def get_attachment(self, attachment_id: str, chat_ids: frozenset[str]) -> dict | None:
        """Internal lookup for authenticated download; never a public serializer."""
        scopes = self._scope(chat_ids)
        if not scopes:
            return None
        slots = ",".join("?" for _ in scopes)
        columns = ",".join(f"a.{key}" for key in (*_SAFE_ATTACHMENT_FIELDS, "local_name"))
        with self._connection() as connection:
            if connection is None:
                return None
            row = connection.execute(f"""
                SELECT {columns},m.chat_id FROM attachments a JOIN messages m ON m.id=a.message_row_id
                WHERE a.id=? AND m.chat_id IN ({slots})
            """, (attachment_id, *scopes)).fetchone()
            return dict(row) if row else None

    def status(self, chat_ids: frozenset[str]) -> dict:
        scopes = self._scope(chat_ids)
        result = {"message_count": 0, "last_received_at": None,
                  "attachments": {state: 0 for state in _STATES}}
        if not scopes:
            return result
        slots = ",".join("?" for _ in scopes)
        with self._connection() as connection:
            if connection is None:
                return result
            row = connection.execute(f"""
                SELECT COUNT(*) AS message_count,MAX(received_at) AS last_received_at
                FROM messages WHERE chat_id IN ({slots})
            """, scopes).fetchone()
            result.update(dict(row))
            for row in connection.execute(f"""
                SELECT a.state,COUNT(*) AS total FROM attachments a JOIN messages m ON m.id=a.message_row_id
                WHERE m.chat_id IN ({slots}) GROUP BY a.state
            """, scopes):
                result["attachments"][row["state"]] = row["total"]
        return result

    def claim_attachment(self, chat_ids: frozenset[str], max_attempts: int = 3) -> dict | None:
        """Dedicated media worker only; never run from a GET request.

        A new claim token fences off workers whose processing lease expired.
        Recovery is scoped and bounded, and never initializes an absent archive.
        """
        scopes = self._scope(chat_ids)
        if type(max_attempts) is not int or not 1 <= max_attempts <= 10:
            raise ValueError("invalid_max_attempts")
        if not scopes:
            return None
        slots = ",".join("?" for _ in scopes)
        now = time.time()
        with self._connection(write=True) as connection:
            if connection is None:
                return None
            connection.execute(f"""
                UPDATE attachments SET state='failed',error_code='lease_expired',
                    lease_until=NULL,claim_token=NULL,next_attempt_at=?
                WHERE state='processing' AND lease_until <= ? AND message_row_id IN
                    (SELECT id FROM messages WHERE chat_id IN ({slots}))
            """, (now, now, *scopes))
            row = connection.execute(f"""
                SELECT a.*,m.chat_id FROM attachments a JOIN messages m ON m.id=a.message_row_id
                WHERE m.chat_id IN ({slots}) AND a.state IN ('pending','failed')
                    AND a.attempts < ? AND a.next_attempt_at <= ?
                ORDER BY a.message_row_id LIMIT 1
            """, (*scopes, max_attempts, now)).fetchone()
            if row is None:
                return None
            token = uuid.uuid4().hex
            connection.execute("""
                UPDATE attachments SET state='processing',attempts=attempts+1,lease_until=?,claim_token=?
                WHERE id=?
            """, (now + _LEASE_SECONDS, token, row["id"]))
            result = dict(row)
            result.update(state="processing", attempts=row["attempts"] + 1,
                          lease_until=now + _LEASE_SECONDS, claim_token=token)
            return result

    def finish_attachment(self, attachment_id: str, *, local_name: str, mime_type: str,
                          size: int, sha256: str, claim_token: str) -> bool:
        if not isinstance(claim_token, str) or not re.fullmatch(r"[0-9a-f]{32}", claim_token):
            raise ValueError("invalid_claim_token")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", local_name) or local_name in (".", ".."):
            raise ValueError("invalid_local_media_name")
        if not re.fullmatch(r"[0-9a-f]{64}", sha256) or type(size) is not int or size < 0:
            raise ValueError("invalid_media_metadata")
        self._text(mime_type, maximum=128)
        with self._connection(write=True) as connection:
            if connection is None:
                return False
            cursor = connection.execute("""
                UPDATE attachments SET state='ready',local_name=?,mime_type=?,size=?,sha256=?,
                    download_url=NULL,error_code=NULL,lease_until=NULL,claim_token=NULL
                WHERE id=? AND state='processing' AND claim_token=?
            """, (local_name, mime_type, size, sha256, attachment_id, claim_token))
            return bool(cursor.rowcount)

    def fail_attachment(self, attachment_id: str, error_code: str,
                        *, claim_token: str) -> bool:
        if not isinstance(claim_token, str) or not re.fullmatch(r"[0-9a-f]{32}", claim_token):
            raise ValueError("invalid_claim_token")
        # Never persist exceptions, URLs, response bodies, or credentials.
        if not isinstance(error_code, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", error_code):
            raise ValueError("invalid_media_error_code")
        with self._connection(write=True) as connection:
            if connection is None:
                return False
            cursor = connection.execute("""
                UPDATE attachments SET state='failed',error_code=?,next_attempt_at=?,lease_until=NULL,claim_token=NULL
                WHERE id=? AND state='processing' AND claim_token=?
            """, (error_code, time.time() + _RETRY_DELAY, attachment_id, claim_token))
            return bool(cursor.rowcount)
