"""Small, dependency-free wire boundary for the furniture read API."""
import json

SCOPE_HEADER = "X-Furniture-Archive-Scope"
QUERY_HEADER = "X-Furniture-Archive-Query"
MAX_SCOPE_BYTES = 4096
VERSION = 1


def invalid():
    raise ValueError("Invalid archive request")


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            invalid()
        result[key] = value
    return result


def load_json(data):
    def nonfinite(_):
        invalid()
    return json.loads(data, object_pairs_hook=_pairs, parse_constant=nonfinite)


def _identity(value):
    return (isinstance(value, str) and 1 <= len(value) <= 256 and value != "*"
            and all(32 < ord(char) < 127 for char in value))


def encode_scope(instance_id, chat_ids):
    if (not _identity(instance_id) or not isinstance(chat_ids, frozenset)
            or not 1 <= len(chat_ids) <= 100 or not all(map(_identity, chat_ids))):
        invalid()
    value = json.dumps({"instance_id": instance_id, "chat_ids": sorted(chat_ids)},
                       ensure_ascii=True, separators=(",", ":"))
    if len(value.encode("ascii")) > MAX_SCOPE_BYTES:
        invalid()
    return value


def decode_scope(value):
    if not isinstance(value, str) or not 0 < len(value) <= MAX_SCOPE_BYTES:
        invalid()
    try:
        value.encode("ascii")
        scope = load_json(value)
        if not isinstance(scope, dict) or set(scope) != {"instance_id", "chat_ids"}:
            invalid()
        chats = scope["chat_ids"]
        if not isinstance(chats, list) or not all(map(_identity, chats)):
            invalid()
        selected = frozenset(chats)
        if chats != sorted(selected):
            invalid()
        encode_scope(scope["instance_id"], selected)
        return scope["instance_id"], selected
    except (UnicodeError, TypeError, RecursionError):
        invalid()


def envelope(instance_id, chat_ids, result):
    return {"version": VERSION, "instance_id": instance_id,
            "chat_ids": sorted(chat_ids), "result": result}


def encode_query(query):
    if (not isinstance(query, str) or not 2 <= len(query) <= 200
            or len(query.strip()) < 2 or '\x00' in query):
        invalid()
    return json.dumps(query, ensure_ascii=True)


def decode_query(value):
    if not isinstance(value, str) or not 0 < len(value) <= 1202:
        invalid()
    try:
        value.encode('ascii')
        query = load_json(value)
        encode_query(query)
        return query
    except (UnicodeError, TypeError, RecursionError):
        invalid()
