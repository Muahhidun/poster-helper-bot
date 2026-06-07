"""Tests for department-specific smart packaging rules and price habits"""
import pytest
from tests.conftest import TEST_USER_ID

@pytest.fixture(autouse=True)
def cleanup_rules_and_habits(db):
    """Clean up rules and habits for the test user before and after the test"""
    # Delete test packaging rules by cleaning up the database or deleting specific entries
    rules = db.get_packaging_rules(TEST_USER_ID)
    for rule in rules:
        if rule['poster_ingredient_id'] in (8888, 8889):
            db.delete_packaging_rule_by_id(rule['id'], TEST_USER_ID)
            
    habits = db.get_ingredient_habits(TEST_USER_ID)
    for habit in habits:
        if habit['poster_ingredient_id'] in (8888, 8889):
            db.delete_ingredient_habit_by_id(habit['id'], TEST_USER_ID)
            
    yield
    
    rules = db.get_packaging_rules(TEST_USER_ID)
    for rule in rules:
        if rule['poster_ingredient_id'] in (8888, 8889):
            db.delete_packaging_rule_by_id(rule['id'], TEST_USER_ID)
            
    habits = db.get_ingredient_habits(TEST_USER_ID)
    for habit in habits:
        if habit['poster_ingredient_id'] in (8888, 8889):
            db.delete_ingredient_habit_by_id(habit['id'], TEST_USER_ID)


def test_department_specific_packaging_rules(db):
    """Test that packaging rules with different account names can coexist and be retrieved correctly"""
    # Add rules for the same ingredient id but different departments
    success1 = db.add_packaging_rule(
        telegram_user_id=TEST_USER_ID,
        poster_ingredient_id=8888,
        original_unit="шт",
        coefficient=0.5,
        target_unit="кг",
        notes="Rule for department A",
        account_name="Pizzburg"
    )
    assert success1 is True

    success2 = db.add_packaging_rule(
        telegram_user_id=TEST_USER_ID,
        poster_ingredient_id=8888,
        original_unit="шт",
        coefficient=1.2,
        target_unit="кг",
        notes="Rule for department B",
        account_name="Pizzburg Cafe"
    )
    assert success2 is True

    # Retrieve rules and check properties
    rules = db.get_packaging_rules(TEST_USER_ID)
    test_rules = [r for r in rules if r['poster_ingredient_id'] == 8888]
    assert len(test_rules) == 2

    # Map rules by account name
    rules_by_acc = {r['account_name']: r for r in test_rules}
    assert "Pizzburg" in rules_by_acc
    assert "Pizzburg Cafe" in rules_by_acc

    assert abs(rules_by_acc["Pizzburg"]["coefficient"] - 0.5) < 0.001
    assert abs(rules_by_acc["Pizzburg Cafe"]["coefficient"] - 1.2) < 0.001


def test_department_specific_habits(db):
    """Test that price habits with different account names can coexist and be retrieved correctly"""
    # Add habits for the same ingredient id but different departments
    success1 = db.add_ingredient_habit(
        telegram_user_id=TEST_USER_ID,
        poster_ingredient_id=8889,
        default_price=1500.0,
        notes="Habit for department A",
        account_name="Pizzburg"
    )
    assert success1 is True

    success2 = db.add_ingredient_habit(
        telegram_user_id=TEST_USER_ID,
        poster_ingredient_id=8889,
        default_price=2500.0,
        notes="Habit for department B",
        account_name="Pizzburg Cafe"
    )
    assert success2 is True

    # Retrieve habits and check properties
    habits = db.get_ingredient_habits(TEST_USER_ID)
    test_habits = [h for h in habits if h['poster_ingredient_id'] == 8889]
    assert len(test_habits) == 2

    # Map habits by account name
    habits_by_acc = {h['account_name']: h for h in test_habits}
    assert "Pizzburg" in habits_by_acc
    assert "Pizzburg Cafe" in habits_by_acc

    assert abs(float(habits_by_acc["Pizzburg"]["default_price"]) - 1500.0) < 0.001
    assert abs(float(habits_by_acc["Pizzburg Cafe"]["default_price"]) - 2500.0) < 0.001
