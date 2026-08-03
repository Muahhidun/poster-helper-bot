import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from database import UserDatabase


def test_db_account_snapshots_lifecycle(tmp_path):
    """Test saving and retrieving account balance snapshots for 15-day history."""
    db = UserDatabase()
    test_uid = 888123

    # Save snapshots
    assert db.save_account_balance_snapshot(test_uid, "2026-08-01", "total", 5000000.0, "Общий баланс") is True
    assert db.save_account_balance_snapshot(test_uid, "2026-08-02", "total", 5200000.0, "Общий баланс", net_change=200000.0) is True
    assert db.save_account_balance_snapshot(test_uid, "2026-08-02", "wolt", 197000.0, "Wolt") is True

    # Retrieve history
    history = db.get_account_balance_history(test_uid, "total", days=15)
    assert len(history) >= 2
    dates = [h['date'] for h in history]
    assert "2026-08-01" in dates
    assert "2026-08-02" in dates

    wolt_hist = db.get_account_balance_history(test_uid, "wolt", days=15)
    assert len(wolt_hist) >= 1
    assert wolt_hist[0]['balance'] == 197000.0


@pytest.fixture
def client():
    from web_app import app
    app.config['TESTING'] = True
    app.config['SECRET_KEY'] = 'test_secret_key'
    with app.test_client() as c:
        yield c


def test_accounts_route_access_control(client):
    """Test that /accounts and /api/accounts/summary require owner role."""
    # Unauthenticated -> redirect to login
    res = client.get('/accounts')
    assert res.status_code == 302
    assert '/login' in res.location

    # Admin role -> forbidden
    with client.session_transaction() as sess:
        sess['web_user_id'] = 100
        sess['telegram_user_id'] = 999999
        sess['username'] = 'admin_user'
        sess['role'] = 'admin'

    res = client.get('/accounts')
    assert res.status_code == 302

    res_api = client.get('/api/accounts/summary')
    assert res_api.status_code == 403

    # Cashier role -> forbidden
    with client.session_transaction() as sess:
        sess['role'] = 'cashier'

    res_api = client.get('/api/accounts/summary')
    assert res_api.status_code == 403

    # Owner role -> allowed (returns template / 200)
    with client.session_transaction() as sess:
        sess['role'] = 'owner'

    res_owner = client.get('/accounts')
    assert res_owner.status_code == 200
    assert 'Счета'.encode('utf-8') in res_owner.data


def test_api_accounts_summary_aggregation(client, monkeypatch):
    """Test accounts summary calculation with Wolt 30% discount and excluded accounts."""
    with client.session_transaction() as sess:
        sess['web_user_id'] = 1
        sess['telegram_user_id'] = 999999
        sess['username'] = 'owner_user'
        sess['role'] = 'owner'

    mock_fin_accs = [
        {'account_id': 1, 'name': 'Оставил в кассе на закупы', 'balance': 1000000},    # 10,000 Tenge
        {'account_id': 2, 'name': 'Kaspi Pay', 'balance': 10000000},                   # 100,000 Tenge
        {'account_id': 3, 'name': 'Halyk Bank', 'balance': 5000000},                   # 50,000 Tenge
        {'account_id': 4, 'name': 'Деньги дом (Жандос)', 'balance': 4000000},          # 40,000 Tenge
        {'account_id': 5, 'name': 'Деньги дом (Руслан)', 'balance': 3000000},          # 30,000 Tenge
        {'account_id': 6, 'name': 'Wolt', 'balance': 10000000},                        # Gross 100,000 Tenge -> Net 70,000 (-30%)
        {'account_id': 7, 'name': 'Денежный ящик (Кассира)', 'balance': 99999900},     # EXCLUDED
        {'account_id': 8, 'name': 'Инкассация (вечером)', 'balance': 88888800},         # EXCLUDED
        {'account_id': 9, 'name': 'Форте банк', 'balance': 77777700},                   # EXCLUDED
        {'account_id': 10, 'name': 'Прибыль', 'balance': 66666600}                      # EXCLUDED
    ]

    async def mock_get_accounts(self):
        return mock_fin_accs

    monkeypatch.setattr("poster_client.PosterClient.get_accounts", mock_get_accounts)

    res = client.get('/api/accounts/summary')
    assert res.status_code == 200
    data = res.get_json()
    assert data['success'] is True

    # Check Wolt Net balance (100,000 * 0.70 = 70,000)
    wolt_acc = next(a for a in data['accounts'] if a['key'] == 'wolt')
    assert wolt_acc['balance'] == 70000.0
    assert wolt_acc['gross_balance'] == 100000.0

    # Total Sum = 10,000 + 100,000 + 50,000 + 40,000 + 30,000 + 70,000 = 300,000
    assert data['total_sum'] == 300000.0

    # Ensure excluded accounts are NOT present
    keys = [a['key'] for a in data['accounts']]
    names = [a['name'] for a in data['accounts']]
    assert not any('Денежный ящик' in n for n in names)
    assert not any('Инкассация' in n for n in names)
    assert not any('Форте банк' in n for n in names)
    assert not any('Прибыль' in n for n in names)
