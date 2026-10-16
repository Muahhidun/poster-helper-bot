"""Four GET views of the private archive, never the accounting application."""
import base64
from pathlib import Path
import re

from flask import jsonify, request

from .http_contract import QUERY_HEADER, SCOPE_HEADER, decode_query, decode_scope, envelope


def register_mcp_reads(blueprint):
    # Importing ArchiveReader needs only stdlib + the furniture store, not the
    # MCP SDK, JWT dependencies, the financial database or any application env.
    from furniture_mcp.archive import ArchiveReader
    from .routes import reader

    def execute(operation, allowed_args, default_args=()):
        try:
            if (set(request.args) - allowed_args
                    or any(len(request.args.getlist(key)) != 1 for key in request.args)):
                raise ValueError()
            instance, chats = decode_scope(request.headers.get(SCOPE_HEADER))
            cfg = reader().settings
            # Narrow only. The bearer token cannot use this header to grant a
            # foreign instance/chat or override the server's configured policy.
            if instance != cfg.instance_id or not chats <= cfg.read_chat_ids:
                return jsonify(error='Not found'), 404
            archive = ArchiveReader(Path(cfg.storage_dir), instance, chats)
            args = []
            for name, default, integer in default_args:
                value = request.args.get(name, default)
                if integer:
                    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,19}', value):
                        raise ValueError()
                    value = int(value)
                args.append(value)
            result = operation(archive, *args)
            if result is None:
                return jsonify(error='Not found'), 404
            if isinstance(result, dict) and 'data' in result:
                result = dict(result)
                result['data_base64'] = base64.b64encode(result.pop('data')).decode('ascii')
            return jsonify(envelope(instance, chats, result))
        except ValueError:
            return jsonify(error='Invalid archive request'), 400
        except Exception:
            return jsonify(error='Archive unavailable'), 503

    @blueprint.get('/mcp/chats')
    def mcp_chats():
        return execute(lambda archive, cursor, limit: archive.list_chats(cursor, limit),
                       {'after_chat_id', 'limit'},
                       (('after_chat_id', '', False), ('limit', '50', True)))

    @blueprint.get('/mcp/search')
    def mcp_search():
        return execute(lambda archive, chat, cursor, limit:
                       archive.search_messages(decode_query(request.headers.get(QUERY_HEADER)), chat, cursor, limit),
                       {'chat_id', 'after_id', 'limit'},
                       (('chat_id', None, False),
                        ('after_id', '0', True), ('limit', '20', True)))

    @blueprint.get('/mcp/messages/<message_id>')
    def mcp_message(message_id):
        def read(archive):
            if not re.fullmatch(r'[0-9]{1,19}', message_id):
                raise ValueError()
            return archive.read_message(int(message_id))
        return execute(read, set())

    @blueprint.get('/mcp/attachments/<attachment_id>')
    def mcp_attachment(attachment_id):
        return execute(lambda archive: archive.read_attachment(attachment_id), set())
