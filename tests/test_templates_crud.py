"""Tests for shipment templates database CRUD operations"""
import pytest

from tests.conftest import TEST_USER_ID


@pytest.fixture(autouse=True)
def cleanup_templates(db):
    """Clean up test templates before and after each test"""
    db.delete_shipment_template(TEST_USER_ID, "тестовый лаваш")
    db.delete_shipment_template(TEST_USER_ID, "айран")
    yield
    db.delete_shipment_template(TEST_USER_ID, "тестовый лаваш")
    db.delete_shipment_template(TEST_USER_ID, "айран")


def _create_lavash_template(db):
    """Helper to create a test lavash template"""
    return db.create_shipment_template(
        telegram_user_id=TEST_USER_ID,
        template_name="тестовый лаваш",
        supplier_id=1,
        supplier_name="Тестовый поставщик",
        account_id=2,
        account_name="Тестовый счет",
        items=[{"id": 1, "name": "Лаваш", "price": 40}],
        storage_id=1,
    )


def test_create_template(db):
    """Test creating a shipment template"""
    success = _create_lavash_template(db)
    assert success is True


def test_get_templates(db):
    """Test listing templates for a user"""
    _create_lavash_template(db)
    templates = db.get_shipment_templates(TEST_USER_ID)
    assert len(templates) >= 1
    names = [t['template_name'] for t in templates]
    assert "тестовый лаваш" in names


def test_get_single_template(db):
    """Test retrieving a single template by name"""
    _create_lavash_template(db)
    template = db.get_shipment_template(TEST_USER_ID, "тестовый лаваш")
    assert template is not None
    assert template['template_name'] == "тестовый лаваш"
    assert template['supplier_name'] == "Тестовый поставщик"
    assert template['account_name'] == "Тестовый счет"
    assert len(template['items']) == 1
    assert template['items'][0]['name'] == "Лаваш"


def test_update_template_price(db):
    """Test updating template item price"""
    _create_lavash_template(db)
    template = db.get_shipment_template(TEST_USER_ID, "тестовый лаваш")
    new_items = template['items'].copy()
    new_items[0]['price'] = 45

    success = db.update_shipment_template(
        telegram_user_id=TEST_USER_ID,
        template_name="тестовый лаваш",
        items=new_items,
    )
    assert success is True

    updated = db.get_shipment_template(TEST_USER_ID, "тестовый лаваш")
    assert updated['items'][0]['price'] == 45


def test_delete_template(db):
    """Test deleting a template"""
    _create_lavash_template(db)
    success = db.delete_shipment_template(TEST_USER_ID, "тестовый лаваш")
    assert success is True

    template = db.get_shipment_template(TEST_USER_ID, "тестовый лаваш")
    assert template is None


def test_multiple_templates(db):
    """Test creating and listing multiple templates"""
    _create_lavash_template(db)

    success = db.create_shipment_template(
        telegram_user_id=TEST_USER_ID,
        template_name="айран",
        supplier_id=2,
        supplier_name="Молочник",
        account_id=2,
        account_name="Каспи Пей",
        items=[{"id": 5, "name": "Айран", "price": 150}],
        storage_id=1,
    )
    assert success is True

    templates = db.get_shipment_templates(TEST_USER_ID)
    assert len(templates) == 2
