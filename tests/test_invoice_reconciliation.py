"""Tests for invoice math reconciliation (qty * price == sum) in parser_service"""
import pytest

from parser_service import ParserService


@pytest.fixture
def parser():
    return ParserService()


def test_correct_rows_untouched(parser):
    items = [{"name": "Фри", "qty": 10.0, "price": 1200.0, "sum": 12000.0}]
    result = parser.reconcile_items(items)
    assert result[0]['qty'] == 10.0
    assert result[0]['price'] == 1200.0
    assert result[0]['sum'] == 12000.0


def test_fix_integer_qty_from_sum(parser):
    """OCR misread qty as 1, but sum says 8 × 1820 = 14560"""
    items = [{"name": "Сыр", "qty": 1, "price": 1820, "sum": 14560}]
    result = parser.reconcile_items(items)
    assert result[0]['qty'] == 8.0
    assert result[0]['sum'] == 14560


def test_fix_lost_decimal_point_in_qty(parser):
    """Classic OCR bug: 8.4 кг прочитано как 84 кг (×10)"""
    items = [{"name": "Мясо", "qty": 84, "price": 3200.0, "sum": 26880.0}]
    result = parser.reconcile_items(items)
    assert result[0]['qty'] == pytest.approx(8.4)
    assert result[0]['sum'] == 26880.0


def test_fix_lost_decimal_point_x100(parser):
    """Запятая потеряна на два разряда: 1.55 → 155"""
    items = [{"name": "Специи", "qty": 155, "price": 2000.0, "sum": 3100.0}]
    result = parser.reconcile_items(items)
    assert result[0]['qty'] == pytest.approx(1.55)


def test_fix_extra_decimal_point_in_qty(parser):
    """Обратный случай: 84 прочитано как 8.4 (сумма больше ожидаемой в 10 раз)"""
    items = [{"name": "Мука", "qty": 8.4, "price": 500.0, "sum": 42000.0}]
    result = parser.reconcile_items(items)
    assert result[0]['qty'] == pytest.approx(84.0)


def test_fix_fractional_qty_one_decimal(parser):
    """Вес с одним знаком после запятой восстанавливается из суммы"""
    items = [{"name": "Помидоры", "qty": 18, "price": 1100.0, "sum": 19910.0}]
    result = parser.reconcile_items(items)
    assert result[0]['qty'] == pytest.approx(18.1)


def test_fix_price_when_qty_trusted(parser):
    """Если qty не восстанавливается красиво — корректируем цену из суммы"""
    items = [{"name": "Зелень", "qty": 3.0, "price": 1000.0, "sum": 3777.0}]
    result = parser.reconcile_items(items)
    assert result[0]['price'] == pytest.approx(1259.0)
    assert result[0]['qty'] == 3.0


def test_reconstruct_missing_qty(parser):
    items = [{"name": "Огурцы", "qty": None, "price": 900.0, "sum": 6840.0}]
    result = parser.reconcile_items(items)
    assert result[0]['qty'] == pytest.approx(7.6)


def test_reconstruct_missing_price(parser):
    items = [{"name": "Айсберг", "qty": 5.2, "price": None, "sum": 6760.0}]
    result = parser.reconcile_items(items)
    assert result[0]['price'] == pytest.approx(1300.0)


def test_compute_missing_sum(parser):
    items = [{"name": "Лаваш", "qty": 100, "price": 90.0, "sum": None}]
    result = parser.reconcile_items(items)
    assert result[0]['sum'] == pytest.approx(9000.0)


def test_invoice_total_recalculated_from_rows(parser):
    """Итог накладной пересчитывается из сумм строк при расхождении"""
    parsed = {
        "document_type": "printed_invoice",
        "invoice": {
            "supplier": "Метро",
            "total_sum": 99999.0,
            "items": [
                {"name": "А", "qty": 2, "price": 100.0, "sum": 200.0},
                {"name": "Б", "qty": 3, "price": 100.0, "sum": 300.0},
            ],
        },
    }
    result = parser._reconcile_invoice_items(parsed)
    assert result['invoice']['total_sum'] == pytest.approx(500.0)


def test_garbage_values_dont_crash(parser):
    items = [
        {"name": "Х", "qty": "abc", "price": "xyz", "sum": "??"},
        {"name": "Y"},
        {},
    ]
    result = parser.reconcile_items(items)
    assert len(result) == 3
