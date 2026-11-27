"""Накладная Япоши 27.11.2026: «Мука высшего сорта 10 кг», 50 кг × 340 = 17 000.
Сайт превращал это в 500 кг × 34, умножая килограммы на фасовку 10 кг."""
from unittest.mock import patch

import pytest

import web_app
from web_app import normalize_supply_item_measurements

FLOUR = 'Мука высшего сорта 10 кг'


def _norm(item, **kw):
    qty, price, unit, *_ = normalize_supply_item_measurements(item, 'Мука Гранум', **kw)
    return round(qty, 3), round(price, 2), unit


def test_kilograms_are_not_multiplied_by_bag_size():
    item = {'name': FLOUR, 'qty': 500, 'price': 34, 'sum': 17000, 'unit': 'кг',
            'original_qty': 50, 'original_unit': 'кг', 'original_price': 340,
            'pack_size': 10, 'target_unit': 'кг'}
    assert _norm(item) == (50, 340, 'кг')


def test_inflated_kilograms_repaired_by_last_supply_price():
    # Модель сама умножила и не оставила метаданных фасовки
    item = {'name': FLOUR, 'qty': 500, 'price': 34, 'sum': 17000, 'unit': 'кг'}
    assert _norm(item, reference_price=340) == (50, 340, 'кг')


def test_metro_bags_still_convert_to_kilograms():
    # Метро: «Мука 10 кг», 5 шт по 3400
    item = {'name': FLOUR, 'qty': 5, 'price': 3400, 'sum': 17000, 'unit': 'шт'}
    assert _norm(item) == (50, 340, 'кг')


def test_metro_bags_with_metadata_still_convert():
    item = {'name': FLOUR, 'qty': 50, 'price': 340, 'sum': 17000, 'unit': 'кг',
            'original_qty': 5, 'original_unit': 'шт', 'original_price': 3400,
            'pack_size': 10, 'target_unit': 'кг'}
    assert _norm(item) == (50, 340, 'кг')


def test_real_price_change_is_not_rescaled():
    item = {'name': FLOUR, 'qty': 50, 'price': 300, 'sum': 15000, 'unit': 'кг'}
    assert _norm(item, reference_price=340) == (50, 300, 'кг')


@pytest.fixture
def db(tmp_path, monkeypatch):
    import config
    import database
    if database.DB_TYPE != 'sqlite':
        pytest.skip('runs on SQLite only')
    monkeypatch.setattr(config, 'DATABASE_PATH', tmp_path / 'users.db')
    return database.UserDatabase()


def test_draft_uses_last_supply_price_of_the_same_item(db):
    user_id = 777003
    db.bulk_add_price_history(user_id, [
        # Другой товар с тем же ID не должен влиять
        {'ingredient_id': 5, 'ingredient_name': 'Кока-Кола 0,5л', 'supplier_id': 6,
         'supplier_name': 'Кока-кола', 'price': 3.4, 'quantity': 1, 'unit': 'шт',
         'supply_id': 2, 'date': '2026-11-26'},
        {'ingredient_id': 5, 'ingredient_name': 'Мука Гранум', 'supplier_id': 1,
         'supplier_name': 'Метро', 'price': 340, 'quantity': 50, 'unit': 'кг',
         'supply_id': 1, 'date': '2026-09-01'},
    ])
    draft_id = db.create_empty_supply_draft(
        telegram_user_id=user_id, supplier_name='Япоша', invoice_date='2026-11-27',
        total_sum=17000, linked_expense_draft_id=None, source='kaspi')
    match = (5, 'Мука Гранум', 'кг', 'ingredient', 'Pizzburg')
    with patch('web_app.resolve_item_match', return_value=match), \
         patch('matchers.get_ingredient_matcher', return_value=None), \
         patch('matchers.get_product_matcher', return_value=None):
        web_app._add_items_to_supply_draft(db, user_id, draft_id, [
            {'name': FLOUR, 'qty': 500, 'price': 34, 'sum': 17000, 'unit': 'кг'}])
    items = db.get_supply_draft_with_items(draft_id)['items']
    assert [(float(i['quantity']), float(i['price_per_unit'])) for i in items] == [(50, 340)]
