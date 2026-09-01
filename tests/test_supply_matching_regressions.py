"""Regression tests for item matching and exactly-once packaging."""

from tests.conftest import TEST_USER_ID


def test_product_match_does_not_use_only_a_shared_volume_token():
    from matchers import ProductMatcher

    assert ProductMatcher(TEST_USER_ID).match('Совершенно другой напиток 1л') is None


def test_fuse_spelling_and_drink_subcategory_are_supported(tmp_path, monkeypatch):
    import config
    from matchers import ProductMatcher

    (tmp_path / 'poster_products.csv').write_text(
        'product_id,product_name,category_name,account_name\n'
        '55,Фьюс чай 1л,Напитки,Pizzburg\n'
        '243,Фьюс 1л,Напитки Кола,Pizzburg-cafe\n',
        encoding='utf-8',
    )
    monkeypatch.setattr(config, 'DATA_DIR', tmp_path)
    matcher = ProductMatcher(None)

    assert matcher.match('Fuse Tea 1 литр')[:2] == (55, 'Фьюс чай 1л')
    assert matcher.match('Fuse Tea 1 литр', target_account='Pizzburg-cafe')[:2] == (
        243, 'Фьюс 1л'
    )


def test_database_ingredient_alias_is_loaded_and_used(db):
    from matchers import IngredientMatcher

    alias = 'тестовый соус для бургера'
    db.delete_ingredient_alias(TEST_USER_ID, alias)
    try:
        assert db.add_ingredient_alias(
            telegram_user_id=TEST_USER_ID,
            alias_text=alias,
            poster_item_id=249,
            poster_item_name='Бургерный соус',
            source='ingredient',
            notes='regression test',
        )
        match = IngredientMatcher(TEST_USER_ID).match(alias)
        assert match is not None
        assert match[0] == 249
        assert match[1] == 'Бургерный соус'
    finally:
        db.delete_ingredient_alias(TEST_USER_ID, alias)


def test_packaging_metadata_reconstructs_quantity_once_after_double_ai_multiply():
    from web_app import normalize_supply_item_measurements

    values = normalize_supply_item_measurements({
        'qty': 10000,
        'price': 0.73,
        'sum': 7300,
        'unit': 'шт',
        'original_qty': 1,
        'original_unit': 'уп',
        'original_price': 7300,
        'pack_size': 100,
        'target_unit': 'шт',
        'packaging_applied': True,
    }, 'Нори 100шт упк')

    qty, price, unit, parsed_qty, parsed_unit, parsed_price = values
    assert (qty, price, unit) == (100, 73, 'шт')
    assert (parsed_qty, parsed_unit, parsed_price) == (1, 'уп', 7300)


def test_packaging_metadata_fixes_double_tortilla_conversion():
    from web_app import normalize_supply_item_measurements

    qty, price, unit, *_ = normalize_supply_item_measurements({
        'qty': 432,
        'price': 9.4441666667,
        'sum': 4080,
        'unit': 'шт',
        'original_qty': 3,
        'original_unit': 'уп',
        'original_price': 1360,
        'pack_size': 12,
        'target_unit': 'шт',
        'packaging_applied': True,
    }, 'Тортилья сырная (12шт)')

    assert qty == 36
    assert round(price, 2) == 113.33
    assert unit == 'шт'


def test_already_normalized_item_is_not_changed_without_a_rule():
    from web_app import normalize_supply_item_measurements

    values = normalize_supply_item_measurements(
        {'qty': 100, 'price': 73, 'sum': 7300, 'unit': 'шт'},
        'Нори 100шт упк',
        default_price=73,
    )
    assert values[:3] == (100, 73, 'шт')


def test_supply_total_mismatch_is_reported_instead_of_rewriting_expense():
    from web_app import calculate_supply_total_mismatch

    items = [
        {'quantity': 10, 'price_per_unit': 1500},
        {'quantity': 2, 'price_per_unit': 2000},
    ]
    assert calculate_supply_total_mismatch(items, 20000) == (20000, 19000, -1000)


def test_supply_total_allows_small_rounding_difference():
    from web_app import calculate_supply_total_mismatch

    items = [{'quantity': 3, 'price_per_unit': 3333.33}]
    assert calculate_supply_total_mismatch(items, 10000) is None


def test_manual_supply_correction_does_not_create_future_rules(db):
    from web_app import app

    db.create_user(TEST_USER_ID, 'mock_token', '1', 'https://mock.joinposter.com/api')
    draft_id = db.create_empty_supply_draft(
        telegram_user_id=TEST_USER_ID,
        supplier_name='Тестовый поставщик',
        total_sum=2000,
    )
    item_id = db.add_supply_draft_item(
        supply_draft_id=draft_id,
        item_name='ошибочно распознанный соус',
        quantity=2,
        unit='кг',
        price_per_unit=1000,
        poster_ingredient_id=249,
        poster_ingredient_name='Бургерный соус',
        poster_account_name='Pizzburg',
        parsed_quantity=2,
        parsed_unit='кг',
        parsed_price_per_unit=1000,
    )

    client = app.test_client()
    with client.session_transaction() as session:
        session['telegram_user_id'] = TEST_USER_ID
        session['web_user_id'] = 1
        session['role'] = 'owner'

    try:
        response = client.post(f'/supplies/update-item/{item_id}', json={
            'poster_ingredient_id': 249,
            'poster_ingredient_name': 'Бургерный соус',
            'poster_account_name': 'Pizzburg',
            'quantity': 3,
            'price_per_unit': 900,
        })
        assert response.status_code == 200
        assert response.get_json()['success'] is True

        updated = db.get_supply_draft_item(item_id, telegram_user_id=TEST_USER_ID)
        assert updated['quantity'] == 3
        assert updated['price_per_unit'] == 900
        assert updated['total'] == 2700
        assert all(
            alias['alias_text'] != 'ошибочно распознанный соус'
            for alias in db.get_ingredient_aliases(TEST_USER_ID)
        )
        assert all(
            habit['poster_ingredient_id'] != 249
            or habit.get('notes') != 'Изучено из ручного ввода цены'
            for habit in db.get_ingredient_habits(TEST_USER_ID)
        )
    finally:
        db.delete_supply_draft(draft_id, telegram_user_id=TEST_USER_ID)


def test_legacy_auto_learned_corrections_are_removed(db):
    alias_text = 'временный авто алиас'
    db.delete_ingredient_alias(TEST_USER_ID, alias_text)
    db.add_ingredient_alias(
        TEST_USER_ID, alias_text, 249, 'Бургерный соус', 'ingredient',
        'Авто-сохранено при ручной привязке в Поставках',
    )
    db.add_packaging_rule(
        TEST_USER_ID, 249, 'кг', 1.5, 'кг',
        'Авто-изучено: 2 кг -> 3 кг', 'Pizzburg',
    )
    db.add_ingredient_habit(
        TEST_USER_ID, 249, 900, None,
        'Изучено из ручного ввода цены', 'Pizzburg',
    )

    db._remove_auto_learned_corrections()

    assert all(a['alias_text'] != alias_text for a in db.get_ingredient_aliases(TEST_USER_ID))
    assert all(
        rule.get('notes') != 'Авто-изучено: 2 кг -> 3 кг'
        for rule in db.get_packaging_rules(TEST_USER_ID)
    )
    assert all(
        habit.get('notes') != 'Изучено из ручного ввода цены'
        for habit in db.get_ingredient_habits(TEST_USER_ID)
    )
