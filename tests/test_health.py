def test_health_exposes_running_revision(monkeypatch):
    from web_app import app

    monkeypatch.setenv('RAILWAY_GIT_COMMIT_SHA', '1234567890abcdef')

    response = app.test_client().get('/health')

    assert response.status_code == 200
    assert response.data == b'ok'
    assert response.headers['X-App-Commit'] == '1234567890ab'
