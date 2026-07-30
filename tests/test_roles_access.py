import pytest
from unittest.mock import patch, MagicMock
from web_app import app, check_role_access, get_home_for_role, resolve_cafe_info, resolve_cashier_info


@pytest.fixture
def client():
    app.config['TESTING'] = True
    app.config['SECRET_KEY'] = 'test_secret_key'
    with app.test_client() as client:
        yield client


def test_get_home_for_role():
    assert get_home_for_role('owner') == '/'
    assert get_home_for_role('admin') == '/cafe/shift-closing'
    assert get_home_for_role('cashier') == '/cashier/shift-closing'
    assert get_home_for_role('unknown') == '/'


def test_check_role_access_owner():
    assert check_role_access('/settings/access', 'owner') is True
    assert check_role_access('/cafe/shift-closing', 'owner') is True
    assert check_role_access('/cashier/shift-closing', 'owner') is True
    assert check_role_access('/expenses', 'owner') is True


def test_check_role_access_admin():
    assert check_role_access('/cafe/shift-closing', 'admin') is True
    assert check_role_access('/api/cafe/poster-data', 'admin') is True
    assert check_role_access('/logout', 'admin') is True
    assert check_role_access('/shift-closing', 'admin') is True

    # Forbidden for admin
    assert check_role_access('/settings/access', 'admin') is False
    assert check_role_access('/cashier/shift-closing', 'admin') is False
    assert check_role_access('/expenses', 'admin') is False


def test_check_role_access_cashier():
    assert check_role_access('/cashier/shift-closing', 'cashier') is True
    assert check_role_access('/api/cashier/salaries/calculate', 'cashier') is True
    assert check_role_access('/logout', 'cashier') is True
    assert check_role_access('/shift-closing', 'cashier') is True

    # Forbidden for cashier
    assert check_role_access('/settings/access', 'cashier') is False
    assert check_role_access('/cafe/shift-closing', 'cashier') is False
    assert check_role_access('/expenses', 'cashier') is False


def test_resolve_cafe_info_with_account(client):
    mock_db = MagicMock()
    mock_db.get_web_user_poster_info.return_value = {
        'telegram_user_id': 12345,
        'poster_account_id': 2,
        'account_name': 'Pizzburg Cafe',
        'poster_token': 'token123',
        'poster_user_id': 'user1',
        'poster_base_url': 'https://cafe.joinposter.com/api'
    }

    with app.test_request_context('/cafe/shift-closing'):
        from flask import session
        session['role'] = 'admin'
        session['web_user_id'] = 10
        session['poster_account_id'] = 2

        with patch('web_app.get_database', return_value=mock_db):
            info = resolve_cafe_info()
            assert info['account_name'] == 'Pizzburg Cafe'
            assert info['poster_token'] == 'token123'


def test_resolve_cafe_info_fallback_when_none(client):
    mock_db = MagicMock()
    mock_db.get_web_user_poster_info.return_value = None
    mock_db.get_accounts.return_value = [
        {'id': 1, 'account_name': 'Pizzburg Main', 'is_primary': True, 'poster_token': 'main_tok'},
        {'id': 2, 'account_name': 'Pizzburg Cafe', 'is_primary': False, 'poster_token': 'cafe_tok'}
    ]

    with app.test_request_context('/cafe/shift-closing'):
        from flask import session
        session['role'] = 'admin'
        session['web_user_id'] = 11
        session['telegram_user_id'] = 12345
        session['poster_account_id'] = None

        with patch('web_app.get_database', return_value=mock_db):
            info = resolve_cafe_info()
            assert info['poster_account_id'] == 2
            assert info['account_name'] == 'Pizzburg Cafe'
            assert info['poster_token'] == 'cafe_tok'


def test_resolve_cashier_info_fallback_when_none(client):
    mock_db = MagicMock()
    mock_db.get_web_user_poster_info.return_value = None
    mock_db.get_accounts.return_value = [
        {'id': 1, 'account_name': 'Pizzburg Main', 'is_primary': True, 'poster_token': 'main_tok'}
    ]

    with app.test_request_context('/cashier/shift-closing'):
        from flask import session
        session['role'] = 'cashier'
        session['web_user_id'] = 12
        session['telegram_user_id'] = 12345
        session['poster_account_id'] = None

        with patch('web_app.get_database', return_value=mock_db):
            info = resolve_cashier_info()
            assert info['poster_account_id'] == 1
            assert info['account_name'] == 'Pizzburg Main'
            assert info['poster_token'] == 'main_tok'


def test_db_web_user_lifecycle(tmp_path):
    import uuid
    from database import UserDatabase
    db = UserDatabase()

    suffix = uuid.uuid4().hex[:6]
    test_uid = int(uuid.uuid4().int % 100000000)
    admin_uname = f"admin_{suffix}"
    cashier_uname = f"cashier_{suffix}"

    # 1. Create owner telegram account
    db.create_user(telegram_user_id=test_uid, poster_token="token_test", poster_user_id=f"user_{suffix}")

    # 2. Create web users
    admin_id = db.create_web_user(
        telegram_user_id=test_uid,
        username=admin_uname,
        password="password123",
        role="admin",
        label="Test Admin",
        poster_account_id=None
    )
    assert admin_id is not None

    cashier_id = db.create_web_user(
        telegram_user_id=test_uid,
        username=cashier_uname,
        password="password456",
        role="cashier",
        label="Test Cashier",
        poster_account_id=None
    )
    assert cashier_id is not None

    # 3. Verify user authentication
    user = db.verify_web_user(admin_uname, "password123")
    assert user is not None
    assert user['username'] == admin_uname
    assert user['role'] == 'admin'

    # Incorrect password verification
    invalid_user = db.verify_web_user(admin_uname, "wrong_pass")
    assert invalid_user is None

    # 4. List users
    users = db.list_web_users(test_uid)
    assert len(users) >= 2

    # 5. Reset password
    assert db.reset_web_user_password(admin_id, test_uid, "new_password_789") is True
    assert db.verify_web_user(admin_uname, "new_password_789") is not None

    # 6. Update user
    assert db.update_web_user(cashier_id, test_uid, label="Updated Cashier Name", is_active=1) is True
    c_user = db.get_web_user_by_id(cashier_id)
    assert c_user['label'] == "Updated Cashier Name"

    # 7. Delete user
    assert db.delete_web_user(cashier_id, test_uid) is True
    assert db.get_web_user_by_id(cashier_id) is None

    # Cleanup test admin user
    db.delete_web_user(admin_id, test_uid)
