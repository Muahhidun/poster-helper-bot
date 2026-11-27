"""Инарин и Япоша 27.11.2026: соус для пиццы, Цезарь (только в Кафе), соусницы."""
import pytest

from matchers import IngredientMatcher, normalize_ingredient_text

P, C = 'Pizzburg', 'Pizzburg-cafe'
ITEMS = [
    (80, 'Польпа (5кг) Мутти', P), (253, 'соус для донера готовый', P), (91, 'Майонез', P),
    (222, 'Соус сырный (на бургер) 1кг', P), (244, 'Багет для донера', P), (96, 'Пицца коробка', P),
    (101, 'Соус тары 30мл', P), (355, 'Майонез', C), (208, 'Соус Цезарь', C),
    (67, 'Соус тары 30мл', C), (68, 'Соус тары 50мл', C), (241, 'Томатный соус', C),
]
ALIASES = [
    ('пицца соус', 80), ('соус польпа', 80), ('польпа', 80),
    ('пицца соус chef gourmet со специями 4.1 кг', 80),
    ('майонез провонсаль 67% астория ведро 9,6 кг', 91),
    ('соус майонезный «сырный» 42% pro 1 кг короб 6 кг тз astoria', 222),
    ('багет с луком 180г', 244), ('коробки под пиццу 32×32', 96),
    ('соусница 30 мл, с совмещенной крышкой (1235п2) / аналог 2301008', 101),
    ('соусница 50 мл., с совмещенной крышкой (1246п) аналог 2301106', 68),
]


def _matcher(extra_aliases=()):
    m = IngredientMatcher.__new__(IngredientMatcher)
    m.telegram_user_id = None
    m.ingredients, m.names, m.aliases, m._name_to_info, m._id_entries = {}, {}, {}, {}, {}
    for item_id, name, account in ITEMS:
        info = {'id': item_id, 'name': name, 'unit': 'кг', 'account_name': account, 'type': 'ingredient'}
        key = normalize_ingredient_text(name)
        m.ingredients[(item_id, account)] = info
        m.names.setdefault(key, []).append((item_id, account))
        m._name_to_info[(key, account)] = info
        m._id_entries.setdefault(item_id, []).append(info)
    for text, item_id, *account in list(ALIASES) + list(extra_aliases):
        for info in m._id_entries[item_id]:
            if not account or info['account_name'] == account[0]:
                m.aliases.setdefault(normalize_ingredient_text(text), []).append((item_id, info['account_name']))
                break
    return m


def test_pizza_sauce_finds_pizza_alias_despite_word_ending():
    match = _matcher().match('Соус томатный для пиццы классический PIKADOR PRO', target_account=P)
    assert match[1] == 'Польпа (5кг) Мутти'


def test_preposition_alone_does_not_link_doner_sauce():
    match = _matcher().match('Соус томатный для пиццы классический PIKADOR PRO', target_account=P)
    assert match[1] != 'соус для донера готовый'


@pytest.mark.parametrize('wording', [
    'Соус майонезный «Цезарь Сырный» 42% PRO балк 1 кг',
    'Соус майонезный Цезарь сырный 42% PRO балк 1кг',
])
def test_remembered_cafe_only_caesar_beats_weak_pizzburg_guess(wording):
    remembered = [('соус майонезный «цезарь сырный» 42% pro балк 1 кг', 208, C)]
    match = _matcher(remembered).match(wording, target_account=P)
    assert (match[1], match[4]) == ('Соус Цезарь', C)


def test_same_item_in_both_departments_still_goes_to_pizzburg():
    match = _matcher().match('Соус тары 30мл', target_account=C)
    assert (match[1], match[4]) == ('Соус тары 30мл', P)


def test_different_size_is_not_treated_as_the_same_item():
    match = _matcher().match('соусница 50 мл.', target_account=P)
    assert match[1] == 'Соус тары 50мл'
