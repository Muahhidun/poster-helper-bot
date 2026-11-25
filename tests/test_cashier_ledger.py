"""Тетрадь расходов кассира: закупные деньги → касса, API и права."""
from unittest.mock import patch

import pytest

import web_app
from web_app import _build_cashier_ledger

USER_ID = 777001
ACCOUNT_ID = 1
OTHER_ACCOUNT_ID = 2


def _drafts(*amounts):
    return [{'id': i + 1, 'amount': a, 'description': f'r{i}', 'status': 'pending',
             'completion_status': 'pending'} for i, a in enumerate(amounts)]


def test_ledger_splits_float_then_register():
    # Пример из тетради: оставили 56 550, расходы по порядку
    ledger = _build_cashier_ledger(56550, _drafts(23500, 3000, 10500, 8359, 27360))
    assert ledger['total'] == 72719
    assert ledger['from_float'] == 56550
    assert ledger['float_left'] == 0
    assert ledger['from_register'] == 16169
    buckets = [e['bucket'] for e in ledger['entries']]
    assert buckets == ['float', 'float', 'float', 'float', 'split']
    last = ledger['entries'][-1]
    assert last['from_float'] == 11191 and last['from_register'] == 16169


def test_ledger_float_not_spent():
    ledger = _build_cashier_ledger(50000, _drafts(10000, 5000))
    assert ledger['from_register'] == 0
    assert ledger['float_left'] == 35000


def test_ledger_without_float_counts_all_from_register():
    ledger = _build_cashier_ledger(None, _drafts(1000, 2000))
    assert ledger['from_register'] == 3000
    assert [e['bucket'] for e in ledger['entries']] == ['register', 'register']


def test_ledger_marks_processed_entries_locked():
    drafts = _drafts(1000)
    drafts[0]['completion_status'] = 'completed'
    assert _build_cashier_ledger(0, drafts)['entries'][0]['locked'] is True


@pytest.fixture
def db(tmp_path, monkeypatch):
    import config
    import database
    if database.DB_TYPE != 'sqlite':
        pytest.skip('ledger API test runs on SQLite only')
    monkeypatch.setattr(config, 'DATABASE_PATH', tmp_path / 'users.db')
    return database.UserDatabase()


@pytest.fixture
def client(db):
    web_app.app.config['TESTING'] = True
    info = {'telegram_user_id': USER_ID, 'poster_account_id': ACCOUNT_ID, 'account_name': 'Pizzburg'}
    with patch('web_app.get_database', return_value=db), \
         patch('web_app.resolve_cashier_info', return_value=info), \
         patch('web_app._cashier_business_date', return_value='2026-11-25'):
        with web_app.app.test_client() as c:
            with c.session_transaction() as sess:
                sess['telegram_user_id'] = USER_ID
                sess['web_user_id'] = 5
                sess['role'] = 'cashier'
            yield c


def test_cashier_ledger_flow(client, db):
    r = client.post('/api/cashier/expenses/float', json={'amount': '56550'})
    assert r.status_code == 200 and r.json['purchase_float'] == 56550

    for amount, desc in [(23500, 'Фарш'), (3000, 'Зарядка'), (10500, 'Весы'),
                         (8359, 'Кюрдамир'), (27360, 'Алимжан')]:
        r = client.post('/api/cashier/expenses', json={'amount': amount, 'description': desc})
        assert r.status_code == 200, r.json

    data = client.get('/api/cashier/expenses').json
    assert data['from_register'] == 16169
    assert [e['description'] for e in data['entries']] == ['Фарш', 'Зарядка', 'Весы', 'Кюрдамир', 'Алимжан']

    # Черновики видны владельцу в «Наличке» за эту дату
    drafts = [d for d in db.get_expense_drafts(USER_ID, status='all')
              if str(d['created_at'])[:10] == '2026-11-25']
    assert len(drafts) == 5
    assert all(d['source'] == 'cash' and d['created_by'] == 'cashier'
               and d['poster_account_id'] == ACCOUNT_ID for d in drafts)

    # Исправить сумму, потом удалить
    весы = next(e for e in data['entries'] if e['description'] == 'Весы')
    r = client.put(f"/api/cashier/expenses/{весы['id']}", json={'amount': 9500})
    assert r.status_code == 200 and r.json['from_register'] == 15169
    r = client.delete(f"/api/cashier/expenses/{весы['id']}")
    assert r.status_code == 200 and len(r.json['entries']) == 4


def test_cashier_cannot_touch_processed_or_foreign_entries(client, db):
    client.post('/api/cashier/expenses', json={'amount': 1000, 'description': 'Хлеб'})
    entry = client.get('/api/cashier/expenses').json['entries'][0]

    db.update_expense_draft(entry['id'], completion_status='completed')
    assert client.delete(f"/api/cashier/expenses/{entry['id']}").status_code == 409
    assert client.put(f"/api/cashier/expenses/{entry['id']}", json={'amount': 5}).status_code == 409

    # Обычный расход владельца кассиру не виден и недоступен
    owner_draft = db.create_expense_draft(USER_ID, amount=500, description='Интернет',
                                          poster_account_id=ACCOUNT_ID, created_at='2026-11-25')
    assert all(e['id'] != owner_draft for e in client.get('/api/cashier/expenses').json['entries'])
    assert client.delete(f'/api/cashier/expenses/{owner_draft}').status_code == 404


def test_cashier_cannot_delete_entry_owner_turned_into_supply(client, db):
    client.post('/api/cashier/expenses', json={'amount': 8359, 'description': 'Кюрдамир'})
    entry = client.get('/api/cashier/expenses').json['entries'][0]
    db.update_expense_draft(entry['id'], expense_type='supply')
    assert client.delete(f"/api/cashier/expenses/{entry['id']}").status_code == 409


def test_cashier_ledger_validation(client):
    assert client.post('/api/cashier/expenses', json={'amount': 0, 'description': 'x'}).status_code == 400
    assert client.post('/api/cashier/expenses', json={'amount': 100, 'description': '  '}).status_code == 400
    assert client.post('/api/cashier/expenses/float', json={'amount': -5}).status_code == 400
