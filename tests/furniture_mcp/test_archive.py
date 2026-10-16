"""Read-only MCP archive tests: synthetic data, temp storage, no network."""

import hashlib
import json
import os
import sqlite3
import stat
import uuid

import pytest

from furniture_mcp import archive
from furniture_mcp.archive import ArchiveReader
from furniture_reader.store import ArchiveStore


INSTANCE = "archive-test-instance"
CHAT = "furniture-a@g.us"
OTHER_CHAT = "furniture-b@g.us"
SCOPE = frozenset({CHAT})


def message(number=1, *, chat=CHAT, instance=INSTANCE, text="Furniture invoice"):
    return {"instance_id": instance, "chat_id": chat, "message_id": f"message-{number}",
            "direction": "incoming", "sent_at": 1_700_000_000,
            "received_at": "2026-10-16T10:00:00+00:00", "message_type": "textMessage", "text": text}


def pending_attachment():
    return {"id": uuid.uuid4().hex, "filename": "invoice.pdf",
            "declared_mime_type": "application/pdf", "state": "pending",
            "download_url": "https://example.invalid/invoice?secret=synthetic"}


def ready_attachment(root, *, number=1, chat=CHAT, instance=INSTANCE, content=b"%PDF-fake invoice"):
    store = ArchiveStore(root)
    item = pending_attachment()
    store.save_message(message(number, chat=chat, instance=instance), item)
    job = store.claim_attachment(frozenset({chat}))
    name = f"{item['id']}-{job['claim_token']}.bin"
    path = store.media_root / name
    path.write_bytes(content)
    path.chmod(0o600)
    assert store.finish_attachment(item["id"], local_name=name, mime_type="application/pdf",
                                   size=len(content), sha256=hashlib.sha256(content).hexdigest(),
                                   claim_token=job["claim_token"])
    return item, path


def snapshot(root):
    return {str(path.relative_to(root)): (stat.S_IMODE(path.stat().st_mode),
            path.stat().st_mtime_ns, path.read_bytes())
            for path in root.rglob("*") if path.is_file()}


def encoded_size(value):
    return len(json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode())


def test_absent_archive_and_empty_scopes_do_not_initialize_files(tmp_path):
    root = tmp_path / "not-created"
    for chats in (SCOPE, frozenset()):
        reader = ArchiveReader(root, INSTANCE, chats)
        assert reader.list_chats() == {"chats": [], "next_after_chat_id": None}
        assert reader.search_messages("invoice") == {"messages": [], "next_after_id": None}
        assert reader.read_message(1) is None
        assert reader.read_attachment("a" * 32) is None
    assert not root.exists()


def test_strict_instance_and_chat_scope_across_every_query(tmp_path):
    root = tmp_path / "archive"
    own, _ = ready_attachment(root)
    foreign_chat, _ = ready_attachment(root, number=2, chat=OTHER_CHAT)
    foreign_instance, _ = ready_attachment(root, number=3, instance="different-instance")
    reader = ArchiveReader(root, INSTANCE, SCOPE)
    assert reader.list_chats() == {"chats": [{"chat_id": CHAT, "message_count": 1,
            "last_received_at": "2026-10-16T10:00:00+00:00"}], "next_after_chat_id": None}
    rows = reader.search_messages("invoice")["messages"]
    assert len(rows) == 1 and rows[0]["id"] == 1
    assert reader.search_messages("invoice", chat_id=OTHER_CHAT)["messages"] == []
    assert reader.read_message(1)["instance_id"] == INSTANCE
    assert reader.read_message(2) is None
    assert reader.read_message(3) is None
    assert reader.read_attachment(own["id"])["data"] == b"%PDF-fake invoice"
    assert reader.read_attachment(foreign_chat["id"]) is None
    assert reader.read_attachment(foreign_instance["id"]) is None
    empty = ArchiveReader(root, INSTANCE, frozenset())
    assert empty.list_chats()["chats"] == []
    assert empty.read_message(1) is None and empty.read_attachment(own["id"]) is None


def test_list_chats_lexical_pagination_and_counts(tmp_path):
    root = tmp_path / "archive"
    store = ArchiveStore(root)
    chats = frozenset({"work-B", "work-A", "work-C"})
    for chat in chats:
        store.save_message(message(chat=chat))
    store.save_message(message(2, chat="work-B"))
    reader = ArchiveReader(root, INSTANCE, chats)
    first = reader.list_chats(limit=2)
    assert [row["chat_id"] for row in first["chats"]] == ["work-A", "work-B"]
    assert first["chats"][1]["message_count"] == 2
    assert first["next_after_chat_id"] == "work-B"
    second = reader.list_chats(after_chat_id=first["next_after_chat_id"], limit=2)
    assert [row["chat_id"] for row in second["chats"]] == ["work-C"]
    assert second["next_after_chat_id"] is None


def test_search_literal_unicode_casefold_and_sql_injection(tmp_path):
    root = tmp_path / "archive"
    store = ArchiveStore(root)
    for number, text in enumerate(("НАКЛАДНАЯ по мебели", "50%_discount", "50otherdiscount", "' OR 1=1 --")):
        store.save_message(message(number, text=text))
    reader = ArchiveReader(root, INSTANCE, SCOPE)
    assert [m["text"] for m in reader.search_messages("накладная")["messages"]] == ["НАКЛАДНАЯ по мебели"]
    assert [m["text"] for m in reader.search_messages("%_")["messages"]] == ["50%_discount"]
    assert [m["text"] for m in reader.search_messages("' OR 1=1 --")["messages"]] == ["' OR 1=1 --"]
    assert reader.search_messages("invoice", chat_id="' OR 1=1 --")["messages"] == []
    assert reader.list_chats(after_chat_id="' OR 1=1 --")["chats"][0]["message_count"] == 4


def test_search_cursor_does_not_skip_rows_when_page_budget_truncates(tmp_path):
    root = tmp_path / "archive"
    store = ArchiveStore(root)
    for number in range(23):
        store.save_message(message(number, text="🪑" * 1100 + " invoice"))
    reader = ArchiveReader(root, INSTANCE, SCOPE)
    ids = []
    cursor = 0
    pages = 0
    while True:
        page = reader.search_messages("invoice", after_id=cursor, limit=20)
        pages += 1
        assert encoded_size(page) <= archive.SEARCH_MAX_BYTES
        assert page["messages"]
        assert all(row["text_truncated"] and row["text_length"] == 1108 for row in page["messages"])
        ids.extend(row["id"] for row in page["messages"])
        if page["next_after_id"] is None:
            break
        assert page["next_after_id"] == page["messages"][-1]["id"] > cursor
        cursor = page["next_after_id"]
    assert ids == list(range(1, 24))
    assert pages > 2  # byte budget binds sooner than the requested item limit


def test_normal_limit_pagination_and_last_page(tmp_path):
    root = tmp_path / "archive"
    store = ArchiveStore(root)
    for number in range(3):
        store.save_message(message(number))
    reader = ArchiveReader(root, INSTANCE, SCOPE)
    first = reader.search_messages("invoice", limit=2)
    assert [row["id"] for row in first["messages"]] == [1, 2]
    assert first["next_after_id"] == 2
    second = reader.search_messages("invoice", limit=2, after_id=2)
    assert [row["id"] for row in second["messages"]] == [3]
    assert second["next_after_id"] is None
    assert reader.search_messages("invoice", after_id=3)["messages"] == []


def test_read_message_marks_truncation_and_bounds_encoded_response(tmp_path):
    root = tmp_path / "archive"
    store = ArchiveStore(root)
    text = "🪑" * 100_000
    store.save_message(message(text=text))
    reader = ArchiveReader(root, INSTANCE, SCOPE)
    row = reader.read_message(1)
    assert encoded_size(row) <= archive.MESSAGE_MAX_BYTES
    assert row["text_truncated"] is True and row["text_length"] == 100_000
    assert row["text"] and text.startswith(row["text"])
    assert reader.read_message(999) is None


def test_safe_fields_no_urls_paths_or_claims_and_pending_not_downloaded(tmp_path):
    root = tmp_path / "archive"
    store = ArchiveStore(root)
    item = pending_attachment()
    store.save_message(message(), item)
    reader = ArchiveReader(root, INSTANCE, SCOPE)
    outputs = [reader.search_messages("invoice"), reader.read_message(1), reader.read_attachment(item["id"])]
    serialized = json.dumps(outputs)
    for forbidden in ("download_url", "local_name", "claim_token", "synthetic", str(root), "https://"):
        assert forbidden not in serialized
    assert outputs[-1]["state"] == "pending" and "data" not in outputs[-1]


@pytest.mark.parametrize("fragment", ["🪑", "Мебель", '\\"\n\t\r\b\f'])
def test_payload_budgets_leave_room_for_duplicate_escaped_transport(tmp_path, fragment):
    root = tmp_path / "archive"
    store = ArchiveStore(root)
    text = "invoice " + (fragment * 100_000)[:99_992]
    for number in range(6):
        store.save_message(message(number, text=text))
    reader = ArchiveReader(root, INSTANCE, SCOPE)
    for payload, payload_limit, wire_limit in (
        (reader.search_messages("invoice", limit=50), archive.SEARCH_MAX_BYTES, 64 * 1024),
        (reader.read_message(1), archive.MESSAGE_MAX_BYTES, 128 * 1024),
    ):
        assert encoded_size(payload) <= payload_limit
        for text_ascii in (False, True):
            envelope = {"jsonrpc": "2.0", "id": 1, "result": {
                "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=text_ascii)}],
                "structuredContent": payload, "isError": False,
            }}
            assert encoded_size(envelope) < wire_limit


def test_existing_archive_reads_preserve_contents_and_use_ro(tmp_path, monkeypatch):
    root = tmp_path / "archive"
    item, _ = ready_attachment(root)
    before = snapshot(root)
    real_connect = sqlite3.connect
    traces = []
    def connect(database_uri, **kwargs):
        assert database_uri.endswith("?mode=ro") and kwargs["uri"]
        connection = real_connect(database_uri, **kwargs)
        connection.set_trace_callback(traces.append)
        return connection
    monkeypatch.setattr("furniture_reader.store.sqlite3.connect", connect)
    reader = ArchiveReader(root, INSTANCE, SCOPE)
    reader.list_chats()
    reader.search_messages("invoice")
    reader.read_message(1)
    reader.read_attachment(item["id"])
    assert traces.count("PRAGMA query_only = ON") == 4
    assert not any(sql.startswith(("INSERT", "UPDATE", "DELETE", "CREATE", "BEGIN")) for sql in traces)
    assert snapshot(root) == before


def test_attachment_limit_returns_metadata_without_opening_file(tmp_path, monkeypatch):
    root = tmp_path / "archive"
    item, _ = ready_attachment(root, content=b"x" * (2 * 1024 * 1024 + 1))
    reader = ArchiveReader(root, INSTANCE, SCOPE)
    def forbidden(*args):
        raise AssertionError("oversize content must not be read")
    monkeypatch.setattr(reader, "_read_private_file", forbidden)
    result = reader.read_attachment(item["id"])
    assert result["state"] == "too_large" and "data" not in result
    assert result["size"] == 2 * 1024 * 1024 + 1


def test_attachment_at_exact_limit_is_read(tmp_path):
    root = tmp_path / "archive"
    content = b"x" * (2 * 1024 * 1024)
    item, _ = ready_attachment(root, content=content)
    result = ArchiveReader(root, INSTANCE, SCOPE).read_attachment(item["id"])
    assert result["state"] == "ready" and result["data"] == content


@pytest.mark.parametrize("attack", ["symlink", "hardlink", "wrong_size", "wrong_digest", "public_mode", "missing", "directory", "fifo"])
def test_attachment_file_attacks_never_return_bytes(tmp_path, attack):
    root = tmp_path / "archive"
    item, path = ready_attachment(root)
    original = path.read_bytes()
    if attack in ("symlink", "hardlink"):
        target = tmp_path / "target.pdf"
        target.write_bytes(original)
        target.chmod(0o600)
        path.unlink()
        if attack == "symlink":
            path.symlink_to(target)
        else:
            os.link(target, path)
    elif attack == "wrong_size":
        path.write_bytes(original + b"extra")
    elif attack == "wrong_digest":
        path.write_bytes(b"x" * len(original))
    elif attack == "public_mode":
        path.chmod(0o644)
    else:
        path.unlink()
        if attack == "directory":
            path.mkdir(mode=0o700)
        elif attack == "fifo":
            os.mkfifo(path, 0o600)
    result = ArchiveReader(root, INSTANCE, SCOPE).read_attachment(item["id"])
    assert result["state"] == "unavailable" and "data" not in result
    assert str(path) not in str(result)


@pytest.mark.parametrize("name", ["../private.pdf", "/tmp/private.pdf", "a" * 32 + "-" + "b" * 32 + ".bin"])
def test_attachment_database_path_traversal_and_wrong_id_rejected(tmp_path, name):
    root = tmp_path / "archive"
    item, _ = ready_attachment(root)
    with sqlite3.connect(root / "archive.sqlite3") as connection:
        connection.execute("UPDATE attachments SET local_name=? WHERE id=?", (name, item["id"]))
    result = ArchiveReader(root, INSTANCE, SCOPE).read_attachment(item["id"])
    assert result["state"] == "unavailable" and "data" not in result
    assert name not in str(result)


def test_private_file_uid_is_enforced(tmp_path, monkeypatch):
    root = tmp_path / "archive"
    item, _ = ready_attachment(root)
    reader = ArchiveReader(root, INSTANCE, SCOPE)
    real_fstat = os.fstat
    def changed_uid(fd):
        info = real_fstat(fd)
        if stat.S_ISREG(info.st_mode):
            values = list(info)
            values[4] = info.st_uid + 1
            return os.stat_result(values)
        return info
    monkeypatch.setattr(archive.os, "fstat", changed_uid)
    result = reader.read_attachment(item["id"])
    assert result["state"] == "unavailable" and "data" not in result


@pytest.mark.parametrize("column,value", [("mime_type", "text/html"), ("sha256", "invalid-digest")])
def test_invalid_ready_metadata_never_opens_media(tmp_path, monkeypatch, column, value):
    root = tmp_path / "archive"
    item, _ = ready_attachment(root)
    with sqlite3.connect(root / "archive.sqlite3") as connection:
        connection.execute(f"UPDATE attachments SET {column}=? WHERE id=?", (value, item["id"]))
    reader = ArchiveReader(root, INSTANCE, SCOPE)
    def forbidden(*args):
        raise AssertionError("invalid media metadata must not open content")
    monkeypatch.setattr(reader, "_read_private_file", forbidden)
    result = reader.read_attachment(item["id"])
    assert result["state"] == "unavailable" and "data" not in result


@pytest.mark.parametrize("directory", ["root", "media"])
def test_attachment_parent_symlink_is_rejected_before_reading(tmp_path, directory):
    root = tmp_path / "archive"
    item, _ = ready_attachment(root)
    source = root if directory == "root" else root / "media"
    moved = tmp_path / "moved"
    source.rename(moved)
    source.symlink_to(moved, target_is_directory=True)
    with pytest.raises(RuntimeError, match="^Archive unavailable$"):
        ArchiveReader(root, INSTANCE, SCOPE).read_attachment(item["id"])


def test_private_directory_descriptors_reject_post_query_symlink(tmp_path):
    root = tmp_path / "archive"
    _, path = ready_attachment(root)
    reader = ArchiveReader(root, INSTANCE, SCOPE)
    moved = tmp_path / "moved-media"
    (root / "media").rename(moved)
    (root / "media").symlink_to(moved, target_is_directory=True)
    # This helper is called after the SQL query. A replacement directory must
    # still be rejected even if the earlier archive checks already succeeded.
    assert reader._read_private_file(path.name, len(b"%PDF-fake invoice"),
                                    hashlib.sha256(b"%PDF-fake invoice").hexdigest()) is None


def test_queries_interrupt_at_step_budget_without_partial_results(tmp_path, monkeypatch):
    root = tmp_path / "archive"
    store = ArchiveStore(root)
    for number in range(120):
        store.save_message(message(number))
    monkeypatch.setattr(archive, "QUERY_MAX_STEPS", 1000)
    with pytest.raises(RuntimeError, match="^Archive unavailable$"):
        ArchiveReader(root, INSTANCE, SCOPE).list_chats()


def test_queries_interrupt_at_time_budget_without_partial_results(tmp_path, monkeypatch):
    root = tmp_path / "archive"
    ArchiveStore(root).save_message(message())
    monkeypatch.setattr(archive, "QUERY_SECONDS", 0)
    with pytest.raises(RuntimeError, match="^Archive unavailable$"):
        ArchiveReader(root, INSTANCE, SCOPE).search_messages("invoice")


@pytest.mark.parametrize("method,args", [
    ("list_chats", {"limit": 51}), ("list_chats", {"limit": True}),
    ("list_chats", {"after_chat_id": "a" * 257}),
    ("search_messages", {"query": "x"}), ("search_messages", {"query": " " * 2}),
    ("search_messages", {"query": "x" * 201}), ("search_messages", {"query": "\x00x"}),
    ("search_messages", {"query": "ok", "after_id": -1}),
    ("search_messages", {"query": "ok", "after_id": 1 << 63}),
    ("search_messages", {"query": "ok", "limit": 0}),
    ("search_messages", {"query": "ok", "limit": 51}),
    ("search_messages", {"query": "ok", "chat_id": []}),
    ("read_message", {"message_id": True}), ("read_message", {"message_id": 0}),
    ("read_message", {"message_id": "1 OR 1=1"}),
    ("read_attachment", {"attachment_id": "../bad"}),
])
def test_invalid_arguments_generic_and_do_not_initialize(tmp_path, method, args):
    root = tmp_path / "archive"
    reader = ArchiveReader(root, INSTANCE, SCOPE)
    with pytest.raises(ValueError, match="^Invalid archive request$"):
        getattr(reader, method)(**args)
    assert not root.exists()


@pytest.mark.parametrize("instance,chats", [("", SCOPE), ("x" * 257, SCOPE),
    (INSTANCE, "not-a-set"), (INSTANCE, frozenset({""})),
    (INSTANCE, frozenset(f"chat-{i}" for i in range(257)))])
def test_invalid_server_scope_is_rejected_without_io(tmp_path, instance, chats):
    with pytest.raises(ValueError, match="^Invalid archive request$"):
        ArchiveReader(tmp_path / "archive", instance, chats)
    assert not (tmp_path / "archive").exists()


def test_unrelated_database_rejected_without_modification(tmp_path):
    root = tmp_path / "archive"
    root.mkdir(mode=0o700)
    database = root / "archive.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE unrelated(value TEXT)")
    database.chmod(0o600)
    before = snapshot(root)
    with pytest.raises(RuntimeError, match="^Archive unavailable$"):
        ArchiveReader(root, INSTANCE, SCOPE).list_chats()
    assert snapshot(root) == before
