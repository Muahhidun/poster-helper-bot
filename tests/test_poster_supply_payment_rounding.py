from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from poster_client import PosterClient


@pytest.mark.asyncio
async def test_supply_payment_never_exceeds_fractional_supply_total():
    client = PosterClient(
        telegram_user_id=1,
        poster_token="token",
        poster_user_id="user",
        poster_base_url="https://example.invalid/api",
    )
    client._request = AsyncMock(return_value={"response": 123})

    supply_id = await client.create_supply(
        supplier_id=10,
        storage_id=20,
        date="2026-09-14 12:00:00",
        ingredients=[
            {"id": 30, "num": 0.958, "price": 4539, "type": "ingredient"},
        ],
        account_id=40,
    )

    assert supply_id == 123
    request_data = client._request.await_args.kwargs["data"]
    payment = Decimal(request_data["transactions[0][amount]"])
    supplied = (
        Decimal(str(request_data["ingredient[0][num]"]))
        * Decimal(str(request_data["ingredient[0][sum]"]))
    )
    assert payment == Decimal("4348.36")
    assert payment <= supplied


@pytest.mark.asyncio
async def test_supply_payment_keeps_exact_cent_total_unchanged():
    client = PosterClient(
        telegram_user_id=1,
        poster_token="token",
        poster_user_id="user",
        poster_base_url="https://example.invalid/api",
    )
    client._request = AsyncMock(return_value={"response": 124})

    await client.create_supply(
        supplier_id=10,
        storage_id=20,
        date="2026-09-14 12:00:00",
        ingredients=[
            {"id": 30, "num": 10, "price": 73, "type": "ingredient"},
        ],
        account_id=40,
    )

    request_data = client._request.await_args.kwargs["data"]
    assert request_data["transactions[0][amount]"] == "730.00"
