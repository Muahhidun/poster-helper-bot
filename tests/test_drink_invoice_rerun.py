"""Повторная отправка накладной Coca-Cola 27.11.2026: модель вернула ящики
(8 × 6311.76) и названия с «500мл»; товары без заведения шли как «Unknown»."""
from unittest.mock import patch

import pytest

import matchers
import web_app
from web_app import normalize_supply_item_measurements, _department_for_item

P = 'Pizzburg'
PRODUCTS = [(14, 'Кока-Кола 0,5л'), (15, 'Кока-Кола 1л'), (177, 'Фанта ЖБ'), (171, 'Фьюс жб'),
            (56, 'Фьюс чай 0,5л'), (55, 'Фьюс чай 1л'), (21, 'Спрайт 0,5л')]
ALIASES = [('кола 0.5л', 14), ('кола без сахара 1л', 15), ('кола пэт 1лх12', 15),
           ('фанта ананас ж/б 0.45л', 177), ('фьюс чай манго-ананас 0.5л', 56),
           ('фьюс чай персик 1л', 55), ('спрайт ж/б 0.45л', 21)]


@pytest.fixture
def products():
    m = matchers.ProductMatcher.__new__(matchers.ProductMatcher)
    m.telegram_user_id = None
    m.products, m.names, m.aliases, m._name_to_info, m._id_entries = {}, {}, {}, {}, {}
    for item_id, name in PRODUCTS:
        info = {'id': item_id, 'name': name, 'category': 'Напитки', 'unit': 'шт', 'account_name': P}
        key = matchers.normalize_product_text(name)
        m.products[(item_id, P)] = info
        m.names.setdefault(key, []).append((item_id, P))
        m._id_entries.setdefault(item_id, []).append(info)
    for text, item_id in ALIASES:
        m.aliases.setdefault(matchers.normalize_product_text(text), []).append((item_id, P))
    return m


@pytest.mark.parametrize('invoice_name, expected', [
    ('Кола ПЭТ 500мл', 'Кока-Кола 0,5л'),
    ('КОЛА ПЭТ 500МЛХ24', 'Кока-Кола 0,5л'),
    ('Фьюс Манго-Ананас ПЭТ 500мл', 'Фьюс чай 0,5л'),
    ('ФЬЮС МАНГ-АНАН ПЭТ 500М', 'Фьюс чай 0,5л'),
    ('Фьюс Персик ПЭТ 1л', 'Фьюс чай 1л'),
    ('КОКА-КОЛА ПЭТ 1ЛХ12 UTC', 'Кока-Кола 1л'),
    ('Фанта Ананас ж/б 450мл', 'Фанта ЖБ'),
    ('Спрайт ж/б 0.45л', 'Спрайт 0,5л'),
])
def test_drink_volume_decides_the_product(products, invoice_name, expected):
    assert products.match(invoice_name, target_account=P)[1] == expected


def test_millilitres_normalized_to_litres():
    assert matchers.normalize_product_text('Кола ПЭТ 500мл') == 'кола пэт 0.5л'
    assert matchers.normalize_product_text('КОКА-КОЛА ПЭТ 1ЛХ12') == 'кока-кола пэт 1л'


@pytest.mark.parametrize('qty, case_price, last_bottle_price, bottles, bottle_price', [
    (8, 6311.76, 525.98, 96, 525.98),
    (5, 8757.84, 364.91, 120, 364.91),
    (1, 4378.92, 364.91, 12, 364.91),
    (1, 3898.44, 324.87, 12, 324.87),
])
def test_cases_expanded_to_bottles_by_last_price(qty, case_price, last_bottle_price, bottles, bottle_price):
    item = {'name': 'Кока-Кола', 'qty': qty, 'price': case_price, 'sum': qty * case_price, 'unit': 'шт'}
    q, p, unit, *_ = normalize_supply_item_measurements(
        item, 'Кока-Кола 1л', item_type='product', reference_price=last_bottle_price)
    assert (round(q, 3), round(p, 2), unit) == (bottles, bottle_price, 'шт')


def test_price_rise_is_not_mistaken_for_a_case():
    item = {'name': 'Кока-Кола 1л', 'qty': 96, 'price': 600, 'sum': 57600, 'unit': 'шт'}
    q, p, *_ = normalize_supply_item_measurements(
        item, 'Кока-Кола 1л', item_type='product', reference_price=525.98)
    assert (q, p) == (96, 600)


def test_ingredients_are_not_case_expanded():
    item = {'name': 'Булочки', 'qty': 2, 'price': 3600, 'sum': 7200, 'unit': 'шт'}
    q, p, *_ = normalize_supply_item_measurements(
        item, 'Булочка', item_type='ingredient', reference_price=300)
    assert (q, p) == (2, 3600)


ACCOUNTS = [{'id': 1, 'account_name': 'Pizzburg', 'is_primary': True},
            {'id': 2, 'account_name': 'Pizzburg-cafe', 'is_primary': False}]


def test_unknown_department_falls_back_to_primary():
    assert _department_for_item(ACCOUNTS, 15, 'Unknown') == (1, 'Pizzburg')


def test_known_department_is_kept():
    assert _department_for_item(ACCOUNTS, 208, 'Pizzburg-cafe') == (2, 'Pizzburg-cafe')


def test_unmatched_item_gets_no_department():
    assert _department_for_item(ACCOUNTS, None, None) == (None, None)
