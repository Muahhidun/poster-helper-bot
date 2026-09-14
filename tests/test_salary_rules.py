from unittest.mock import AsyncMock, patch

import pytest

from cafe_salary import (
    calculate_cafe_cashier_salary,
    calculate_cafe_sushi_salary,
    calculate_roll_equivalents,
    is_yuri_name,
)
from cashier_salary import CashierSalaryCalculator
from doner_salary import DonerSalaryCalculator
from tests.conftest import TEST_USER_ID


@pytest.mark.parametrize("doner_count", [0, 199, 200, 239, 240, 259, 279])
def test_doner_salary_minimum_is_fourteen_thousand(doner_count):
    assert DonerSalaryCalculator(TEST_USER_ID).calculate_salary(doner_count) == 14_000


def test_doner_salary_norms_continue_above_minimum():
    calculator = DonerSalaryCalculator(TEST_USER_ID)
    assert calculator.calculate_salary(280) == 15_000
    assert calculator.calculate_salary(300) == 16_000


@pytest.mark.parametrize("cashier_count", [2, 3])
def test_main_cashier_salary_minimum_is_eight_thousand(cashier_count):
    calculator = CashierSalaryCalculator(TEST_USER_ID)
    assert calculator.calculate_salary(0, cashier_count) == 8_000
    assert calculator.calculate_salary(50_000_000, cashier_count) == 8_000


@pytest.mark.parametrize(
    ("revenue", "expected"),
    [
        (0, 9_000),
        (150_000, 9_000),
        (150_001, 9_500),
        (200_000, 9_500),
        (200_001, 10_000),
        (241_000, 10_000),
        (250_001, 10_500),
    ],
)
def test_cafe_cashier_salary_uses_started_fifty_thousand_bands(revenue, expected):
    assert calculate_cafe_cashier_salary(revenue) == expected


@pytest.mark.parametrize("name", ["Юрий", "Юра", "Юрик", "Yuri", "Yuriy", "Юрий П."])
def test_yuri_name_variants(name):
    assert is_yuri_name(name) is True


def test_cafe_sushi_salary_is_variable_only_for_yuri():
    assert calculate_cafe_sushi_salary("Юра", 3.5) == 15_175
    assert calculate_cafe_sushi_salary("Юра", 17.76) == 15_900
    assert calculate_cafe_sushi_salary("Бауржан", 100) == 14_000


def test_rolls_half_rolls_and_sets_are_converted_to_full_roll_equivalents():
    categories = [
        {"category_id": 1, "category_name": "Роллы"},
        {"category_id": 2, "category_name": "Сеты"},
        {"category_id": 3, "category_name": "Напитки"},
        {"category_id": 4, "category_name": "Роллы 4 шт"},
    ]
    sales = [
        {"product_name": "Филадельфия", "category_id": 1, "count": 2},
        {"product_name": "Калифорния 4 шт", "category_id": 1, "count": 3},
        {"product_name": "Лава", "category_id": 4, "count": 2},
        {"product_name": "Сет 24 шт", "category_id": 2, "count": 1},
        {"product_name": "Сет 32 ролла", "category_id": 2, "count": 2},
        {"product_name": "Кола", "category_id": 3, "count": 50},
    ]

    equivalents, details, warnings = calculate_roll_equivalents(sales, categories)

    assert equivalents == pytest.approx(15.5)
    assert len(details) == 5
    assert warnings == []


def test_set_without_piece_count_is_not_silently_counted():
    equivalents, details, warnings = calculate_roll_equivalents(
        [{"product_name": "Сет Семейный", "category_id": 2, "count": 1}],
        [{"category_id": 2, "category_name": "Сеты"}],
    )
    assert equivalents == 0
    assert details == []
    assert "Сет Семейный" in warnings[0]


def test_sushi_gunkan_and_onigiri_salary_equivalents():
    categories = [
        {"category_id": 1, "category_name": "Суши"},
        {"category_id": 2, "category_name": "Гунканы"},
        {"category_id": 3, "category_name": "Анигири"},
    ]
    sales = [
        {"product_name": "Суши Сяке", "category_id": 1, "count": 2},
        {"product_name": "Гункан с угрём", "category_id": 2, "count": 3},
        {"product_name": "Анигири с лососем", "category_id": 3, "count": 2},
    ]

    equivalents, details, warnings = calculate_roll_equivalents(sales, categories)

    assert equivalents == 4.5
    assert [detail["kind"] for detail in details] == ["sushi", "gunkan", "onigiri"]
    assert [detail["rolls_per_sale"] for detail in details] == [0.5, 0.5, 1.0]
    assert [detail["yuri_bonus"] for detail in details] == [50.0, 75.0, 100.0]
    assert warnings == []
    assert calculate_cafe_sushi_salary("Юрий", equivalents) == 15_225


def test_generic_sushi_category_does_not_turn_rolls_into_piece_sushi():
    categories = [{"category_id": 1, "category_name": "Суши"}]
    sales = [
        {"product_name": "Дракон-Фила Лайт 50/50 - 4шт", "category_id": 1, "count": 1},
        {"product_name": "Капа Маки - 8шт (2026)", "category_id": 1, "count": 2},
        {"product_name": "Суши Ассорти - 3шт", "category_id": 1, "count": 1},
        {"product_name": "Суши с Креветкой - 1шт", "category_id": 1, "count": 4},
    ]

    equivalents, details, warnings = calculate_roll_equivalents(sales, categories)

    assert [detail["kind"] for detail in details] == ["roll", "roll", "sushi", "sushi"]
    assert [detail["roll_equivalents"] for detail in details] == [0.5, 2.0, 1.5, 2.0]
    assert equivalents == 6.0
    assert warnings == []


@pytest.mark.parametrize(
    ("product_name", "expected_equivalents", "expected_pieces"),
    [
        ("Ролл авторский 10 шт", 1, 8),
        ("Ролл авторский 13 шт", 2, 16),
        ("Сет фирменный 17 шт", 2, 16),
        ("Ролл половинка 4 шт", 0.5, 4),
    ],
)
def test_nonstandard_piece_counts_round_to_salary_portions(
    product_name, expected_equivalents, expected_pieces
):
    category = "Сеты" if product_name.startswith("Сет") else "Роллы"
    equivalents, details, warnings = calculate_roll_equivalents(
        [{"product_name": product_name, "category_id": 1, "count": 1}],
        [{"category_id": 1, "category_name": category}],
    )

    assert equivalents == expected_equivalents
    assert details[0]["normalized_pieces"] == expected_pieces
    assert warnings == []


def test_cafe_salary_calculate_endpoint_returns_suggestions():
    from web_app import app

    app.config["TESTING"] = True
    client = app.test_client()
    with client.session_transaction() as session:
        session["telegram_user_id"] = TEST_USER_ID
        session["web_user_id"] = 1
        session["role"] = "owner"

    result = {
        "revenue": 241_000,
        "cashier_salary": 10_000,
        "sushi_salary": 15_175,
        "povar_salary": 10_000,
        "roll_equivalents": 3.5,
        "roll_details": [],
        "warnings": [],
        "is_yuri": True,
    }
    mock_info = {
        "telegram_user_id": TEST_USER_ID,
        "poster_account_id": 2,
        "poster_token": "token",
        "poster_user_id": "user",
        "poster_base_url": "https://example.test/api",
    }

    with patch("web_app.resolve_cafe_info", return_value=mock_info), patch(
        "cafe_salary.CafeSalaryCalculator.calculate",
        new=AsyncMock(return_value=result),
    ) as calculate:
        response = client.post(
            "/api/cafe/salaries/calculate",
            json={"date": "2026-09-14", "sushi_name": "Юрий"},
        )

    assert response.status_code == 200
    assert response.get_json()["cashier_salary"] == 10_000
    calculate.assert_awaited_once_with(date="20260914", sushi_name="Юрий")
