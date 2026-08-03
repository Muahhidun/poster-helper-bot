"""Reliable aggregation and history calculations for the capital dashboard.

All monetary values exposed by this module are tenge. Poster sends finance
amounts in minor units, so conversion happens once at the API boundary.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, Iterable, List, Optional

import pytz

from poster_client import PosterClient


KZ_TZ = pytz.timezone("Asia/Almaty")
MONEY_HISTORY_FROM = "20100101"
WOLT_NET_FACTOR = Decimal("0.70")

ACCOUNT_DEFINITIONS: Dict[str, Dict[str, str]] = {
    "cash": {
        "name": "Касса",
        "subtitle": "Оставил в кассе на закупы",
        "icon": "cash",
        "color": "green",
    },
    "kaspi": {
        "name": "Kaspi Pay",
        "subtitle": "Основной + Sunday",
        "icon": "kaspi",
        "color": "red",
    },
    "halyk": {
        "name": "Halyk Bank",
        "subtitle": "Основной + Sunday",
        "icon": "halyk",
        "color": "teal",
    },
    "money_home": {
        "name": "Деньги дома",
        "subtitle": "Жандос + Руслан",
        "icon": "bank",
        "color": "blue",
    },
    "wolt": {
        "name": "Wolt",
        "subtitle": "После вычета 30% комиссии",
        "icon": "wolt",
        "color": "blue",
    },
}

VALID_ACCOUNT_KEYS = frozenset({"total", *ACCOUNT_DEFINITIONS.keys()})


def _decimal(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def minor_to_tenge(value: Any) -> Decimal:
    return _money(_decimal(value) / Decimal("100"))


def normalise_text(value: Any) -> str:
    return " ".join(str(value or "").casefold().replace("ё", "е").split())


def classify_account(name: Any) -> Optional[str]:
    """Map a Poster finance account name to a selected physical account.

    Unknown accounts are deliberately ignored. They must never silently fall
    into cash or another physical account.
    """
    value = normalise_text(name)
    if not value:
        return None
    if "оставил в кассе" in value:
        return "cash"
    if "kaspi" in value or "каспий" in value:
        return "kaspi"
    if "halyk" in value or "халык" in value:
        return "halyk"
    if "деньги дом" in value or "деньги дома" in value:
        return "money_home"
    if "wolt" in value or "вольт" in value:
        return "wolt"
    return None


def transaction_amount_tenge(transaction: Dict[str, Any]) -> Decimal:
    """Return the signed transaction amount in tenge.

    Poster documents type=0 as expense and type=1 as income. Amounts normally
    already carry their sign. The type fallback only fixes a positive expense;
    it does not erase negative corrections on income transactions.
    """
    raw_value = transaction.get("amount")
    if raw_value is None:
        raw_value = transaction.get("sum")
    amount = minor_to_tenge(raw_value)
    if str(transaction.get("type")) == "0" and amount > 0:
        amount = -amount
    return _money(amount)


def is_deleted_transaction(transaction: Dict[str, Any]) -> bool:
    return str(transaction.get("delete") or "0") == "1"


def _factor_for(account_key: str) -> Decimal:
    return WOLT_NET_FACTOR if account_key == "wolt" else Decimal("1")


def _raw_account_balance(account: Dict[str, Any], field: str = "balance") -> Decimal:
    return minor_to_tenge(account.get(field))


def build_physical_accounts(raw_accounts: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    totals = {key: Decimal("0") for key in ACCOUNT_DEFINITIONS}
    components: Dict[str, List[Dict[str, Any]]] = {key: [] for key in ACCOUNT_DEFINITIONS}

    for account in raw_accounts:
        key = classify_account(account.get("name"))
        if not key:
            continue
        gross = _raw_account_balance(account)
        net = _money(gross * _factor_for(key))
        totals[key] += net
        component = {
            "store_id": str(account.get("store_id") or ""),
            "store_name": account.get("store_name") or "Poster",
            "account_id": str(account.get("account_id") or ""),
            "account_name": account.get("name") or "",
            "balance": float(net),
        }
        if key == "wolt":
            component["gross_balance"] = float(_money(gross))
        components[key].append(component)

    result: List[Dict[str, Any]] = []
    for key, definition in ACCOUNT_DEFINITIONS.items():
        balance = _money(totals[key])
        item = {
            "key": key,
            **definition,
            "balance": float(balance),
            "components": components[key],
        }
        if key == "wolt":
            item["gross_balance"] = float(
                _money(sum((_decimal(c.get("gross_balance")) for c in components[key]), Decimal("0")))
            )
        result.append(item)
    return result


def build_money_home_breakdown(
    raw_accounts: Iterable[Dict[str, Any]],
    transactions: Iterable[Dict[str, Any]],
    live_balance: Any,
) -> Dict[str, Any]:
    """Split the pooled Money Home balance by names in transaction comments."""
    zhandos = Decimal("0")
    ruslan = Decimal("0")
    unassigned = Decimal("0")
    unnamed_count = 0
    ambiguous_count = 0

    money_accounts = [a for a in raw_accounts if classify_account(a.get("name")) == "money_home"]
    for account in money_accounts:
        unassigned += _raw_account_balance(account, "balance_start")

    for transaction in transactions:
        if is_deleted_transaction(transaction):
            continue
        if classify_account(transaction.get("account_name") or transaction.get("name")) != "money_home":
            continue
        amount = transaction_amount_tenge(transaction)
        comment = normalise_text(transaction.get("comment") or transaction.get("description"))
        is_zhandos = "жандос" in comment or "zhandos" in comment
        is_ruslan = "руслан" in comment or "ruslan" in comment
        if is_zhandos and not is_ruslan:
            zhandos += amount
        elif is_ruslan and not is_zhandos:
            ruslan += amount
        else:
            unassigned += amount
            if is_zhandos and is_ruslan:
                ambiguous_count += 1
            else:
                unnamed_count += 1

    live = _money(_decimal(live_balance))
    calculated = _money(zhandos + ruslan + unassigned)
    reconciliation_delta = _money(live - calculated)
    # Keep the displayed split arithmetically equal to the official live balance,
    # but expose any unexplained difference instead of pretending it is assigned.
    unassigned += reconciliation_delta

    return {
        "available": True,
        "zhandos": float(_money(zhandos)),
        "ruslan": float(_money(ruslan)),
        "unassigned": float(_money(unassigned)),
        "total": float(live),
        "reconciled": abs(reconciliation_delta) < Decimal("0.01"),
        "reconciliation_delta": float(reconciliation_delta),
        "unnamed_transactions": unnamed_count,
        "ambiguous_transactions": ambiguous_count,
    }


def build_summary(
    raw_accounts: Iterable[Dict[str, Any]],
    money_transactions: Optional[Iterable[Dict[str, Any]]] = None,
    money_error: Optional[str] = None,
) -> Dict[str, Any]:
    raw_accounts = list(raw_accounts)
    accounts = build_physical_accounts(raw_accounts)
    total = _money(sum((_decimal(item["balance"]) for item in accounts), Decimal("0")))
    money_item = next(item for item in accounts if item["key"] == "money_home")
    if money_error:
        money_item["owners"] = {"available": False, "error": money_error}
    else:
        money_item["owners"] = build_money_home_breakdown(
            raw_accounts,
            money_transactions or [],
            money_item["balance"],
        )
    return {"accounts": accounts, "total_sum": float(total)}


def parse_poster_datetime(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    parsed: Optional[datetime] = None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(text[:19], fmt)
            break
        except ValueError:
            continue
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return KZ_TZ.localize(parsed)
    return parsed.astimezone(KZ_TZ)


def last_completed_date(now: Optional[datetime] = None) -> date:
    now = now or datetime.now(KZ_TZ)
    if now.tzinfo is None:
        now = KZ_TZ.localize(now)
    shifted = now.astimezone(KZ_TZ) - timedelta(hours=2)
    return shifted.date() - timedelta(days=1)


def completed_dates(now: Optional[datetime] = None, days: int = 15, include_anchor: bool = False) -> List[date]:
    end = last_completed_date(now)
    count = days + (1 if include_anchor else 0)
    return [end - timedelta(days=offset) for offset in range(count - 1, -1, -1)]


def _cutoff_for(day: date) -> datetime:
    return KZ_TZ.localize(datetime.combine(day + timedelta(days=1), time(hour=2)))


def build_completed_history(
    raw_accounts: Iterable[Dict[str, Any]],
    transactions: Iterable[Dict[str, Any]],
    now: Optional[datetime] = None,
    days: int = 15,
) -> Dict[str, List[Dict[str, Any]]]:
    """Reconstruct completed 02:00 balances from current balances and transactions."""
    now = now or datetime.now(KZ_TZ)
    if now.tzinfo is None:
        now = KZ_TZ.localize(now)
    now = now.astimezone(KZ_TZ)

    current_accounts = build_physical_accounts(raw_accounts)
    current = {item["key"]: _decimal(item["balance"]) for item in current_accounts}

    usable_transactions: List[tuple[datetime, str, Decimal]] = []
    for transaction in transactions:
        if is_deleted_transaction(transaction):
            continue
        key = classify_account(transaction.get("account_name") or transaction.get("name"))
        if not key:
            continue
        tx_dt = parse_poster_datetime(transaction.get("date") or transaction.get("created_at"))
        if tx_dt is None or tx_dt > now:
            continue
        amount = _money(transaction_amount_tenge(transaction) * _factor_for(key))
        usable_transactions.append((tx_dt, key, amount))

    dates_with_anchor = completed_dates(now, days=days, include_anchor=True)
    balances_by_key: Dict[str, List[Decimal]] = {key: [] for key in ACCOUNT_DEFINITIONS}
    balances_by_key["total"] = []

    for day in dates_with_anchor:
        cutoff = _cutoff_for(day)
        day_balances: Dict[str, Decimal] = {}
        for key in ACCOUNT_DEFINITIONS:
            after_cutoff = sum(
                (amount for tx_dt, tx_key, amount in usable_transactions if tx_key == key and tx_dt > cutoff),
                Decimal("0"),
            )
            day_balances[key] = _money(current[key] - after_cutoff)
            balances_by_key[key].append(day_balances[key])
        balances_by_key["total"].append(
            _money(sum(day_balances.values(), Decimal("0")))
        )

    history: Dict[str, List[Dict[str, Any]]] = {}
    visible_dates = dates_with_anchor[1:]
    for key, balances in balances_by_key.items():
        items: List[Dict[str, Any]] = []
        for index, day in enumerate(visible_dates, start=1):
            balance = balances[index]
            previous = balances[index - 1]
            items.append({
                "date": day.isoformat(),
                "formatted_date": day.strftime("%d.%m"),
                "balance": float(_money(balance)),
                "net_change": float(_money(balance - previous)),
                "cutoff_at": _cutoff_for(day).isoformat(),
            })
        history[key] = items
    return history


def history_stats(history: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not history:
        return {
            "period_change": 0.0,
            "average_daily_change": 0.0,
            "positive_days": 0,
            "negative_days": 0,
            "flat_days": 0,
        }
    period_change = _money(_decimal(history[-1]["balance"]) - _decimal(history[0]["balance"]))
    intervals = max(1, len(history) - 1)
    changes = [_decimal(item.get("net_change")) for item in history]
    return {
        "period_change": float(period_change),
        "average_daily_change": float(_money(period_change / Decimal(intervals))),
        "positive_days": sum(1 for value in changes if value > 0),
        "negative_days": sum(1 for value in changes if value < 0),
        "flat_days": sum(1 for value in changes if value == 0),
    }


async def _fetch_store(
    store: Dict[str, Any],
    date_from: Optional[str],
    date_to: Optional[str],
    include_money_history: bool,
) -> Dict[str, Any]:
    store_id = str(store.get("id") or "")
    store_name = store.get("account_name") or "Poster"
    status = {
        "store_id": store_id,
        "store_name": store_name,
        "success": False,
        "error": None,
    }
    token = store.get("poster_token")
    if not token:
        status["error"] = "Не настроен токен Poster"
        return {"status": status, "accounts": [], "transactions": [], "money_transactions": []}

    client = PosterClient(
        poster_token=token,
        poster_user_id=store.get("poster_user_id"),
        poster_base_url=store.get("poster_base_url"),
    )
    accounts: List[Dict[str, Any]] = []
    transactions: List[Dict[str, Any]] = []
    money_transactions: List[Dict[str, Any]] = []
    try:
        finance_accounts = await client.get_accounts()
        for account in finance_accounts:
            accounts.append({
                **account,
                "store_id": store_id,
                "store_name": store_name,
                "account_id": str(account.get("account_id") or account.get("id") or ""),
                "name": (account.get("name") or account.get("account_name") or "").strip(),
            })

        selected_counts = {key: 0 for key in ACCOUNT_DEFINITIONS}
        for account in accounts:
            key = classify_account(account.get("name"))
            if key:
                selected_counts[key] += 1
        missing = [ACCOUNT_DEFINITIONS[key]["name"] for key, count in selected_counts.items() if count == 0]
        duplicates = [ACCOUNT_DEFINITIONS[key]["name"] for key, count in selected_counts.items() if count > 1]
        if missing:
            raise ValueError(f"Не найдены выбранные счета: {', '.join(missing)}")
        if duplicates:
            raise ValueError(f"Найдено несколько счетов вместо одного: {', '.join(duplicates)}")

        if date_from and date_to:
            recent = await client.get_transactions(date_from=date_from, date_to=date_to)
            transactions = [
                {**tx, "store_id": store_id, "store_name": store_name}
                for tx in recent
            ]

        if include_money_history:
            money_ids = [a["account_id"] for a in accounts if classify_account(a.get("name")) == "money_home"]
            try:
                money_batches = await asyncio.gather(*[
                    client.get_transactions(
                        date_from=MONEY_HISTORY_FROM,
                        date_to=date_to or datetime.now(KZ_TZ).strftime("%Y%m%d"),
                        account_id=account_id,
                    )
                    for account_id in money_ids
                ])
                for batch in money_batches:
                    money_transactions.extend(
                        {**tx, "store_id": store_id, "store_name": store_name}
                        for tx in batch
                    )
            except Exception as exc:  # balances remain usable even if owner split fails
                status["money_error"] = str(exc)

        status["success"] = True
        return {
            "status": status,
            "accounts": accounts,
            "transactions": transactions,
            "money_transactions": money_transactions,
        }
    except Exception as exc:
        status["error"] = str(exc)
        return {"status": status, "accounts": [], "transactions": [], "money_transactions": []}
    finally:
        await client.close()


async def fetch_capital_data(
    poster_accounts: Iterable[Dict[str, Any]],
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    include_money_history: bool = False,
) -> Dict[str, Any]:
    stores = list(poster_accounts)
    results = await asyncio.gather(*[
        _fetch_store(store, date_from, date_to, include_money_history)
        for store in stores
    ])
    return {
        "source_status": [result["status"] for result in results],
        "accounts": [account for result in results for account in result["accounts"]],
        "transactions": [tx for result in results for tx in result["transactions"]],
        "money_transactions": [tx for result in results for tx in result["money_transactions"]],
        "complete": bool(stores) and all(result["status"]["success"] for result in results),
    }
