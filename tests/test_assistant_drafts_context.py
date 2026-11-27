"""Повторная отправка накладной после удаления черновика (Кока-кола, 27.11.2026)."""
from parser_service import format_active_drafts_for_assistant, ASSISTANT_SYSTEM_PROMPT

EXPENSE = {'id': 11718, 'description': 'Кока-кола', 'amount': 121026, 'expense_type': 'supply',
           'source': 'kaspi', 'category': 'Прочее'}


def test_expense_without_supply_draft_is_marked_unfilled():
    text = format_active_drafts_for_assistant([EXPENSE])
    assert 'Расход ID: 11718' in text
    assert 'ещё НЕ добавлены' in text


def test_expense_with_filled_supply_draft_is_not_marked():
    supply = {'id': 900, 'type': 'supply', 'linked_expense_draft_id': 11718,
              'supplier_name': 'Кока-кола', 'total_sum': 121026, 'items': [{'id': 1}]}
    text = format_active_drafts_for_assistant([EXPENSE, supply])
    assert 'ещё НЕ добавлены' not in text
    assert 'позиций уже заполнено: 1' in text


def test_expense_already_sent_to_poster_is_not_marked():
    sent = dict(EXPENSE, poster_transaction_id='supply_1234')
    assert 'ещё НЕ добавлены' not in format_active_drafts_for_assistant([sent])


def test_transaction_expense_is_not_marked():
    salary = dict(EXPENSE, expense_type='transaction')
    assert 'ещё НЕ добавлены' not in format_active_drafts_for_assistant([salary])


def test_prompt_forbids_refusing_by_chat_history():
    assert 'Никогда не отказывайся обрабатывать присланную накладную' in ASSISTANT_SYSTEM_PROMPT
