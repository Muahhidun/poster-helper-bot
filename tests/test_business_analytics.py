import asyncio
from datetime import date, datetime

from account_analytics import KZ_TZ
from business_analytics import (
    _daily_store_metrics,
    _product_economics,
    collect_business_report,
    format_telegram_report,
)


def test_daily_metrics_use_full_calendar_day_and_exclude_transfers():
    orders = [
        {'status': '2', 'date_close_date': '2026-08-04 23:40:00', 'payed_sum': '100000'},
        {'status': '1', 'date_close_date': '2026-08-04 12:00:00', 'payed_sum': '999999'},
    ]
    transactions = [
        {'date': '2026-08-04 23:50:00', 'type': '0', 'amount': '-20000', 'category_name': 'Комиссии'},
        {'date': '2026-08-04 15:00:00', 'type': '0', 'amount': '-50000', 'category_name': 'Переводы'},
        {'date': '2026-08-04 18:00:00', 'type': '1', 'amount': '300000', 'account_name': 'Прибыль', 'comment': 'Жандос'},
    ]

    rows = _daily_store_metrics('1', 'Pizzburg', orders, transactions, date(2026, 8, 4), date(2026, 8, 4))

    assert rows[0]['revenue'] == 1000.0
    assert rows[0]['checks'] == 1
    assert rows[0]['expenses'] == 200.0
    assert rows[0]['profit_withdrawals'] == 3000.0


def test_product_economics_exposes_zero_cost_sales():
    result = _product_economics([
        {'product_name': 'Сэт', 'payed_sum': '100000', 'product_profit': '100000', 'delete': '0'},
        {'product_name': 'Напиток', 'payed_sum': '50000', 'product_profit': '30000', 'delete': '0'},
    ])

    assert result['revenue'] == 1500.0
    assert result['theoretical_cogs'] == 200.0
    assert result['zero_cost_sold_products'] == 1
    assert result['zero_cost_revenue_pct'] == 66.67


class FakePosterClient:
    def __init__(self, poster_token, **kwargs):
        self.token = poster_token

    async def close(self):
        return None

    async def get_accounts(self):
        if self.token == 'broken':
            raise RuntimeError('Poster timeout')
        return [
            {'account_id': '1', 'name': 'Оставил в кассе (на закупы)', 'balance': '100000'},
            {'account_id': '2', 'name': 'Kaspi Pay', 'balance': '200000'},
            {'account_id': '3', 'name': 'Халык банк', 'balance': '300000'},
            {'account_id': '4', 'name': 'Деньги дома (отложенные)', 'balance': '400000'},
            {'account_id': '5', 'name': 'Wolt доставка', 'balance': '500000'},
        ]

    async def get_transactions(self, date_from, date_to, account_id=None):
        return [
            {'date': '2026-08-04 12:00:00', 'type': '0', 'amount': '-10000', 'category_name': 'Комиссии', 'delete': '0'},
            {'date': '2026-08-04 13:00:00', 'type': '1', 'amount': '20000', 'account_name': 'Прибыль', 'comment': 'Жандос', 'delete': '0'},
        ]

    async def get_products(self):
        return [{'product_id': '1', 'product_name': 'Товар', 'cost': '0'}]

    async def get_ingredient_movements(self, date_from, date_to):
        return [{'ingredient_id': '1', 'ingredient_name': 'Фарш', 'income': '10', 'write_offs': '8', 'end': '-1', 'cost_end': '1000'}]

    async def _request(self, method, endpoint, params=None, **kwargs):
        if endpoint == 'dash.getTransactions':
            return {'response': [
                {'status': '2', 'date_close_date': '2026-08-04 12:00:00', 'payed_sum': '100000'},
                {'status': '2', 'date_close_date': '2026-07-04 12:00:00', 'payed_sum': '80000'},
            ]}
        if endpoint == 'dash.getProductsSales':
            current = params['dateFrom'] >= '20260706'
            value = '100000' if current else '80000'
            return {'response': [{'product_id': '1', 'product_name': 'Товар', 'payed_sum': value, 'product_profit': value, 'delete': '0'}]}
        raise AssertionError(endpoint)


def test_report_fails_closed_when_one_poster_account_is_unavailable(monkeypatch):
    monkeypatch.setattr('business_analytics.PosterClient', FakePosterClient)
    stores = [
        {'id': 1, 'account_name': 'Pizzburg', 'poster_token': 'ok', 'poster_user_id': '1', 'poster_base_url': 'https://one'},
        {'id': 2, 'account_name': 'Cafe', 'poster_token': 'broken', 'poster_user_id': '2', 'poster_base_url': 'https://two'},
    ]
    now = KZ_TZ.localize(datetime(2026, 8, 5, 9, 0))

    report = asyncio.run(collect_business_report(stores, report_date=date(2026, 8, 4), now=now))

    assert report['success'] is False
    assert [item['success'] for item in report['source_status']] == [True, False]
    assert 'полные данные' in report['error']


def test_complete_two_store_report_and_telegram_format(monkeypatch):
    monkeypatch.setattr('business_analytics.PosterClient', FakePosterClient)
    stores = [
        {'id': 1, 'account_name': 'Pizzburg', 'poster_token': 'one', 'poster_user_id': '1', 'poster_base_url': 'https://one'},
        {'id': 2, 'account_name': 'Cafe', 'poster_token': 'two', 'poster_user_id': '2', 'poster_base_url': 'https://two'},
    ]
    now = KZ_TZ.localize(datetime(2026, 8, 5, 9, 0))

    report = asyncio.run(collect_business_report(stores, report_date=date(2026, 8, 4), now=now))

    assert report['success'] is True
    assert len(report['stores']) == 2
    assert report['combined']['current']['revenue'] == 2000.0
    assert report['combined']['current']['profit_withdrawals'] == 400.0
    assert any(item['id'].startswith('zero_cost_') for item in report['insights'])
    message = format_telegram_report(report, {'question': 'Цена < 100 & подтверждена?'})
    assert 'Утренний отчёт' in message
    assert 'Pizzburg' in message
    assert 'Cafe' in message
    assert 'Цена &lt; 100 &amp; подтверждена?' in message


def test_analytics_page_is_owner_only():
    from web_app import app
    app.config['TESTING'] = True
    app.config['SECRET_KEY'] = 'analytics-test'
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess['web_user_id'] = 1
            sess['telegram_user_id'] = 999999
            sess['username'] = 'admin'
            sess['role'] = 'admin'
        assert client.get('/analytics').status_code == 302
        assert client.get('/api/analytics/summary').status_code == 403

        with client.session_transaction() as sess:
            sess['role'] = 'owner'
        response = client.get('/analytics')
        assert response.status_code == 200
        assert 'Анализ бизнеса'.encode('utf-8') in response.data


def test_business_report_database_lifecycle(tmp_path, monkeypatch):
    import config
    from database import UserDatabase

    monkeypatch.setattr(config, 'DATABASE_PATH', tmp_path / 'analytics.db')
    db = UserDatabase()
    uid = 771122
    assert db.create_user(uid, 'token', '1', 'https://example.test/api')
    report = {
        'success': True,
        'report_date': '2026-08-04',
        'source_status': [{'store_name': 'Pizzburg', 'success': True}],
        'capital': {'history': [{'date': '2026-08-04', 'balance': 3000000.0}]},
        'daily_metrics': [{
            'date': '2026-08-04', 'store_id': 'total', 'store_name': 'Все заведения',
            'revenue': 100000.0, 'checks': 25, 'average_check': 4000.0,
            'expenses': 70000.0, 'supplies': 40000.0,
            'non_supply_expenses': 30000.0, 'profit_withdrawals': 0.0,
        }],
    }
    ai = {'summary': 'Проверено', 'priority': 'Расходы', 'question': 'Что изменилось?'}

    assert db.save_business_analytics_report(uid, report, ai)
    loaded = db.get_latest_business_analytics_report(uid)
    assert loaded['report_date'] == '2026-08-04'
    assert loaded['ai_commentary']['summary'] == 'Проверено'
    metrics = db.get_business_daily_metrics(uid)
    assert metrics[0]['revenue'] == 100000.0
    assert metrics[0]['capital_balance'] == 3000000.0
    assert db.mark_business_report_sent(uid, '2026-08-04')
    assert db.get_latest_business_analytics_report(uid)['telegram_sent_at'] is not None
