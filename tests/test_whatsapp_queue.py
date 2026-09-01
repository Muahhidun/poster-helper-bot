"""Regression tests for durable, sequential WhatsApp batch processing."""

import json
from unittest.mock import patch

from tests.conftest import TEST_USER_ID


def _reset_queue(db):
    conn = db._get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM whatsapp_jobs")
    cursor.execute("DELETE FROM whatsapp_batches")
    conn.commit()
    conn.close()


def _payload(message_id: str) -> str:
    return json.dumps({
        'idMessage': message_id,
        'typeWebhook': 'incomingFileMessageReceived',
        'senderData': {'chatId': 'group@g.us'},
        'messageData': {
            'typeMessage': 'imageMessage',
            'imageMessageData': {
                'downloadUrl': f'https://example.com/{message_id}.jpg',
                'fileName': f'{message_id}.jpg',
            },
        },
    })


def test_enqueue_groups_a_burst_and_deduplicates_message_ids(db):
    db.create_user(TEST_USER_ID, 'mock_token', '1', 'https://mock.joinposter.com/api')
    _reset_queue(db)

    first = db.enqueue_whatsapp_job(
        TEST_USER_ID, 'group@g.us', 'message-1', _payload('message-1')
    )
    second = db.enqueue_whatsapp_job(
        TEST_USER_ID, 'group@g.us', 'message-2', _payload('message-2')
    )
    duplicate = db.enqueue_whatsapp_job(
        TEST_USER_ID, 'group@g.us', 'message-1', _payload('message-1')
    )

    assert first['new_batch'] is True
    assert second['new_batch'] is False
    assert first['batch_id'] == second['batch_id']
    assert duplicate['duplicate'] is True
    assert duplicate['job_id'] == first['job_id']

    conn = db._get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT total_jobs FROM whatsapp_batches WHERE id = ?", (first['batch_id'],))
    assert cursor.fetchone()[0] == 2
    conn.close()


def test_queue_processes_jobs_in_order_and_sends_one_batch_summary(db):
    import web_app

    db.create_user(TEST_USER_ID, 'mock_token', '1', 'https://mock.joinposter.com/api')
    _reset_queue(db)
    first = db.enqueue_whatsapp_job(
        TEST_USER_ID, 'group@g.us', 'message-1', _payload('message-1')
    )
    second = db.enqueue_whatsapp_job(
        TEST_USER_ID, 'group@g.us', 'message-2', _payload('message-2')
    )
    processed_ids = []

    def process(job):
        processed_ids.append(job['id'])
        return 'Готово', [f"Поставка: тест (черновик #{job['id']})"]

    with patch.object(web_app, '_process_whatsapp_job_payload', side_effect=process), \
         patch.object(web_app, 'send_whatsapp_message', return_value=True) as send:
        assert web_app.process_whatsapp_queue(settle_seconds=0) == 2

    assert processed_ids == [first['job_id'], second['job_id']]
    send.assert_called_once()
    summary = send.call_args[0][1]
    assert 'Получено: 2' in summary
    assert f"черновик #{first['job_id']}" in summary
    assert f"черновик #{second['job_id']}" in summary


def test_new_message_cannot_join_a_batch_while_its_summary_is_being_sent(db):
    db.create_user(TEST_USER_ID, 'mock_token', '1', 'https://mock.joinposter.com/api')
    _reset_queue(db)
    first = db.enqueue_whatsapp_job(
        TEST_USER_ID, 'group@g.us', 'message-1', _payload('message-1')
    )
    claimed = db.claim_next_whatsapp_job()
    db.finish_whatsapp_job(claimed['id'], 'Готово', '[]')

    ready = db.get_ready_whatsapp_batches(settle_seconds=0)
    assert [batch['id'] for batch in ready] == [first['batch_id']]

    second = db.enqueue_whatsapp_job(
        TEST_USER_ID, 'group@g.us', 'message-2', _payload('message-2')
    )
    assert second['new_batch'] is True
    assert second['batch_id'] != first['batch_id']


def test_queue_retries_a_transient_failure_without_creating_an_extra_job(db):
    import web_app

    db.create_user(TEST_USER_ID, 'mock_token', '1', 'https://mock.joinposter.com/api')
    _reset_queue(db)
    queued = db.enqueue_whatsapp_job(
        TEST_USER_ID, 'group@g.us', 'message-retry', _payload('message-retry')
    )

    with patch.object(
        web_app,
        '_process_whatsapp_job_payload',
        side_effect=[RuntimeError('temporary failure'), ('Готово', ['Поставка создана'])],
    ) as process, patch.object(web_app, 'send_whatsapp_message', return_value=True):
        assert web_app.process_whatsapp_queue(settle_seconds=0) == 2

    assert process.call_count == 2
    conn = db._get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT status, attempts FROM whatsapp_jobs WHERE id = ?",
        (queued['job_id'],),
    )
    status, attempts = cursor.fetchone()
    conn.close()
    assert status == 'completed'
    assert attempts == 2


def test_batch_summary_marks_unmatched_supply_rows_for_review(db):
    from web_app import _classify_whatsapp_draft_summary

    db.create_user(TEST_USER_ID, 'mock_token', '1', 'https://mock.joinposter.com/api')
    draft_id = db.create_empty_supply_draft(
        telegram_user_id=TEST_USER_ID,
        supplier_name='Тест',
        total_sum=1000,
    )
    item_id = db.add_supply_draft_item(
        supply_draft_id=draft_id,
        item_name='Неизвестный соус',
        quantity=1,
        unit='шт',
        price_per_unit=1000,
    )
    try:
        icon, status = _classify_whatsapp_draft_summary(
            db, f'Поставка: Тест (черновик #{draft_id})'
        )
        assert icon == '❌'
        assert status == 'не найдено позиций: 1'

        db.update_supply_draft_item(
            item_id,
            telegram_user_id=TEST_USER_ID,
            poster_ingredient_id=249,
            poster_ingredient_name='Бургерный соус',
        )
        icon, status = _classify_whatsapp_draft_summary(
            db, f'Поставка: Тест (черновик #{draft_id})'
        )
        assert icon == '✅'
        assert status == 'готов к проверке'
    finally:
        db.delete_supply_draft(draft_id, telegram_user_id=TEST_USER_ID)


def test_memory_action_requires_an_explicit_user_request(db):
    from web_app import execute_assistant_actions

    db.create_user(TEST_USER_ID, 'mock_token', '1', 'https://mock.joinposter.com/api')
    original_memory = db.get_assistant_memory(TEST_USER_ID)
    rule = 'Тестовое правило из WhatsApp не автоматическое'
    action = [{'action': 'add_to_memory', 'rule_text': rule}]
    try:
        execute_assistant_actions(
            TEST_USER_ID, action, '2026-09-01', '', allow_memory_actions=False
        )
        assert rule not in db.get_assistant_memory(TEST_USER_ID)

        execute_assistant_actions(
            TEST_USER_ID, action, '2026-09-01', '', allow_memory_actions=True
        )
        assert rule in db.get_assistant_memory(TEST_USER_ID)
    finally:
        db.save_assistant_memory(TEST_USER_ID, original_memory)
