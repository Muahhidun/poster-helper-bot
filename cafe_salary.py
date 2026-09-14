"""Automatic salary calculations for the Pizzburg Cafe shift."""

import asyncio
import math
import re
from typing import Dict, Iterable, List, Tuple

from poster_client import PosterClient


YURI_NAME_PATTERN = re.compile(
    r"\b(?:юрий|юра|юрка|юрик|юрчик|юрец|yuri(?:y)?|yury|yura|iuri(?:i|y)?)\b",
    re.IGNORECASE,
)


def is_yuri_name(name: str) -> bool:
    """Recognize common Cyrillic and Latin variants of the name Yuri."""
    return bool(YURI_NAME_PATTERN.search((name or "").strip()))


def calculate_cafe_cashier_salary(revenue: float) -> int:
    """9,000₸ through 150k revenue, then +500₸ per started 50k band."""
    revenue = max(0.0, float(revenue or 0))
    if revenue <= 150_000:
        return 9_000
    extra_bands = math.ceil((revenue - 150_000) / 50_000)
    return 9_000 + extra_bands * 500


def calculate_cafe_sushi_salary(name: str, roll_equivalents: float) -> int:
    """Yuri gets 15k + 50₸ per roll; every other sushi chef gets 14k."""
    if not is_yuri_name(name):
        return 14_000
    # Salary additions can only be 25₸ (half a roll) or 50₸ (a full roll).
    normalized_equivalents = _round_to_half_roll(roll_equivalents)
    return int(15_000 + normalized_equivalents * 50)


def _round_to_half_roll(value: float) -> float:
    """Round a calculated total to the nearest half-roll, with .25 rounding up."""
    return math.floor(max(0.0, float(value or 0)) * 2 + 0.5) / 2


def _has_stem(text: str, stem: str) -> bool:
    return bool(re.search(rf"\b{stem}[a-zа-яё]*\b", (text or "").lower()))


def _piece_count(product_name: str) -> float | None:
    text = (product_name or "").lower().replace("½", "1/2")
    if re.search(r"\b1\s*/\s*2\b", text) or re.search(r"\bпол(?:овина|овинка)?\s+рол", text):
        return 4.0
    match = re.search(
        r"(\d+(?:[.,]\d+)?)\s*(?:шт(?:ук[аи]?)?|pcs?|pieces?|кус(?:очк\w*)?|ролл\w*)\b",
        text,
    )
    if not match:
        return None
    return float(match.group(1).replace(",", "."))


def _rounded_roll_pieces(pieces: float) -> float:
    """Normalize unusual roll/set sizes to the closest multiple of eight."""
    pieces = max(0.0, float(pieces or 0))
    if pieces == 4:
        return 4.0
    # Halfway values round up: 10 -> 8, 13 -> 16, 17 -> 16.
    return float(max(8, math.floor((pieces + 4) / 8) * 8))


def calculate_roll_equivalents(
    product_sales: Iterable[dict], categories: Iterable[dict]
) -> Tuple[float, List[dict], List[str]]:
    """Convert Cafe dishes into full- and half-roll salary equivalents."""
    category_names = {
        str(category.get("category_id") or category.get("id")): (
            category.get("category_name") or category.get("name") or ""
        )
        for category in categories
    }
    total = 0.0
    details: List[dict] = []
    warnings: List[str] = []

    for product in product_sales:
        name = str(product.get("product_name") or product.get("name") or "").strip()
        category_name = str(
            product.get("category_name")
            or category_names.get(str(product.get("category_id")), "")
        ).strip()
        sold_count = float(product.get("count") or 0)
        if sold_count <= 0:
            continue

        name_is_set = _has_stem(name, "сет")
        name_is_roll = _has_stem(name, "рол")
        category_is_set = _has_stem(category_name, "сет")
        category_is_roll = _has_stem(category_name, "рол")
        is_set = name_is_set or category_is_set
        is_roll = name_is_roll or category_is_roll

        is_onigiri = (
            _has_stem(name, "онигир")
            or _has_stem(name, "анигир")
            or _has_stem(name, "onigir")
            or (
                (
                    _has_stem(category_name, "онигир")
                    or _has_stem(category_name, "анигир")
                    or _has_stem(category_name, "onigir")
                )
                and not name_is_set
                and not name_is_roll
            )
        )
        is_gunkan = (
            _has_stem(name, "гункан")
            or (_has_stem(category_name, "гункан") and not name_is_set and not name_is_roll)
        )
        is_sushi = (
            _has_stem(name, "суш")
            or (
                _has_stem(category_name, "суш")
                and not name_is_set
                and not name_is_roll
                and not category_is_set
                and not category_is_roll
            )
        )

        if not is_set and not is_roll and not is_onigiri and not is_gunkan and not is_sushi:
            continue

        # The piece count may be written either in the product name or in the
        # Poster category (for example, a separate "Роллы 4 шт" category).
        pieces = _piece_count(f"{name} {category_name}")
        if is_set and pieces is None:
            warnings.append(
                f"Не учтён сет «{name}»: количество штук не найдено в названии."
            )
            continue

        normalized_pieces = pieces
        if is_onigiri:
            per_product = pieces if pieces is not None else 1.0
            kind = "onigiri"
        elif is_gunkan or is_sushi:
            per_product = (pieces if pieces is not None else 1.0) * 0.5
            kind = "gunkan" if is_gunkan else "sushi"
        else:
            normalized_pieces = _rounded_roll_pieces(pieces) if pieces is not None else None
            per_product = (normalized_pieces / 8.0) if normalized_pieces is not None else 1.0
            kind = "set" if is_set else "roll"

        equivalents = sold_count * per_product
        total += equivalents
        details.append({
            "name": name,
            "category_name": category_name,
            "sold_count": sold_count,
            "pieces": pieces,
            "normalized_pieces": normalized_pieces,
            "roll_equivalents": equivalents,
            "kind": kind,
        })

    return _round_to_half_roll(total), details, warnings


class CafeSalaryCalculator:
    """Load Cafe sales from Poster and calculate the three shift salaries."""

    def __init__(self, account_info: Dict):
        self.account_info = account_info

    async def calculate(self, date: str, sushi_name: str) -> Dict:
        client = PosterClient(
            telegram_user_id=self.account_info["telegram_user_id"],
            poster_token=self.account_info["poster_token"],
            poster_user_id=self.account_info["poster_user_id"],
            poster_base_url=self.account_info["poster_base_url"],
        )
        try:
            transactions_result, products_result, categories_result = await asyncio.gather(
                client._request("GET", "dash.getTransactions", params={
                    "dateFrom": date,
                    "dateTo": date,
                }),
                client._request("GET", "dash.getProductsSales", params={
                    "dateFrom": date,
                    "dateTo": date,
                }),
                client._request("GET", "menu.getCategories"),
            )
        finally:
            await client.close()

        transactions = transactions_result.get("response", [])
        revenue_tiyin = sum(
            abs(float(transaction.get("payed_cash") or 0))
            + abs(float(transaction.get("payed_card") or 0))
            for transaction in transactions
            if str(transaction.get("status")) == "2"
        )
        revenue = revenue_tiyin / 100.0
        roll_equivalents, details, warnings = calculate_roll_equivalents(
            products_result.get("response", []),
            categories_result.get("response", []),
        )

        return {
            "revenue": revenue,
            "cashier_salary": calculate_cafe_cashier_salary(revenue),
            "sushi_salary": calculate_cafe_sushi_salary(sushi_name, roll_equivalents),
            "povar_salary": 10_000,
            "roll_equivalents": roll_equivalents,
            "roll_details": details,
            "warnings": warnings,
            "is_yuri": is_yuri_name(sushi_name),
        }
