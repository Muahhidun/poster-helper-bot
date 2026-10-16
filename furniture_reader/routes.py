"""Dedicated bearer-authenticated GET API; no owner-session or Telegram fallback."""
import os
from pathlib import Path
import re
import stat

from flask import Blueprint, current_app, jsonify, request, send_file

from .config import authorized


def reader():
    return current_app.extensions['furniture_reader']


def create_blueprint():
    blueprint = Blueprint('furniture_reader', __name__, url_prefix='/api/furniture-reader')

    @blueprint.before_request
    def guard():
        service = reader()
        if not service.settings.enabled:
            return jsonify(error='Not found'), 404
        if request.method != 'GET':
            return jsonify(error='GET only'), 405
        if not service.readable():
            return jsonify(error='Reader not configured'), 503
        if not authorized(request.headers.get('Authorization', ''), service.settings.read_token_sha256):
            return jsonify(error='Unauthorized'), 401, {'WWW-Authenticate': 'Bearer'}

    @blueprint.after_request
    def privacy_headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Content-Security-Policy'] = "default-src 'none'; sandbox"
        response.headers['Referrer-Policy'] = 'no-referrer'
        return response

    @blueprint.errorhandler(Exception)
    def unavailable(error):
        # Do not return SQL paths, signed media URLs or exception messages.
        from werkzeug.exceptions import HTTPException
        if isinstance(error, HTTPException):
            return jsonify(error=error.name), error.code
        return jsonify(error='Archive unavailable'), 503

    @blueprint.get('/messages')
    def messages():
        service = reader()
        chat_id = request.args.get('chat_id')
        if chat_id is not None and chat_id not in service.settings.read_chat_ids:
            return jsonify(error='Not found'), 404
        try:
            after_id = int(request.args.get('after_id', '0'))
            limit = int(request.args.get('limit', '50'))
            if after_id < 0 or not 1 <= limit <= 100:
                raise ValueError()
        except ValueError:
            return jsonify(error='Invalid pagination'), 400
        rows = service.store().list_messages(service.settings.read_chat_ids, chat_id, after_id, limit)
        return jsonify(messages=rows, next_after_id=rows[-1]['id'] if rows else after_id)

    @blueprint.get('/status')
    def status():
        service = reader()
        return jsonify(service.store().status(service.settings.read_chat_ids))

    @blueprint.get('/attachments/<attachment_id>')
    def attachment(attachment_id):
        service = reader()
        if not re.fullmatch(r'[a-f0-9]{32}', attachment_id):
            return jsonify(error='Not found'), 404
        item = service.store().get_attachment(attachment_id, service.settings.read_chat_ids)
        if not item or item['state'] != 'ready':
            return jsonify(error='Not found'), 404
        name = item.get('local_name') or ''
        if not re.fullmatch(r'[a-f0-9]{32}-[a-f0-9]{32}\.bin', name):
            return jsonify(error='Not found'), 404
        directory = Path(service.settings.storage_dir) / 'media'
        if directory.is_symlink():
            return jsonify(error='Not found'), 404
        try:
            fd = os.open(directory / name, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError:
            return jsonify(error='Not found'), 404
        file = os.fdopen(fd, 'rb')
        info = os.fstat(file.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_size != item['size']
                or info.st_nlink != 1 or info.st_uid != os.getuid() or info.st_mode & 0o077):
            file.close()
            return jsonify(error='Not found'), 404
        # Always attachment; no inline PDF/image preview or public static URL.
        response = send_file(file, mimetype='application/octet-stream', as_attachment=True,
                             download_name=item['filename'], conditional=False, etag=False)
        response.content_length = info.st_size
        response.call_on_close(file.close)
        return response

    from .http_api import register_mcp_reads
    register_mcp_reads(blueprint)
    return blueprint
