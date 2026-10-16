"""Archive tests use fake messages and temporary SQLite files only."""

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import sqlite3
import stat
import uuid

import pytest

from furniture_reader.store import ArchiveStore


CHAT_A = "furniture-a@g.us"
CHAT_B = "furniture-b@g.us"
SCOPE = frozenset({CHAT_A})


def message(message_id="message-1", chat_id=CHAT_A, instance_id="test-instance"):
    return {"instance_id": instance_id, "chat_id": chat_id, "message_id": message_id,
            "direction": "incoming", "sent_at": 1_700_000_000,
            "received_at": "2026-10-16T10:00:00+00:00", "message_type": "textMessage",
            "text": "Test furniture invoice"}


def attachment(state="pending", attachment_id=None):
    return {"id": attachment_id or uuid.uuid4().hex, "filename": "invoice.pdf",
            "declared_mime_type": "application/pdf", "state": state,
            "download_url": "https://media.example.invalid/invoice?signature=fake"}


def finish(store, claim, **overrides):
    args = {"local_name": f"{claim['id']}-{claim['claim_token']}.bin",
            "mime_type": "application/pdf", "size": 15, "sha256": "a" * 64,
            "claim_token": claim["claim_token"]}
    args.update(overrides)
    return store.finish_attachment(claim["id"], **args)


def test_construction_reads_and_worker_on_absent_archive_do_not_create_files(tmp_path):
    root = tmp_path / "absent"
    store = ArchiveStore(root)
    assert store.list_messages(SCOPE) == []
    assert store.get_attachment("a" * 32, SCOPE) is None
    assert store.status(SCOPE) == {
        "message_count": 0, "last_received_at": None,
        "attachments": dict.fromkeys(("pending", "processing", "failed", "ready", "disabled", "unsupported"), 0),
    }
    assert store.claim_attachment(SCOPE) is None
    assert not store.fail_attachment("a" * 32, "network_error", claim_token="b" * 32)
    assert not store.finish_attachment("a" * 32, local_name="media.bin", mime_type="application/pdf",
                                       size=5, sha256="c" * 64, claim_token="b" * 32)
    assert not root.exists()


def test_ingest_creates_only_private_archive_and_permissions(tmp_path):
    store = ArchiveStore(tmp_path / "archive")
    assert store.save_message(message())
    assert set(p.name for p in store.root.iterdir()) == {"archive.sqlite3", "media"}
    assert stat.S_IMODE(store.root.stat().st_mode) == 0o700
    assert stat.S_IMODE(store.media_root.stat().st_mode) == 0o700
    assert stat.S_IMODE(store.db_path.stat().st_mode) == 0o600


def test_deduplication_includes_instance_and_chat_preserves_original_attachment(tmp_path):
    store = ArchiveStore(tmp_path / "archive")
    original = attachment()
    assert store.save_message(message(), original)
    assert not store.save_message({**message(), "text": "changed"}, attachment())
    assert store.save_message(message(chat_id=CHAT_B))
    assert store.save_message(message(instance_id="other-instance"))
    rows = store.list_messages(frozenset({CHAT_A, CHAT_B}))
    assert len(rows) == 3
    assert rows[0]["text"] == "Test furniture invoice"
    assert rows[0]["attachment"]["id"] == original["id"]
    assert store.status(SCOPE)["attachments"]["pending"] == 1


def test_message_and_attachment_insert_is_atomic(tmp_path):
    store = ArchiveStore(tmp_path / "archive")
    item = attachment()
    store.save_message(message("first"), item)
    with pytest.raises(sqlite3.IntegrityError):
        store.save_message(message("rolled-back"), item)
    assert [row["message_id"] for row in store.list_messages(SCOPE)] == ["first"]


def test_unknown_declared_mime_is_stored_without_trusting_it(tmp_path):
    store = ArchiveStore(tmp_path / "archive")
    item = {**attachment(), "declared_mime_type": ""}
    assert store.save_message(message(), item)
    assert store.get_attachment(item["id"], SCOPE)["declared_mime_type"] == ""
    assert store.get_attachment(item["id"], SCOPE)["mime_type"] is None


def test_concurrent_duplicate_deliveries_across_store_instances_insert_once(tmp_path):
    root = tmp_path / "archive"
    def deliver(_):
        return ArchiveStore(root).save_message(message(), attachment())
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(deliver, range(16))) == 1
    assert ArchiveStore(root).status(SCOPE)["message_count"] == 1


def test_reads_scope_messages_attachments_and_status_without_url_or_path(tmp_path):
    store = ArchiveStore(tmp_path / "archive")
    first = attachment()
    hidden = attachment()
    store.save_message(message("one"), first)
    store.save_message({**message("two", CHAT_B), "received_at": "2026-10-17T00:00:00Z"}, hidden)
    store.save_message(message("three"))
    rows = store.list_messages(SCOPE, limit=1)
    assert len(rows) == 1
    assert rows[0]["attachment"]["id"] == first["id"]
    assert "download_url" not in str(rows)
    assert "local_name" not in str(rows)
    assert "signature" not in str(rows)
    assert [row["message_id"] for row in store.list_messages(SCOPE, after_id=rows[0]["id"])] == ["three"]
    assert store.list_messages(SCOPE, chat_id=CHAT_B) == []
    assert store.list_messages(frozenset()) == []
    assert store.get_attachment(hidden["id"], SCOPE) is None
    assert store.get_attachment(first["id"], frozenset()) is None
    metadata = store.get_attachment(first["id"], SCOPE)
    assert metadata["chat_id"] == CHAT_A
    assert "download_url" not in metadata
    status = store.status(SCOPE)
    assert status["message_count"] == 2
    assert status["last_received_at"] == "2026-10-16T10:00:00+00:00"
    assert status["attachments"]["pending"] == 1


def test_gets_use_sqlite_ro_query_only_and_do_not_mutate_archive(tmp_path, monkeypatch):
    store = ArchiveStore(tmp_path / "archive")
    item = attachment()
    store.save_message(message(), item)
    before = {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in store.root.iterdir() if p.is_file()}
    real_connect = sqlite3.connect
    seen = []
    def connect(database_uri, **kwargs):
        assert database_uri.endswith("?mode=ro") and kwargs["uri"] is True
        connection = real_connect(database_uri, **kwargs)
        connection.set_trace_callback(seen.append)
        return connection
    monkeypatch.setattr("furniture_reader.store.sqlite3.connect", connect)
    store.list_messages(SCOPE)
    store.get_attachment(item["id"], SCOPE)
    store.status(SCOPE)
    assert seen.count("PRAGMA query_only = ON") == 3
    assert not any(sql.startswith(("CREATE", "UPDATE", "DELETE", "INSERT", "BEGIN")) for sql in seen)
    after = {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in store.root.iterdir() if p.is_file()}
    assert before == after


def test_claim_only_scoped_pending_jobs_and_success_clears_source_url(tmp_path):
    store = ArchiveStore(tmp_path / "archive")
    store.save_message(message("other", CHAT_B), attachment())
    for state in ("disabled", "unsupported"):
        store.save_message(message(state), attachment(state))
    item = attachment()
    store.save_message(message("pending"), item)
    claim = store.claim_attachment(SCOPE)
    assert claim["id"] == item["id"] and claim["chat_id"] == CHAT_A
    assert claim["attempts"] == 1 and claim["state"] == "processing"
    assert claim["download_url"] == item["download_url"]
    assert store.claim_attachment(SCOPE) is None
    assert finish(store, claim)
    assert not finish(store, claim)
    metadata = store.get_attachment(item["id"], SCOPE)
    assert metadata["state"] == "ready" and metadata["size"] == 15
    assert "local_name" not in store.list_messages(SCOPE)[-1]["attachment"]
    with sqlite3.connect(store.db_path) as connection:
        assert connection.execute("SELECT download_url FROM attachments WHERE id=?", (item["id"],)).fetchone()[0] is None
    assert store.status(SCOPE)["attachments"] == {
        "pending": 0, "processing": 0, "failed": 0, "ready": 1, "disabled": 1, "unsupported": 1,
    }


def test_failure_retries_delayed_and_bounded(tmp_path, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("furniture_reader.store.time.time", lambda: now[0])
    store = ArchiveStore(tmp_path / "archive")
    item = attachment()
    store.save_message(message(), item)
    for attempt in range(1, 4):
        claim = store.claim_attachment(SCOPE)
        assert claim["attempts"] == attempt
        assert store.fail_attachment(item["id"], "network_error", claim_token=claim["claim_token"])
        assert store.claim_attachment(SCOPE) is None
        now[0] += 30
    assert store.claim_attachment(SCOPE) is None
    assert store.get_attachment(item["id"], SCOPE)["state"] == "failed"


def test_processing_lease_recovers_only_from_claim_fences_stale_worker_and_bounds_crashes(tmp_path, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("furniture_reader.store.time.time", lambda: now[0])
    store = ArchiveStore(tmp_path / "archive")
    item = attachment()
    store.save_message(message(), item)
    first = store.claim_attachment(SCOPE)
    now[0] += 301
    # GETs cannot resurrect/claim/recover expired work.
    assert store.status(SCOPE)["attachments"]["processing"] == 1
    assert store.get_attachment(item["id"], SCOPE)["state"] == "processing"
    second = store.claim_attachment(SCOPE)
    assert second["claim_token"] != first["claim_token"] and second["attempts"] == 2
    assert not finish(store, first)
    assert not store.fail_attachment(item["id"], "network_error", claim_token=first["claim_token"])
    now[0] += 301
    third = store.claim_attachment(SCOPE)
    assert third["attempts"] == 3
    now[0] += 301
    assert store.claim_attachment(SCOPE) is None
    assert store.get_attachment(item["id"], SCOPE)["error_code"] == "lease_expired"
    assert store.get_attachment(item["id"], SCOPE)["state"] == "failed"


def test_concurrent_workers_claim_once_and_never_touch_revoked_chat(tmp_path):
    root = tmp_path / "archive"
    store = ArchiveStore(root)
    store.save_message(message(), attachment())
    store.save_message(message("revoked", CHAT_B), attachment())
    with ThreadPoolExecutor(max_workers=6) as pool:
        claims = list(pool.map(lambda _: ArchiveStore(root).claim_attachment(SCOPE), range(12)))
    assert len([claim for claim in claims if claim]) == 1
    assert store.status(frozenset({CHAT_B}))["attachments"]["pending"] == 1


@pytest.mark.parametrize("location", ["root", "parent", "database", "media", "journal"])
def test_rejects_symlinks_without_touching_target(tmp_path, location):
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    root = tmp_path / "archive"
    store = ArchiveStore(root)
    if location == "root":
        root.symlink_to(target, target_is_directory=True)
    elif location == "parent":
        (tmp_path / "alias").symlink_to(target, target_is_directory=True)
        store = ArchiveStore(tmp_path / "alias" / "archive")
    else:
        root.mkdir(mode=0o700)
        if location == "media":
            store.media_root.symlink_to(target, target_is_directory=True)
        else:
            target_file = target / "target.sqlite3"
            target_file.write_bytes(b"untouched")
            path = store.db_path if location == "database" else Path(str(store.db_path) + "-journal")
            path.symlink_to(target_file)
    before = {p.name: p.read_bytes() for p in target.iterdir() if p.is_file()}
    with pytest.raises(ValueError, match="unsafe_archive_path"):
        store.save_message(message())
    with pytest.raises(ValueError, match="unsafe_archive_path"):
        store.list_messages(SCOPE)
    assert {p.name: p.read_bytes() for p in target.iterdir() if p.is_file()} == before


def test_rejects_hard_linked_database(tmp_path):
    root = tmp_path / "archive"
    root.mkdir(mode=0o700)
    target = tmp_path / "unrelated.sqlite3"
    target.write_bytes(b"unrelated")
    target.chmod(0o600)
    os.link(target, root / "archive.sqlite3")
    with pytest.raises(ValueError, match="unsafe_archive_hardlink"):
        ArchiveStore(root).save_message(message())
    assert target.read_bytes() == b"unrelated"


def test_rejects_unrelated_database_and_keeps_contents(tmp_path):
    root = tmp_path / "archive"
    root.mkdir(mode=0o700)
    database = root / "archive.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE unrelated (value TEXT)")
        connection.execute("INSERT INTO unrelated VALUES ('untouched')")
    database.chmod(0o600)
    original = database.read_bytes()
    store = ArchiveStore(root)
    with pytest.raises(ValueError, match="not_a_furniture_archive"):
        store.save_message(message())
    with pytest.raises(ValueError, match="not_a_supported_furniture_archive"):
        store.status(SCOPE)
    assert database.read_bytes() == original


def test_rejects_shared_archive_directory_without_changing_permissions(tmp_path):
    root = tmp_path / "public"
    root.mkdir(mode=0o755)
    with pytest.raises(ValueError, match="archive_permissions_not_private"):
        ArchiveStore(root).save_message(message())
    assert stat.S_IMODE(root.stat().st_mode) == 0o755
    assert not (root / "archive.sqlite3").exists()


@pytest.mark.parametrize("changed", [
    {"message_id": ""}, {"direction": "sideways"}, {"sent_at": -1}, {"sent_at": True},
    {"received_at": "2026-01-01T00:00:00"}, {"text": "x" * 100_001},
])
def test_invalid_message_does_not_initialize_archive(tmp_path, changed):
    store = ArchiveStore(tmp_path / "archive")
    with pytest.raises(ValueError):
        store.save_message({**message(), **changed})
    assert not store.root.exists()


@pytest.mark.parametrize("filename", ["../secret", "bad\\name", "bad\nname", ".", ".."])
def test_rejects_unsafe_attachment_display_name_before_initialization(tmp_path, filename):
    store = ArchiveStore(tmp_path / "archive")
    with pytest.raises(ValueError, match="invalid_attachment_filename"):
        store.save_message(message(), {**attachment(), "filename": filename})
    assert not store.root.exists()


def test_rejects_path_traversal_metadata_and_unfiltered_errors(tmp_path):
    store = ArchiveStore(tmp_path / "archive")
    item = attachment()
    store.save_message(message(), item)
    claim = store.claim_attachment(SCOPE)
    with pytest.raises(ValueError, match="invalid_local_media_name"):
        finish(store, claim, local_name="../secret")
    with pytest.raises(ValueError, match="invalid_media_error_code"):
        store.fail_attachment(item["id"], "https://private.example.invalid/token", claim_token=claim["claim_token"])
    assert store.get_attachment(item["id"], SCOPE)["state"] == "processing"


@pytest.mark.parametrize("args", [{"after_id": -1}, {"after_id": "1"}, {"limit": 0}, {"limit": 101}])
def test_rejects_unbounded_pagination(tmp_path, args):
    with pytest.raises(ValueError, match="invalid_message_pagination"):
        ArchiveStore(tmp_path / "archive").list_messages(SCOPE, **args)
