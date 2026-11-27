"""Накладная Coca-Cola 27.11.2026: бутылки не должны превращаться в литры,
а подсказка «прошлая цена» не должна брать цену другого товара с тем же ID."""
from unittest.mock import patch

import pytest

import web_app
from web_app import normalize_supply_item_measurements

USER_ID = 777002


def _norm(item, item_type='product'):
    qty, price, unit, *_ = normalize_supply_item_measurements(
        item, item['name'], item_type=item_type)
    return round(qty, 3), round(price, 2), unit


@pytest.mark.parametrize('name, qty, total, expected_price', [
    ('Кока-кола 0.5л', 120, 43789.20, 364.91),        # 5 × 24 бутылки
    ('Фанта ананас ж/б 0.45л', 12, 4378.92, 364.91),  # 1 × 12 банок
    ('Фьюс чай манго-ананас 0.5л', 12, 3898.44, 324.87),
    ('Кока-кола 1л', 96, 50494.08, 525.98),
])
def test_product_bottles_stay_in_pieces(name, qty, total, expected_price):
    item = {'name': name, 'qty': qty, 'price': round(total / qty, 2), 'sum': total, 'unit': 'шт'}
    assert _norm(item) == (qty, expected_price, 'шт')


def test_product_already_converted_to_litres_goes_back_to_bottles():
    item = {'name': 'Кока-кола 0.5л', 'qty': 60, 'price': 729.82, 'sum': 43789.20, 'unit': 'л',
            'original_qty': 120, 'original_price': 364.91, 'original_unit': 'шт',
            'pack_size': 0.5, 'target_unit': 'л'}
    assert _norm(item) == (120, 364.91, 'шт')


def test_product_box_of_bottles_still_expands_to_pieces():
    item = {'name': 'Кока-кола 1л', 'qty': 96, 'price': 525.98, 'sum': 50494.08, 'unit': 'шт',
            'original_qty': 8, 'original_price': 6311.76, 'original_unit': 'уп',
            'pack_size': 12, 'target_unit': 'шт'}
    assert _norm(item) == (96, 525.98, 'шт')


def test_ingredient_canister_size_still_converts_to_litres():
    item = {'name': 'Масло фритюрное 10л', 'qty': 3, 'price': 9000, 'sum': 27000, 'unit': 'шт'}
    assert _norm(item, item_type='ingredient') == (30, 900, 'л')


@pytest.fixture
def client(tmp_path, monkeypatch):
    import config
    import database
    if database.DB_TYPE != 'sqlite':
        pytest.skip('runs on SQLite only')
    monkeypatch.setattr(config, 'DATABASE_PATH', tmp_path / 'users.db')
    db = database.UserDatabase()
    # Товар 15 «Кока-Кола 1л» и ингредиент 15 — разные позиции с одним ID
    db.bulk_add_price_history(USER_ID, [
        {'ingredient_id': 15, 'ingredient_name': 'Какой-то ингредиент', 'supplier_id': 1,
         'supplier_name': 'X', 'price': 132, 'quantity': 1, 'unit': 'кг', 'supply_id': 2,
         'date': '2026-11-20'},
        {'ingredient_id': 15, 'ingredient_name': 'Кока-Кола 1л', 'supplier_id': 6,
         'supplier_name': 'Кока-кола', 'price': 520, 'quantity': 96, 'unit': 'шт', 'supply_id': 1,
         'date': '2026-11-13'},
    ])
    web_app.app.config['TESTING'] = True
    with patch('web_app.get_database', return_value=db):
        with web_app.app.test_client() as c:
            with c.session_transaction() as sess:
                sess['telegram_user_id'] = USER_ID
                sess['web_user_id'] = 1
                sess['role'] = 'owner'
            yield c


def test_price_history_filtered_by_item_name(client):
    r = client.get('/api/items/price-history/15?name=Кока-Кола 1л')
    assert r.status_code == 200
    assert [float(h['price']) for h in r.json['history']] == [520]


def test_price_history_without_name_keeps_old_behaviour(client):
    r = client.get('/api/items/price-history/15')
    assert [float(h['price']) for h in r.json['history']] == [132, 520]
