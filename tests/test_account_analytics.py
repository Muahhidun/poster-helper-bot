import asyncio
from datetime import datetime

from account_analytics import (
    KZ_TZ,
    build_completed_history,
    build_summary,
    classify_account,
    fetch_capital_data,
    last_completed_date,
    transaction_amount_tenge,
)


def _account(store, account_id, name, balance, balance_start=0):
    return {
        'store_id': store,
        'store_name': store,
        'account_id': account_id,
        'name': name,
        'balance': balance,
        'balance_start': balance_start,
    }


def test_summary_matches_two_poster_accounts_without_double_counting_money_home():
    raw_accounts = [
        _account('PizzBurg', '1', 'Kaspi Pay', -104008007),
        _account('Sunday', '1', 'Kaspi Pay', 178037064),
        _account('PizzBurg', '2', 'Wolt доставка', 53250300),
        _account('Sunday', '2', 'Wolt доставка', 13384600),
        _account('PizzBurg', '3', 'Оставил в кассе (на закупы)', 50770146),
        _account('Sunday', '3', 'Оставил в кассе (на закупы)', -45503573),
        _account('PizzBurg', '4', 'Деньги дома (отложенные)', 10000200),
        _account('Sunday', '4', 'Деньги дома (отложенные)', 0),
        _account('PizzBurg', '5', 'Халык банк', 176113400),
        _account('Sunday', '5', 'Халык банк', 0),
        # These accounts must never leak into capital.
        _account('PizzBurg', '6', 'Денежный ящик (Кассира)', 10497650),
        _account('Sunday', '7', 'Прибыль', 940000000),
    ]
    money_transactions = [{
        'account_name': 'Деньги дома (отложенные)',
        'type': '1',
        'amount': '10000000',
        'comment': 'Жандос',
        'delete': '0',
    }]

    summary = build_summary(raw_accounts, money_transactions)
    by_key = {item['key']: item for item in summary['accounts']}

    assert summary['total_sum'] == 3120536.60
    assert len(summary['accounts']) == 5
    assert by_key['kaspi']['balance'] == 740290.57
    assert by_key['cash']['balance'] == 52665.73
    assert by_key['wolt']['gross_balance'] == 666349.00
    assert by_key['wolt']['balance'] == 466444.30
    assert by_key['money_home']['balance'] == 100002.00
    assert by_key['money_home']['owners']['zhandos'] == 100000.00
    assert by_key['money_home']['owners']['ruslan'] == 0.00
    assert by_key['money_home']['owners']['unassigned'] == 2.00


def test_money_home_expenses_are_subtracted_from_named_owner():
    raw_accounts = [
        _account('PizzBurg', '4', 'Деньги дома (отложенные)', 6000000),
    ]
    transactions = [
        {'account_name': 'Деньги дома (отложенные)', 'type': '1', 'amount': '10000000', 'comment': 'Жандос'},
        {'account_name': 'Деньги дома (отложенные)', 'type': '0', 'amount': '-4000000', 'comment': 'Жандос — зарплата'},
    ]

    owners = next(
        item for item in build_summary(raw_accounts, transactions)['accounts']
        if item['key'] == 'money_home'
    )['owners']

    assert owners['zhandos'] == 60000.00
    assert owners['ruslan'] == 0.00
    assert owners['unassigned'] == 0.00
    assert owners['reconciled'] is True


def test_poster_expense_type_zero_is_negative_and_unknown_account_is_ignored():
    assert float(transaction_amount_tenge({'type': '0', 'amount': '-62787500'})) == -627875.00
    assert float(transaction_amount_tenge({'type': '0', 'amount': '19400000'})) == -194000.00
    assert classify_account('Денежный ящик (Кассира)') is None
    assert classify_account('Прибыль') is None
    assert classify_account('Оставил в кассе (на закупы)') == 'cash'


def test_history_excludes_today_and_uses_0200_almaty_cutoff():
    now = KZ_TZ.localize(datetime(2026, 8, 4, 12, 0, 0))
    raw_accounts = [_account('PizzBurg', '5', 'Халык банк', 100000000)]
    transactions = [
        # This is today's movement, after the cutoff for 3 August.
        {'account_name': 'Халык банк', 'type': '1', 'amount': '10000000', 'date': '2026-08-04 10:00:00'},
        # This belongs to the completed 3 August interval.
        {'account_name': 'Халык банк', 'type': '1', 'amount': '20000000', 'date': '2026-08-03 12:00:00'},
    ]

    history = build_completed_history(raw_accounts, transactions, now=now, days=2)['halyk']

    assert [item['date'] for item in history] == ['2026-08-02', '2026-08-03']
    assert history[0]['balance'] == 700000.00
    assert history[1]['balance'] == 900000.00
    assert history[1]['net_change'] == 200000.00


def test_last_completed_date_waits_until_0200():
    before_cutoff = KZ_TZ.localize(datetime(2026, 8, 4, 1, 59, 59))
    at_cutoff = KZ_TZ.localize(datetime(2026, 8, 4, 2, 0, 0))

    assert last_completed_date(before_cutoff).isoformat() == '2026-08-02'
    assert last_completed_date(at_cutoff).isoformat() == '2026-08-03'


def test_fetch_is_incomplete_when_one_of_two_poster_sources_fails(monkeypatch):
    selected_accounts = [
        {'account_id': '1', 'name': 'Оставил в кассе (на закупы)', 'balance': '0'},
        {'account_id': '2', 'name': 'Kaspi Pay', 'balance': '0'},
        {'account_id': '3', 'name': 'Халык банк', 'balance': '0'},
        {'account_id': '4', 'name': 'Деньги дома (отложенные)', 'balance': '0'},
        {'account_id': '5', 'name': 'Wolt доставка', 'balance': '0'},
    ]

    class FakePosterClient:
        def __init__(self, poster_token, **kwargs):
            self.poster_token = poster_token

        async def get_accounts(self):
            if self.poster_token == 'broken':
                raise RuntimeError('Poster timeout')
            return selected_accounts

        async def get_transactions(self, date_from, date_to, account_id=None):
            return []

        async def close(self):
            return None

    monkeypatch.setattr('account_analytics.PosterClient', FakePosterClient)
    stores = [
        {'id': 1, 'account_name': 'PizzBurg', 'poster_token': 'ok', 'poster_user_id': '1', 'poster_base_url': 'https://one'},
        {'id': 2, 'account_name': 'Sunday', 'poster_token': 'broken', 'poster_user_id': '2', 'poster_base_url': 'https://two'},
    ]

    result = asyncio.run(fetch_capital_data(stores, date_from='20260801', date_to='20260804'))

    assert result['complete'] is False
    assert [status['success'] for status in result['source_status']] == [True, False]
    assert result['source_status'][1]['store_name'] == 'Sunday'
    assert 'timeout' in result['source_status'][1]['error'].lower()
