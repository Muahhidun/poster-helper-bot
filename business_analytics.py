"""Verified business analytics built directly from two Poster accounts.

The module deliberately separates deterministic calculations from AI wording.
Poster is the source of truth; local tables only cache reproducible reports.
All public monetary values are tenge (Poster returns minor currency units).
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, Iterable, List, Optional

import aiohttp

from account_analytics import (
    KZ_TZ,
    build_completed_history,
    build_summary,
    last_completed_date,
    normalise_text,
)
from poster_client import PosterClient


logger = logging.getLogger(__name__)

MONEY = Decimal("0.01")
TRANSFER_CATEGORY = "переводы"
SUPPLY_CATEGORY = "поставки"


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def _tenge(value: Any) -> Decimal:
    return (_decimal(value) / Decimal("100")).quantize(MONEY, rounding=ROUND_HALF_UP)


def _money(value: Any) -> float:
    return float(_decimal(value).quantize(MONEY, rounding=ROUND_HALF_UP))


def _date(value: Any) -> Optional[date]:
    text = str(value or "")[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _pct_change(current: Any, previous: Any) -> Optional[float]:
    current_value = _decimal(current)
    previous_value = _decimal(previous)
    if previous_value == 0:
        return None
    return float(((current_value / previous_value) - 1) * Decimal("100"))


def _period(start: date, end: date) -> Dict[str, str]:
    return {"start": start.isoformat(), "end": end.isoformat()}


def _is_deleted(item: Dict[str, Any]) -> bool:
    return str(item.get("delete") or "0") == "1"


def _is_transfer(item: Dict[str, Any]) -> bool:
    return (
        str(item.get("type")) == "2"
        or normalise_text(item.get("category_name")) == TRANSFER_CATEGORY
    )


def _is_profit_account(item: Dict[str, Any]) -> bool:
    return "прибыл" in normalise_text(item.get("account_name"))


def _owner_from_comment(item: Dict[str, Any]) -> str:
    comment = normalise_text(item.get("comment") or item.get("description"))
    zhandos = "жандос" in comment or "zhandos" in comment
    ruslan = "руслан" in comment or "ruslan" in comment
    if zhandos and not ruslan:
        return "Жандос"
    if ruslan and not zhandos:
        return "Руслан"
    return "Без имени"


def _closed_orders(orders: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [order for order in orders if str(order.get("status")) == "2"]


def _daily_store_metrics(
    store_id: str,
    store_name: str,
    orders: Iterable[Dict[str, Any]],
    transactions: Iterable[Dict[str, Any]],
    start: date,
    end: date,
) -> List[Dict[str, Any]]:
    days: Dict[date, Dict[str, Any]] = {}
    cursor = start
    while cursor <= end:
        days[cursor] = {
            "date": cursor.isoformat(),
            "store_id": store_id,
            "store_name": store_name,
            "revenue": Decimal("0"),
            "checks": 0,
            "expenses": Decimal("0"),
            "supplies": Decimal("0"),
            "profit_withdrawals": Decimal("0"),
        }
        cursor += timedelta(days=1)

    for order in _closed_orders(orders):
        day = _date(order.get("date_close_date") or order.get("date_close"))
        if day not in days:
            continue
        days[day]["revenue"] += _tenge(order.get("payed_sum"))
        days[day]["checks"] += 1

    for transaction in transactions:
        if _is_deleted(transaction):
            continue
        day = _date(transaction.get("date"))
        if day not in days:
            continue
        amount = abs(_tenge(transaction.get("amount")))
        category = normalise_text(transaction.get("category_name"))
        if str(transaction.get("type")) == "0" and not _is_transfer(transaction):
            days[day]["expenses"] += amount
            if category == SUPPLY_CATEGORY or transaction.get("supplier_name"):
                days[day]["supplies"] += amount
        if _is_profit_account(transaction) and _tenge(transaction.get("amount")) > 0:
            days[day]["profit_withdrawals"] += _tenge(transaction.get("amount"))

    result = []
    for value in days.values():
        revenue = value["revenue"]
        checks = value["checks"]
        expenses = value["expenses"]
        supplies = value["supplies"]
        result.append({
            **value,
            "revenue": _money(revenue),
            "checks": checks,
            "average_check": _money(revenue / checks) if checks else 0.0,
            "expenses": _money(expenses),
            "supplies": _money(supplies),
            "non_supply_expenses": _money(expenses - supplies),
            "profit_withdrawals": _money(value["profit_withdrawals"]),
        })
    return result


def _sum_period(rows: Iterable[Dict[str, Any]], start: date, end: date) -> Dict[str, Any]:
    selected = [row for row in rows if start <= date.fromisoformat(row["date"]) <= end]
    revenue = sum((_decimal(row["revenue"]) for row in selected), Decimal("0"))
    checks = sum(int(row["checks"]) for row in selected)
    expenses = sum((_decimal(row["expenses"]) for row in selected), Decimal("0"))
    supplies = sum((_decimal(row["supplies"]) for row in selected), Decimal("0"))
    withdrawals = sum((_decimal(row["profit_withdrawals"]) for row in selected), Decimal("0"))
    return {
        "period": _period(start, end),
        "revenue": _money(revenue),
        "checks": checks,
        "average_check": _money(revenue / checks) if checks else 0.0,
        "expenses": _money(expenses),
        "supplies": _money(supplies),
        "non_supply_expenses": _money(expenses - supplies),
        "cash_result": _money(revenue - expenses),
        "profit_withdrawals": _money(withdrawals),
        "active_days": sum(1 for row in selected if row["checks"] > 0),
    }


def _product_economics(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    valid = [row for row in rows if not _is_deleted(row)]
    revenue = sum((_tenge(row.get("payed_sum")) for row in valid), Decimal("0"))
    profit = sum((_tenge(row.get("product_profit")) for row in valid), Decimal("0"))
    theoretical_cogs = revenue - profit
    zero_cost = [
        row for row in valid
        if _tenge(row.get("payed_sum")) > 0
        and abs(_tenge(row.get("payed_sum")) - _tenge(row.get("product_profit"))) < MONEY
    ]
    zero_revenue = sum((_tenge(row.get("payed_sum")) for row in zero_cost), Decimal("0"))
    return {
        "revenue": _money(revenue),
        "theoretical_cogs": _money(theoretical_cogs),
        "theoretical_cogs_pct": _money(theoretical_cogs * 100 / revenue) if revenue else 0.0,
        "zero_cost_sold_products": len(zero_cost),
        "zero_cost_revenue": _money(zero_revenue),
        "zero_cost_revenue_pct": _money(zero_revenue * 100 / revenue) if revenue else 0.0,
        "zero_cost_top": [
            {
                "name": row.get("product_name") or "Без названия",
                "revenue": _money(_tenge(row.get("payed_sum"))),
            }
            for row in sorted(zero_cost, key=lambda item: _tenge(item.get("payed_sum")), reverse=True)[:8]
        ],
    }


def _movement_quality(
    latest: Iterable[Dict[str, Any]],
    previous: Iterable[Dict[str, Any]],
    revenue_ratio: Decimal,
) -> Dict[str, Any]:
    latest = list(latest)
    previous_by_id = {str(row.get("ingredient_id")): row for row in previous}
    flags = []
    for row in latest:
        old = previous_by_id.get(str(row.get("ingredient_id")), {})
        income = _decimal(row.get("income"))
        old_income = _decimal(old.get("income"))
        cost = _decimal(row.get("cost_end"))
        old_cost = _decimal(old.get("cost_end")) or cost
        spend = income * cost
        old_spend = old_income * old_cost
        if spend < Decimal("100000") or old_spend <= 0:
            continue
        spend_change = ((spend / old_spend) - 1) * 100
        normalized_change = (
            ((spend / old_spend / revenue_ratio) - 1) * 100
            if revenue_ratio > 0 else spend_change
        )
        if normalized_change < 20:
            continue
        flags.append({
            "ingredient_id": str(row.get("ingredient_id") or ""),
            "name": row.get("ingredient_name") or "Без названия",
            "purchase_spend": _money(spend),
            "previous_purchase_spend": _money(old_spend),
            "purchase_change_pct": _money(spend_change),
            "change_vs_revenue_pct": _money(normalized_change),
            "write_offs": _money(row.get("write_offs")),
            "previous_write_offs": _money(old.get("write_offs")),
            "end_stock": _money(row.get("end")),
        })
    flags.sort(key=lambda item: item["purchase_spend"], reverse=True)
    negative = sum(1 for row in latest if _decimal(row.get("end")) < 0)
    return {
        "ingredients": len(latest),
        "negative_end_stock": negative,
        "negative_end_stock_pct": _money(Decimal(negative) * 100 / len(latest)) if latest else 0.0,
        "purchase_flags": flags[:10],
        "confidence": "medium" if negative <= len(latest) * 0.1 else "low",
    }


async def _fetch_store(
    store: Dict[str, Any],
    boundary: date,
    current_start: date,
    previous_start: date,
    previous_end: date,
    report_date: date,
) -> Dict[str, Any]:
    store_id = str(store.get("id") or "")
    store_name = store.get("account_name") or "Poster"
    status = {"store_id": store_id, "store_name": store_name, "success": False, "error": None}
    client = PosterClient(
        poster_token=store.get("poster_token"),
        poster_user_id=store.get("poster_user_id"),
        poster_base_url=store.get("poster_base_url"),
        request_timeout=60,
    )
    try:
        date_from = boundary.strftime("%Y%m%d")
        date_to = (report_date + timedelta(days=1)).strftime("%Y%m%d")
        current_from = current_start.strftime("%Y%m%d")
        previous_from = previous_start.strftime("%Y%m%d")
        previous_to = previous_end.strftime("%Y%m%d")
        report_to = report_date.strftime("%Y%m%d")

        accounts, transactions, orders, products, current_sales, previous_sales, current_movement, previous_movement = await asyncio.gather(
            client.get_accounts(),
            client.get_transactions(date_from=date_from, date_to=date_to),
            client._request("GET", "dash.getTransactions", params={"dateFrom": date_from, "dateTo": report_to}),
            client.get_products(),
            client._request("GET", "dash.getProductsSales", params={"dateFrom": current_from, "dateTo": report_to}),
            client._request("GET", "dash.getProductsSales", params={"dateFrom": previous_from, "dateTo": previous_to}),
            client.get_ingredient_movements(current_from, report_to),
            client.get_ingredient_movements(previous_from, previous_to),
        )

        augmented_accounts = [{
            **row,
            "store_id": store_id,
            "store_name": store_name,
            "account_id": str(row.get("account_id") or row.get("id") or ""),
            "name": (row.get("name") or row.get("account_name") or "").strip(),
        } for row in accounts]
        augmented_transactions = [{**row, "store_id": store_id, "store_name": store_name} for row in transactions]
        status["success"] = True
        return {
            "status": status,
            "store_id": store_id,
            "store_name": store_name,
            "accounts": augmented_accounts,
            "transactions": augmented_transactions,
            "orders": orders.get("response", []),
            "products": products,
            "current_sales": current_sales.get("response", []),
            "previous_sales": previous_sales.get("response", []),
            "current_movement": current_movement,
            "previous_movement": previous_movement,
        }
    except Exception as exc:
        status["error"] = str(exc)
        logger.error("Business analytics Poster fetch failed for %s: %s", store_name, exc)
        return {"status": status, "store_id": store_id, "store_name": store_name}
    finally:
        await client.close()


def _store_summary(
    fetched: Dict[str, Any],
    daily: List[Dict[str, Any]],
    current_start: date,
    previous_start: date,
    previous_end: date,
    report_date: date,
) -> Dict[str, Any]:
    current = _sum_period(daily, current_start, report_date)
    previous = _sum_period(daily, previous_start, previous_end)
    revenue_ratio = (
        _decimal(current["revenue"]) / _decimal(previous["revenue"])
        if _decimal(previous["revenue"]) else Decimal("1")
    )
    return {
        "store_id": fetched["store_id"],
        "store_name": fetched["store_name"],
        "current": current,
        "previous": previous,
        "changes": {
            "revenue_pct": _pct_change(current["revenue"], previous["revenue"]),
            "checks_pct": _pct_change(current["checks"], previous["checks"]),
            "average_check_pct": _pct_change(current["average_check"], previous["average_check"]),
            "expenses_pct": _pct_change(current["expenses"], previous["expenses"]),
            "supplies_pct": _pct_change(current["supplies"], previous["supplies"]),
            "cash_result_pct": _pct_change(current["cash_result"], previous["cash_result"]),
        },
        "product_economics": {
            "current": _product_economics(fetched["current_sales"]),
            "previous": _product_economics(fetched["previous_sales"]),
        },
        "inventory_quality": _movement_quality(
            fetched["current_movement"],
            fetched["previous_movement"],
            revenue_ratio,
        ),
        "menu_quality": {
            "products": len(fetched["products"]),
            "products_with_zero_current_cost": sum(
                1 for row in fetched["products"] if _tenge(row.get("cost")) == 0
            ),
        },
    }


def _combine_daily(store_rows: Iterable[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    by_date: Dict[str, Dict[str, Any]] = {}
    for rows in store_rows:
        for row in rows:
            item = by_date.setdefault(row["date"], {
                "date": row["date"],
                "store_id": "total",
                "store_name": "Все заведения",
                "revenue": Decimal("0"),
                "checks": 0,
                "expenses": Decimal("0"),
                "supplies": Decimal("0"),
                "profit_withdrawals": Decimal("0"),
            })
            for key in ("revenue", "expenses", "supplies", "profit_withdrawals"):
                item[key] += _decimal(row[key])
            item["checks"] += int(row["checks"])
    result = []
    for item in sorted(by_date.values(), key=lambda value: value["date"]):
        revenue = item["revenue"]
        checks = item["checks"]
        expenses = item["expenses"]
        supplies = item["supplies"]
        result.append({
            **item,
            "revenue": _money(revenue),
            "checks": checks,
            "average_check": _money(revenue / checks) if checks else 0.0,
            "expenses": _money(expenses),
            "supplies": _money(supplies),
            "non_supply_expenses": _money(expenses - supplies),
            "profit_withdrawals": _money(item["profit_withdrawals"]),
        })
    return result


def _build_insights(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    insights: List[Dict[str, Any]] = []
    capital = report["capital"]
    generation_change = capital.get("generation_change_pct")
    if generation_change is not None and generation_change <= -10:
        insights.append({
            "id": "capital_generation_down",
            "severity": "critical" if generation_change <= -20 else "warning",
            "title": "Бизнес создаёт меньше свободного капитала",
            "body": (
                f"За последние 30 дней до изъятий создано {capital['current_generated']:,.0f} ₸, "
                f"в предыдущем периоде — {capital['previous_generated']:,.0f} ₸."
            ),
            "evidence": {"change_pct": generation_change},
            "confidence": "high",
            "question": "Какие крупные закупки или разовые расходы этого периода были запланированы заранее?",
        })

    for store in report["stores"]:
        name = store["store_name"]
        changes = store["changes"]
        revenue_change = changes.get("revenue_pct")
        expense_change = changes.get("expenses_pct")
        if revenue_change is not None and revenue_change <= -5:
            insights.append({
                "id": f"revenue_down_{store['store_id']}",
                "severity": "warning",
                "title": f"Снижение продаж: {name}",
                "body": (
                    f"Выручка снизилась на {abs(revenue_change):.1f}%, "
                    f"количество чеков изменилось на {(changes.get('checks_pct') or 0):+.1f}%."
                ),
                "evidence": {"revenue_change_pct": revenue_change, "checks_change_pct": changes.get("checks_pct")},
                "confidence": "high",
                "question": "Были ли изменения графика, меню, доставки или доступности товаров в этом отделе?",
            })
        if expense_change is not None and revenue_change is not None and expense_change - revenue_change >= 5:
            insights.append({
                "id": f"expense_pressure_{store['store_id']}",
                "severity": "warning",
                "title": f"Расходы растут быстрее выручки: {name}",
                "body": (
                    f"Расходы изменились на {expense_change:+.1f}%, "
                    f"а выручка — на {revenue_change:+.1f}%."
                ),
                "evidence": {"expense_change_pct": expense_change, "revenue_change_pct": revenue_change},
                "confidence": "high",
                "question": "Проверьте крупнейшие поставки и разовые расходы этого отдела.",
            })

        product = store["product_economics"]["current"]
        if product["zero_cost_revenue_pct"] >= 10:
            insights.append({
                "id": f"zero_cost_{store['store_id']}",
                "severity": "critical" if product["zero_cost_revenue_pct"] >= 30 else "warning",
                "title": f"Неполная себестоимость меню: {name}",
                "body": (
                    f"У {product['zero_cost_sold_products']} продававшихся позиций нулевая себестоимость; "
                    f"это {product['zero_cost_revenue_pct']:.1f}% выручки отдела."
                ),
                "evidence": {
                    "zero_cost_products": product["zero_cost_sold_products"],
                    "zero_cost_revenue_pct": product["zero_cost_revenue_pct"],
                    "top": product["zero_cost_top"][:5],
                },
                "confidence": "high",
                "question": "С каких самых продаваемых позиций начать проверку техкарт?",
            })

        inventory = store["inventory_quality"]
        if inventory["negative_end_stock_pct"] >= 10:
            insights.append({
                "id": f"inventory_unreliable_{store['store_id']}",
                "severity": "info",
                "title": f"Складские остатки требуют проверки: {name}",
                "body": (
                    f"Отрицательный остаток у {inventory['negative_end_stock']} из "
                    f"{inventory['ingredients']} позиций. Выводы по фактическому расходу ограничены."
                ),
                "evidence": {"negative_stock_pct": inventory["negative_end_stock_pct"]},
                "confidence": "high",
                "question": "Можно ли провести точечный пересчёт 5–10 самых дорогих ингредиентов?",
            })

        for flag in inventory["purchase_flags"][:3]:
            insights.append({
                "id": f"purchase_{store['store_id']}_{flag['ingredient_id']}",
                "severity": "warning",
                "title": f"Закупка выросла быстрее продаж: {flag['name']}",
                "body": (
                    f"Расчётная стоимость прихода выросла на {flag['purchase_change_pct']:.1f}% "
                    f"и составила {flag['purchase_spend']:,.0f} ₸."
                ),
                "evidence": flag,
                "confidence": inventory["confidence"],
                "question": "Проверьте текущий физический остаток, фасовку и соблюдение граммовки.",
            })

    rank = {"critical": 0, "warning": 1, "info": 2}
    insights.sort(key=lambda item: rank.get(item["severity"], 3))
    return insights[:10]


async def collect_business_report(
    poster_accounts: Iterable[Dict[str, Any]],
    report_date: Optional[date] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Collect a complete report. Partial Poster data is never aggregated."""
    stores = list(poster_accounts)
    now = now or datetime.now(KZ_TZ)
    if now.tzinfo is None:
        now = KZ_TZ.localize(now)
    report_date = report_date or last_completed_date(now)
    current_start = report_date - timedelta(days=29)
    previous_end = current_start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=29)
    boundary = previous_start - timedelta(days=1)

    fetched = await asyncio.gather(*[
        _fetch_store(store, boundary, current_start, previous_start, previous_end, report_date)
        for store in stores
    ])
    source_status = [item["status"] for item in fetched]
    if not stores or not all(status["success"] for status in source_status):
        return {
            "success": False,
            "report_date": report_date.isoformat(),
            "error": "Не удалось получить полные данные всех заведений Poster",
            "source_status": source_status,
        }

    store_daily: List[List[Dict[str, Any]]] = []
    store_summaries = []
    all_accounts = []
    all_transactions = []
    for item in fetched:
        daily = _daily_store_metrics(
            item["store_id"], item["store_name"], item["orders"], item["transactions"],
            previous_start, report_date,
        )
        store_daily.append(daily)
        store_summaries.append(
            _store_summary(item, daily, current_start, previous_start, previous_end, report_date)
        )
        all_accounts.extend(item["accounts"])
        all_transactions.extend(item["transactions"])

    combined_daily = _combine_daily(store_daily)
    combined_current = _sum_period(combined_daily, current_start, report_date)
    combined_previous = _sum_period(combined_daily, previous_start, previous_end)

    history_now = KZ_TZ.localize(datetime.combine(report_date + timedelta(days=1), time(hour=12)))
    capital_history = build_completed_history(
        all_accounts, all_transactions, now=history_now, days=61,
    )["total"]
    capital_by_date = {row["date"]: row["balance"] for row in capital_history}
    current_boundary = previous_end.isoformat()
    previous_boundary = boundary.isoformat()
    report_key = report_date.isoformat()
    current_capital_change = _money(
        _decimal(capital_by_date.get(report_key)) - _decimal(capital_by_date.get(current_boundary))
    )
    previous_capital_change = _money(
        _decimal(capital_by_date.get(current_boundary)) - _decimal(capital_by_date.get(previous_boundary))
    )
    current_generated = _money(_decimal(current_capital_change) + _decimal(combined_current["profit_withdrawals"]))
    previous_generated = _money(_decimal(previous_capital_change) + _decimal(combined_previous["profit_withdrawals"]))
    capital_summary = build_summary(all_accounts)

    report: Dict[str, Any] = {
        "success": True,
        "report_date": report_date.isoformat(),
        "generated_at": datetime.now(KZ_TZ).isoformat(),
        "periods": {
            "current": _period(current_start, report_date),
            "previous": _period(previous_start, previous_end),
        },
        "source_status": source_status,
        "stores": store_summaries,
        "combined": {
            "current": combined_current,
            "previous": combined_previous,
            "changes": {
                "revenue_pct": _pct_change(combined_current["revenue"], combined_previous["revenue"]),
                "checks_pct": _pct_change(combined_current["checks"], combined_previous["checks"]),
                "average_check_pct": _pct_change(combined_current["average_check"], combined_previous["average_check"]),
                "expenses_pct": _pct_change(combined_current["expenses"], combined_previous["expenses"]),
                "supplies_pct": _pct_change(combined_current["supplies"], combined_previous["supplies"]),
                "cash_result_pct": _pct_change(combined_current["cash_result"], combined_previous["cash_result"]),
            },
        },
        "capital": {
            "current_balance": _money(capital_by_date.get(report_key)),
            "current_live_balance": capital_summary["total_sum"],
            "current_period_start_balance": _money(capital_by_date.get(current_boundary)),
            "previous_period_start_balance": _money(capital_by_date.get(previous_boundary)),
            "current_change": current_capital_change,
            "previous_change": previous_capital_change,
            "current_withdrawals": combined_current["profit_withdrawals"],
            "previous_withdrawals": combined_previous["profit_withdrawals"],
            "current_generated": current_generated,
            "previous_generated": previous_generated,
            "generation_change_pct": _pct_change(current_generated, previous_generated),
            "history": capital_history[-30:],
        },
        "daily_metrics": [*combined_daily, *[row for rows in store_daily for row in rows]],
        "data_limitations": [
            "Фактические остатки ингредиентов не подтверждены инвентаризацией.",
            "Себестоимость Poster считается теоретической, если техкарты неполны.",
            "Расчётный приход ингредиентов не равен их фактическому потреблению за тот же период.",
        ],
    }
    report["insights"] = _build_insights(report)
    return report


def format_telegram_report(report: Dict[str, Any], ai_commentary: Optional[Dict[str, Any]] = None) -> str:
    """Format a concise, evidence-backed personal Telegram report."""
    if not report.get("success"):
        return "⚠️ Не удалось собрать полный утренний отчёт из двух Poster. Частичные цифры не показаны."

    combined = report["combined"]
    capital = report["capital"]
    current = combined["current"]
    changes = combined["changes"]

    def amount(value: Any) -> str:
        return f"{_money(value):,.0f}".replace(",", " ") + " ₸"

    def pct(value: Optional[float]) -> str:
        return "—" if value is None else f"{value:+.1f}%"

    lines = [
        f"📊 <b>Утренний отчёт за {datetime.strptime(report['report_date'], '%Y-%m-%d').strftime('%d.%m.%Y')}</b>",
        "",
        f"💰 Капитал на закрытие: <b>{amount(capital['current_balance'])}</b>",
        f"📈 Создано до изъятий за 30 дней: <b>{amount(capital['current_generated'])}</b> ({pct(capital['generation_change_pct'])})",
        f"🏦 Переведено в «Прибыль»: <b>{amount(capital['current_withdrawals'])}</b>",
        f"🧾 Выручка 30 дней: <b>{amount(current['revenue'])}</b> ({pct(changes['revenue_pct'])})",
        f"💸 Расходы 30 дней: <b>{amount(current['expenses'])}</b> ({pct(changes['expenses_pct'])})",
        "",
        "<b>По отделам:</b>",
    ]
    for store in report["stores"]:
        lines.append(
            f"• {html.escape(str(store['store_name']))}: {amount(store['current']['revenue'])} выручки "
            f"({pct(store['changes']['revenue_pct'])}), расходы {amount(store['current']['expenses'])}"
        )

    priorities = report.get("insights", [])[:4]
    if priorities:
        lines.extend(["", "<b>Что требует внимания:</b>"])
        for insight in priorities:
            icon = "🔴" if insight["severity"] == "critical" else "🟠" if insight["severity"] == "warning" else "🔵"
            title = html.escape(str(insight["title"]))
            body = html.escape(str(insight["body"]))
            lines.append(f"{icon} <b>{title}</b> — {body}")

    if ai_commentary and ai_commentary.get("question"):
        question = html.escape(str(ai_commentary["question"]))
        lines.extend(["", f"❓ <b>Вопрос:</b> {question}"])
    elif priorities and priorities[0].get("question"):
        question = html.escape(str(priorities[0]["question"]))
        lines.extend(["", f"❓ <b>Вопрос:</b> {question}"])

    lines.extend(["", "ℹ️ Фактический расход ингредиентов оценивается с ограниченной уверенностью до инвентаризации."])
    return "\n".join(lines)


async def generate_ai_commentary(report: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Ask Gemini to prioritize verified facts; deterministic report remains usable on failure."""
    api_key = os.getenv("GEMINI_API_KEY")
    model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
    if not api_key or not report.get("success"):
        return None

    compact_facts = {
        "report_date": report["report_date"],
        "combined": report["combined"],
        "capital": {key: value for key, value in report["capital"].items() if key != "history"},
        "stores": report["stores"],
        "verified_insights": report["insights"],
        "limitations": report["data_limitations"],
    }
    prompt = """Ты — осторожный финансовый руководитель двух заведений PizzBurg.
Используй ТОЛЬКО JSON-факты ниже. Не придумывай числа, причины или события.
Не называй кассовый результат бухгалтерской чистой прибылью.
Если данные склада ненадёжны, формулируй гипотезу и проси физическую проверку.
Выбери один главный приоритет и один конкретный вопрос владельцу.
Верни только JSON: {"summary":"до 350 символов","priority":"до 180 символов","question":"один вопрос"}.

Факты:
""" + json.dumps(compact_facts, ensure_ascii=False, separators=(",", ":"))
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1},
    }
    try:
        timeout = aiohttp.ClientTimeout(total=45)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload) as response:
                if response.status != 200:
                    logger.warning("Business analyst AI returned status %s", response.status)
                    return None
                data = await response.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        parsed = json.loads(text)
        if not all(isinstance(parsed.get(key), str) for key in ("summary", "priority", "question")):
            return None
        return parsed
    except Exception as exc:
        logger.warning("Business analyst AI commentary unavailable: %s", exc)
        return None
