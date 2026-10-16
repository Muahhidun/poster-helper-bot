"""Isolated event routing and media worker; never imports the accounting layer."""
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import threading
import uuid

from .config import ReaderSettings, authorized, chat_ids
from .store import ArchiveStore

logger = logging.getLogger(__name__)
EVENT_TYPES = frozenset({'incomingMessageReceived', 'incomingFileMessageReceived',
                         'outgoingMessageReceived', 'outgoingFileMessageReceived'})
MEDIA_TYPES = frozenset({'image/jpeg', 'image/png', 'image/webp', 'application/pdf'})


class InvalidEvent(ValueError):
    pass


def normalize_event(payload: dict, settings: ReaderSettings):
    """Whitelist event fields. Raw payload, sender phone/name and URLs never enter GET data."""
    if len(json.dumps(payload, ensure_ascii=False).encode()) > 1024 * 1024:
        raise InvalidEvent('event_too_large')
    sender = payload.get('senderData') or {}
    data = payload.get('messageData') or {}
    if not isinstance(sender, dict) or not isinstance(data, dict):
        raise InvalidEvent('invalid_event')
    chat_id = sender.get('chatId')
    message_id = payload.get('idMessage') or data.get('idMessage')
    if (not isinstance(message_id, str) or not 1 <= len(message_id) <= 256
            or '\x00' in message_id):
        raise InvalidEvent('missing_message_id')
    message_type = data.get('typeMessage', '')
    if (not isinstance(message_type, str) or not 1 <= len(message_type) <= 64
            or '\x00' in message_type):
        raise InvalidEvent('invalid_message_type')
    timestamp = payload.get('timestamp')
    if isinstance(timestamp, bool) or not isinstance(timestamp, int) or not 0 < timestamp < 4102444800:
        raise InvalidEvent('invalid_timestamp')
    text = ''
    if message_type == 'textMessage':
        text = (data.get('textMessageData') or {}).get('textMessage', '')
    elif message_type in ('extendedTextMessage', 'quotedMessage'):
        text = (data.get('extendedTextMessageData') or {}).get('text', '')
    file_data = next((data[key] for key in ('fileMessageData', 'imageMessageData',
                     'documentMessageData', 'audioMessageData') if data.get(key)), {})
    if not isinstance(file_data, dict):
        raise InvalidEvent('invalid_attachment')
    if not text:
        text = file_data.get('caption', '')
    if not isinstance(text, str) or len(text) > 32768 or '\x00' in text:
        raise InvalidEvent('invalid_text')
    attachment = None
    if file_data:
        url = file_data.get('downloadUrl') or file_data.get('fileUrl') or ''
        filename = file_data.get('fileName') or 'attachment'
        mime = file_data.get('mimeType') or ''
        if not all(isinstance(value, str) for value in (url, filename, mime)) or len(url) > 8192:
            raise InvalidEvent('invalid_attachment')
        filename = filename.replace('\\', '/').rsplit('/', 1)[-1]
        filename = re.sub(r'[\x00-\x1f\x7f]', '', filename)[:128] or 'attachment'
        if filename in ('.', '..'):
            filename = 'attachment'
        mime = mime.split(';', 1)[0].strip().lower()[:128]
        if '\x00' in mime or '\x00' in url:
            raise InvalidEvent('invalid_attachment')
        state = 'pending' if settings.media_enabled else 'disabled'
        if not url or (mime and mime not in MEDIA_TYPES):
            state = 'unsupported'
        attachment = {'id': uuid.uuid4().hex, 'filename': filename,
                      'declared_mime_type': mime, 'download_url': url, 'state': state}
    return {
        'instance_id': settings.instance_id, 'chat_id': chat_id, 'message_id': message_id,
        'direction': 'outgoing' if payload['typeWebhook'].startswith('outgoing') else 'incoming',
        'sent_at': timestamp, 'received_at': datetime.now(timezone.utc).isoformat(),
        'message_type': message_type, 'text': text,
    }, attachment


class FurnitureReader:
    def __init__(self, settings: ReaderSettings, accounting_chat_ids, app_root: str):
        self.settings = settings
        self.accounting_chat_ids = accounting_chat_ids
        self.app_root = app_root
        self._worker_lock = threading.Lock()

    def configured(self) -> bool:
        cfg = self.settings
        accounting = chat_ids(self.accounting_chat_ids())
        return bool(cfg.enabled and cfg.allowed_chat_ids and accounting
                    and not cfg.allowed_chat_ids.intersection(accounting)
                    and cfg.instance_id and cfg.valid_credentials()
                    and cfg.valid_storage(self.app_root))

    def readable(self) -> bool:
        cfg = self.settings
        return (self.configured() and bool(cfg.read_chat_ids)
                and cfg.read_chat_ids.issubset(cfg.allowed_chat_ids))

    def store(self) -> ArchiveStore:
        # ArchiveStore construction does not initialize/migrate any database.
        return ArchiveStore(Path(self.settings.storage_dir))

    def route_webhook(self, payload: dict, authorization: str):
        """Return None ONLY for the original accountant route (or feature off)."""
        cfg = self.settings
        if not cfg.enabled:
            return None
        sender = payload.get('senderData') or {}
        chat_id = sender.get('chatId') if isinstance(sender, dict) else None
        # Explicit accounting chats retain the original behavior even if the optional
        # module is misconfigured. Overlap disables the entire archive, never dual-routes.
        if chat_id in chat_ids(self.accounting_chat_ids()):
            return None
        if not isinstance(chat_id, str) or chat_id not in cfg.allowed_chat_ids:
            return 'Ignored non-target chat', 200
        if not self.configured():
            return 'Furniture reader not configured', 503
        if not authorized(authorization, cfg.webhook_token_sha256):
            return 'Unauthorized', 401
        instance = payload.get('instanceData') or {}
        if not isinstance(instance, dict) or str(instance.get('idInstance', '')) != cfg.instance_id:
            return 'Unexpected instance', 403
        if payload.get('typeWebhook') not in EVENT_TYPES:
            return 'Ignored webhook type', 200
        try:
            message, attachment = normalize_event(payload, cfg)
            inserted = self.store().save_message(message, attachment)
            return ('Archived' if inserted else 'Duplicate'), 200
        except (InvalidEvent, AttributeError, TypeError):
            return 'Invalid furniture event', 400
        except Exception:
            # No payload/URL/exception string in logs; no fallback to accountant.
            logger.error('Furniture archive write failed')
            return 'Furniture archive unavailable', 503

    def media_tick(self, max_jobs: int = 2):
        cfg = self.settings
        if not (self.configured() and cfg.media_enabled and cfg.media_hosts
                and 1 <= cfg.media_max_bytes <= 25 * 1024 * 1024):
            return 0
        if not self._worker_lock.acquire(blocking=False):
            return 0
        processed = 0
        try:
            from .media import download_media, MediaDownloadError
            store = self.store()
            for _ in range(min(max_jobs, 2)):
                job = store.claim_attachment(cfg.allowed_chat_ids)
                if not job:
                    break
                local_path = None
                try:
                    content, mime_type = download_media(
                        job['download_url'], cfg.media_hosts, cfg.media_max_bytes)
                    local_name = f"{job['id']}-{job['claim_token']}.bin"
                    directory = Path(cfg.storage_dir) / 'media'
                    # Store has already validated private storage and initialized it.
                    if directory.is_symlink():
                        raise ValueError('unsafe_storage')
                    local_path = directory / local_name
                    fd = os.open(local_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                    with os.fdopen(fd, 'wb') as output:
                        output.write(content)
                        output.flush()
                        os.fsync(output.fileno())
                    saved = store.finish_attachment(
                        job['id'], claim_token=job['claim_token'], local_name=local_name,
                        mime_type=mime_type, size=len(content), sha256=hashlib.sha256(content).hexdigest())
                    if not saved:
                        local_path.unlink(missing_ok=True)
                except Exception as error:
                    if local_path is not None:
                        local_path.unlink(missing_ok=True)
                    code = str(error) if isinstance(error, MediaDownloadError) else 'media_failed'
                    store.fail_attachment(job['id'], claim_token=job['claim_token'], error_code=code)
                processed += 1
        except Exception:
            logger.error('Furniture media worker unavailable')
        finally:
            self._worker_lock.release()
        return processed
