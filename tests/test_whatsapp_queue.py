"""Regression tests for durable, sequential WhatsApp batch processing."""

import json
from unittest.mock import patch

from tests.conftest import TEST_USER_ID


def _reset_queue(db):
    conn = db._get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM whatsapp_review_messages")
    cursor.execute("DELETE FROM whatsapp_reviews")
    cursor.execute("DELETE FROM whatsapp_draft_actions")
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


def test_unmatched_row_is_resolved_by_numeric_choices_without_implicit_learning(db):
    import web_app

    db.create_user(TEST_USER_ID, 'mock_token', '1', 'https://mock.joinposter.com/api')
    _reset_queue(db)
    draft_id = db.create_empty_supply_draft(
        telegram_user_id=TEST_USER_ID,
        supplier_name='Япоша',
        total_sum=28900,
    )
    item_id = db.add_supply_draft_item(
        supply_draft_id=draft_id,
        item_name='Сыр творожный RASA 66%',
        quantity=10,
        unit='кг',
        price_per_unit=2890,
    )
    candidates = [
        {
            'item_id': 110,
            'name': 'Кремета Хохланд (2,5кг)',
            'unit': '',
            'account_name': 'Pizzburg',
            'item_type': 'ingredient',
            'score': 96,
        },
        {
            'item_id': 23,
            'name': 'Кремета Хохланд 2.2кг ведро',
            'unit': '',
            'account_name': 'Pizzburg-cafe',
            'item_type': 'ingredient',
            'score': 96,
        },
    ]
    try:
        review_id = db.enqueue_whatsapp_review(
            TEST_USER_ID,
            'group@g.us',
            None,
            draft_id,
            item_id,
            'Сыр творожный RASA 66%',
            json.dumps(candidates, ensure_ascii=False),
        )
        with patch.object(web_app, 'send_whatsapp_message', return_value=True) as send:
            assert web_app._send_pending_whatsapp_review_prompts(db) == 1
            assert 'Ответьте одной цифрой' in send.call_args[0][1]

            assert web_app._handle_whatsapp_review_reply(
                db, TEST_USER_ID, 'group@g.us', '1', message_id='reply-1'
            ) is True
            active = db.get_active_whatsapp_review(TEST_USER_ID, 'group@g.us')
            assert active['status'] == 'awaiting_memory'

            # A repeated Green-API webhook must not use the same "1" as the
            # answer to the next question.
            assert web_app._handle_whatsapp_review_reply(
                db, TEST_USER_ID, 'group@g.us', '1', message_id='reply-1'
            ) is True
            assert db.get_active_whatsapp_review(TEST_USER_ID, 'group@g.us')['status'] == 'awaiting_memory'

            assert web_app._handle_whatsapp_review_reply(
                db, TEST_USER_ID, 'group@g.us', '2', message_id='reply-2'
            ) is True

        assert db.get_active_whatsapp_review(TEST_USER_ID, 'group@g.us') is None
        updated = db.get_supply_draft_with_items(draft_id)['items'][0]
        assert updated['poster_ingredient_id'] == 110
        assert updated['poster_ingredient_name'] == 'Кремета Хохланд (2,5кг)'
        assert updated['item_name'] == 'Сыр творожный RASA 66%'
        aliases = db.get_ingredient_aliases(TEST_USER_ID)
        assert not any(
            alias['alias_text'] == 'сыр творожный rasa 66%'
            for alias in aliases
        )
    finally:
        conn = db._get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM whatsapp_review_messages")
        cursor.execute("DELETE FROM whatsapp_reviews")
        conn.commit()
        conn.close()
        db.delete_supply_draft(draft_id, telegram_user_id=TEST_USER_ID)


def test_candidate_options_include_cross_account_cream_cheese_and_exact_fuse_tea():
    from web_app import _whatsapp_candidate_options

    cheese = _whatsapp_candidate_options(TEST_USER_ID, 'Сыр творожный RASA 66%')
    assert [(item['item_id'], item['account_name']) for item in cheese[:2]] == [
        (110, 'Pizzburg'),
        (23, 'Pizzburg-cafe'),
    ]

    fuse = _whatsapp_candidate_options(TEST_USER_ID, 'Fuse Tea 1 литр')
    assert fuse[0]['item_id'] == 55
    assert fuse[0]['name'] == 'Фьюс чай 1л'
    assert fuse[0]['score'] == 100

    mustard = _whatsapp_candidate_options(TEST_USER_ID, 'соус горчичный')
    assert [(item['item_id'], item['account_name']) for item in mustard[:2]] == [
        (280, 'Pizzburg-cafe'),
        (130, 'Pizzburg'),
    ]


def _create_ready_supply_draft(db, supplier_name: str, amount: float) -> int:
    draft_id = db.create_empty_supply_draft(
        telegram_user_id=TEST_USER_ID,
        supplier_name=supplier_name,
        invoice_date='2026-09-02',
        total_sum=amount,
        source='kaspi',
    )
    db.add_supply_draft_item(
        supply_draft_id=draft_id,
        item_name='Масло',
        quantity=5,
        unit='л',
        price_per_unit=amount / 5,
        poster_ingredient_id=77,
        poster_ingredient_name='Оливковое масло 5л',
        poster_account_id=1,
        poster_account_name='Pizzburg',
    )
    return draft_id


def test_ready_drafts_are_previewed_and_answered_one_at_a_time(db):
    import web_app

    db.create_user(TEST_USER_ID, 'mock_token', '1', 'https://mock.joinposter.com/api')
    _reset_queue(db)
    first_id = _create_ready_supply_draft(db, 'Япоша', 9900)
    second_id = _create_ready_supply_draft(db, 'Идея', 5000)
    try:
        db.enqueue_whatsapp_draft_action(TEST_USER_ID, 'group@g.us', None, first_id)
        db.enqueue_whatsapp_draft_action(TEST_USER_ID, 'group@g.us', None, second_id)
        with patch.object(web_app, 'send_whatsapp_message', return_value=True) as send:
            assert web_app._send_next_whatsapp_prompt(db, 'group@g.us') == 1
            first_prompt = send.call_args.args[1]
            assert f'черновика #{first_id}' in first_prompt
            assert 'Оливковое масло 5л — 5 л × 1 980 = 9 900 ₸ [Pizzburg]' in first_prompt
            assert '1. ✅ Создать поставку в Poster' in first_prompt

            assert web_app._handle_whatsapp_draft_action_reply(
                db, TEST_USER_ID, 'group@g.us', '2', message_id='keep-first'
            ) is True
            assert any(
                f'черновика #{second_id}' in call.args[1]
                for call in send.call_args_list
            )
    finally:
        db.delete_supply_draft(first_id, telegram_user_id=TEST_USER_ID)
        db.delete_supply_draft(second_id, telegram_user_id=TEST_USER_ID)


def test_draft_is_posted_only_after_explicit_choice_and_duplicate_is_ignored(db):
    import web_app

    db.create_user(TEST_USER_ID, 'mock_token', '1', 'https://mock.joinposter.com/api')
    _reset_queue(db)
    draft_id = _create_ready_supply_draft(db, 'Япоша', 9900)
    try:
        action_id = db.enqueue_whatsapp_draft_action(
            TEST_USER_ID, 'group@g.us', None, draft_id
        )
        db.mark_whatsapp_draft_action_prompted(action_id)
        poster_result = {
            'success': True,
            'supply_id': 123,
            'supplies': [{'account_name': 'Pizzburg', 'supply_id': 123}],
        }
        with patch.object(
            web_app, '_process_supply_draft_for_user', return_value=poster_result
        ) as process, patch.object(
            web_app, 'send_whatsapp_message', return_value=True
        ) as send:
            assert web_app._handle_whatsapp_draft_action_reply(
                db, TEST_USER_ID, 'group@g.us', '1', message_id='create-once'
            ) is True
            process.assert_called_once_with(draft_id, TEST_USER_ID)
            assert 'поставка #123' in send.call_args_list[0].args[1]
            assert db.is_whatsapp_interaction_message_handled(
                'group@g.us', 'create-once'
            ) is True
            assert web_app._handle_whatsapp_draft_action_reply(
                db, TEST_USER_ID, 'group@g.us', '1', message_id='create-once'
            ) is False

        conn = db._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT status FROM whatsapp_draft_actions WHERE id = ?", (action_id,)
        )
        assert cursor.fetchone()[0] == 'created'
        conn.close()
    finally:
        db.delete_supply_draft(draft_id, telegram_user_id=TEST_USER_ID)


def test_unmatched_draft_is_never_offered_for_poster_submission(db):
    import web_app

    db.create_user(TEST_USER_ID, 'mock_token', '1', 'https://mock.joinposter.com/api')
    _reset_queue(db)
    draft_id = db.create_empty_supply_draft(
        telegram_user_id=TEST_USER_ID,
        supplier_name='Тест',
        total_sum=1000,
    )
    db.add_supply_draft_item(
        supply_draft_id=draft_id,
        item_name='Неизвестная позиция',
        quantity=1,
        price_per_unit=1000,
    )
    try:
        action_id = db.enqueue_whatsapp_draft_action(
            TEST_USER_ID, 'group@g.us', None, draft_id
        )
        with patch.object(web_app, 'send_whatsapp_message', return_value=True) as send:
            assert web_app._send_pending_whatsapp_draft_action_prompts(
                db, 'group@g.us'
            ) == 0
            assert 'В Poster ничего не отправлено' in send.call_args.args[1]
        conn = db._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT status FROM whatsapp_draft_actions WHERE id = ?", (action_id,)
        )
        assert cursor.fetchone()[0] == 'blocked'
        conn.close()
    finally:
        db.delete_supply_draft(draft_id, telegram_user_id=TEST_USER_ID)


def test_new_review_waits_until_current_draft_decision_is_answered(db):
    import web_app

    db.create_user(TEST_USER_ID, 'mock_token', '1', 'https://mock.joinposter.com/api')
    _reset_queue(db)
    ready_id = _create_ready_supply_draft(db, 'Япоша', 9900)
    unmatched_id = db.create_empty_supply_draft(
        telegram_user_id=TEST_USER_ID, supplier_name='Идея', total_sum=1000
    )
    item_id = db.add_supply_draft_item(
        supply_draft_id=unmatched_id,
        item_name='Новый соус',
        quantity=1,
        price_per_unit=1000,
    )
    try:
        action_id = db.enqueue_whatsapp_draft_action(
            TEST_USER_ID, 'group@g.us', None, ready_id
        )
        db.mark_whatsapp_draft_action_prompted(action_id)
        db.enqueue_whatsapp_review(
            TEST_USER_ID, 'group@g.us', None, unmatched_id, item_id,
            'Новый соус', '[]',
        )
        with patch.object(web_app, 'send_whatsapp_message', return_value=True) as send:
            assert web_app._send_next_whatsapp_prompt(db, 'group@g.us') == 0
            send.assert_not_called()

            assert web_app._handle_whatsapp_draft_action_reply(
                db, TEST_USER_ID, 'group@g.us', '2', message_id='keep-before-review'
            ) is True
            assert any(
                'Что это в Poster?' in call.args[1]
                for call in send.call_args_list
            )
    finally:
        db.delete_supply_draft(ready_id, telegram_user_id=TEST_USER_ID)
        db.delete_supply_draft(unmatched_id, telegram_user_id=TEST_USER_ID)
