"""Tests for assistant memory versioning: saves keep last 10 versions,
rollback restores a previous version."""
import json
import pytest
from tests.conftest import TEST_USER_ID


@pytest.fixture
def app_client():
    from web_app import app
    app.config['TESTING'] = True
    return app.test_client()


def _login(app_client):
    with app_client.session_transaction() as sess:
        sess['telegram_user_id'] = TEST_USER_ID
        sess['web_user_id'] = 1
        sess['role'] = 'owner'


def test_save_creates_version(db):
    """Each save archives the previous text as a version."""
    db.create_user(TEST_USER_ID, "mock_token", "1", "https://mock.joinposter.com/api")
    db.save_assistant_memory(TEST_USER_ID, "version-A")
    db.save_assistant_memory(TEST_USER_ID, "version-B")

    versions = db.get_assistant_memory_versions(TEST_USER_ID)
    assert len(versions) >= 1
    assert versions[0]['memory_text'] == 'version-A'

    current = db.get_assistant_memory(TEST_USER_ID)
    assert current == 'version-B'


def test_keeps_only_10_versions(db):
    """Versions beyond the 10th are pruned."""
    db.create_user(TEST_USER_ID, "mock_token", "1", "https://mock.joinposter.com/api")
    db.save_assistant_memory(TEST_USER_ID, "seed")
    for i in range(12):
        db.save_assistant_memory(TEST_USER_ID, f"v{i}")

    versions = db.get_assistant_memory_versions(TEST_USER_ID)
    assert len(versions) <= 10


def test_rollback_restores_version(db):
    """Rollback restores a chosen version and the old current becomes a new version."""
    db.create_user(TEST_USER_ID, "mock_token", "1", "https://mock.joinposter.com/api")
    db.save_assistant_memory(TEST_USER_ID, "original")
    db.save_assistant_memory(TEST_USER_ID, "overwritten")

    versions = db.get_assistant_memory_versions(TEST_USER_ID)
    target = [v for v in versions if v['memory_text'] == 'original']
    assert target

    ok = db.rollback_assistant_memory(TEST_USER_ID, target[0]['id'])
    assert ok

    current = db.get_assistant_memory(TEST_USER_ID)
    assert current == 'original'


def test_rollback_invalid_id(db):
    db.create_user(TEST_USER_ID, "mock_token", "1", "https://mock.joinposter.com/api")
    assert db.rollback_assistant_memory(TEST_USER_ID, 999999) is False


def test_versions_api_endpoint(app_client, db):
    db.create_user(TEST_USER_ID, "mock_token", "1", "https://mock.joinposter.com/api")
    _login(app_client)
    db.save_assistant_memory(TEST_USER_ID, "api-v1")
    db.save_assistant_memory(TEST_USER_ID, "api-v2")

    resp = app_client.get('/api/assistant/memory/versions')
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data['success'] is True
    assert len(data['versions']) >= 1


def test_rollback_api_endpoint(app_client, db):
    db.create_user(TEST_USER_ID, "mock_token", "1", "https://mock.joinposter.com/api")
    _login(app_client)
    db.save_assistant_memory(TEST_USER_ID, "rollback-orig")
    db.save_assistant_memory(TEST_USER_ID, "rollback-new")

    versions_resp = app_client.get('/api/assistant/memory/versions')
    versions = json.loads(versions_resp.data)['versions']
    target_id = versions[0]['id']

    resp = app_client.post('/api/assistant/memory/rollback',
                           data=json.dumps({'version_id': target_id}),
                           content_type='application/json')
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data['success'] is True
    assert data['memory_text'] == 'rollback-orig'
