"""Regressions from the 25.11.2026 invoices: «огурцы равва» and frying oil 10 l canisters."""
import pytest

from matchers import IngredientMatcher, normalize_ingredient_text, missing_ingredient_tokens


@pytest.fixture
def cucumber_matcher():
    m = IngredientMatcher.__new__(IngredientMatcher)
    m.telegram_user_id = None
    m.ingredients, m.names, m.aliases, m._name_to_info, m._id_entries = {}, {}, {}, {}, {}

    def add(item_id, name, account='Pizzburg'):
        info = {'id': item_id, 'name': name, 'unit': 'кг', 'account_name': account, 'type': 'ingredient'}
        key = normalize_ingredient_text(name)
        m.ingredients[(item_id, account)] = info
        m.names.setdefault(key, []).append((item_id, account))
        m._name_to_info[(key, account)] = info
        m._id_entries.setdefault(item_id, []).append(info)

    add(7, 'Маринованные огурцы (2л-1кг)')
    add(118, 'Огурцы свежие Равва')
    add(200, 'Огурцы свежие')
    m.aliases = {'маринованные огурцы': [(7, 'Pizzburg')], 'огурцы маринованные': [(7, 'Pizzburg')]}
    return m


def test_all_invoice_words_beat_partial_match(cucumber_matcher):
    match = cucumber_matcher.match('огурцы равва', target_account='Pizzburg')
    assert match[1] == 'Огурцы свежие Равва'


def test_marinated_still_matches_marinated(cucumber_matcher):
    match = cucumber_matcher.match('огурцы маринованные', target_account='Pizzburg')
    assert match[1] == 'Маринованные огурцы (2л-1кг)'


def test_missing_tokens_tolerates_word_endings():
    assert missing_ingredient_tokens('огурцы равва', 'Огурцы свежие Равва') == 0
    assert missing_ingredient_tokens('огурцы равва', 'Маринованные огурцы') == 1
    assert missing_ingredient_tokens('помидор', 'Помидоры') == 0


OIL_RULE = {'coefficient': 10, 'original_unit': 'шт', 'target_unit': 'л'}


def _normalize(item, rule=None, learned=900):
    from web_app import normalize_supply_item_measurements
    qty, price, unit, *_ = normalize_supply_item_measurements(
        item, 'Фритюрное масло', matching_rule=rule, default_price=learned)
    return round(qty, 3), round(price, 2), unit


def test_correct_oil_conversion_is_untouched():
    item = {'name': 'Масло', 'qty': 3, 'price': 9000, 'sum': 27000, 'unit': 'шт'}
    assert _normalize(item, OIL_RULE) == (30, 900, 'л')


def test_oil_already_multiplied_by_model_is_repaired():
    # Модель «исправила» цену и вернула 300 л по 90 ₸ без метаданных фасовки
    item = {'name': 'Масло', 'qty': 300, 'price': 90, 'sum': 27000, 'unit': 'л'}
    assert _normalize(item) == (30, 900, 'л')


def test_oil_with_inflated_original_qty_is_repaired():
    # original_qty уже пересчитан (30), и pack_size применился второй раз
    item = {'name': 'Масло', 'qty': 300, 'price': 90, 'sum': 27000, 'unit': 'л',
            'original_qty': 30, 'original_price': 900, 'original_unit': 'шт',
            'pack_size': 10, 'target_unit': 'л'}
    assert _normalize(item, OIL_RULE) == (30, 900, 'л')


def test_ordinary_price_change_is_not_rescaled():
    item = {'name': 'Масло', 'qty': 30, 'price': 600, 'sum': 18000, 'unit': 'л'}
    assert _normalize(item) == (30, 600, 'л')


def test_no_learned_price_means_no_repair():
    item = {'name': 'Масло', 'qty': 300, 'price': 90, 'sum': 27000, 'unit': 'л'}
    assert _normalize(item, learned=None) == (300, 90, 'л')
