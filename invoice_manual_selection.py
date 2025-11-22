"""UI для ручного выбора товаров из кандидатов при обработке накладных"""
import logging
from typing import Dict, List
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


async def show_skipped_items_ui(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    skipped_items_with_candidates: List[Dict],
    skipped_items_no_candidates: List[str],
    supply_draft_result: Dict
):
    """
    Показать UI для выбора товаров с кандидатами

    Args:
        update: Telegram update
        context: Bot context
        skipped_items_with_candidates: Список товаров с кандидатами
        skipped_items_no_candidates: Список товаров без кандидатов
        supply_draft_result: Результат создания черновиков
    """
    # Если есть товары без кандидатов - показать их отдельно
    if skipped_items_no_candidates:
        no_cand_text = (
            f"❌ **Не найдено** ({len(skipped_items_no_candidates)} шт.):\n"
            + "\n".join([f"  • {name}" for name in skipped_items_no_candidates[:10]])
        )
        if len(skipped_items_no_candidates) > 10:
            no_cand_text += f"\n  ... и ещё {len(skipped_items_no_candidates) - 10} шт."

        await update.message.reply_text(no_cand_text, parse_mode='Markdown')

    # Если нет товаров с кандидатами - выход
    if not skipped_items_with_candidates:
        return

    # Сохранить контекст для последующих шагов
    context.user_data['invoice_manual_selection'] = {
        'skipped_items_with_candidates': skipped_items_with_candidates,
        'supply_draft_result': supply_draft_result,
        'current_item_index': 0,
        'selected_items': []  # Товары которые пользователь выбрал
    }

    # Показать первый товар для выбора
    await show_candidate_selection(update, context, 0)


async def show_candidate_selection(update: Update, context: ContextTypes.DEFAULT_TYPE, item_index: int):
    """
    Показать кандидатов для конкретного товара

    Args:
        update: Telegram update
        context: Bot context
        item_index: Индекс товара в списке skipped_items_with_candidates
    """
    selection_data = context.user_data.get('invoice_manual_selection')
    if not selection_data:
        logger.error("No invoice_manual_selection data in context")
        return

    skipped_items = selection_data['skipped_items_with_candidates']

    if item_index >= len(skipped_items):
        # Все товары обработаны - завершить
        await finalize_manual_selection(update, context)
        return

    item = skipped_items[item_index]
    candidates = item['candidates']

    # Формируем текст сообщения
    message_text = (
        f"🔍 **Товар не найден:** `{item['name']}`\n"
        f"📦 Количество: {item['quantity']} {item['unit']}\n"
        f"💰 Цена: {item['price']}₸\n\n"
        f"**Похожие товары:**\n"
    )

    # Создать кнопки для каждого кандидата
    keyboard = []
    for idx, candidate in enumerate(candidates, 1):
        # candidate: (ingredient_id, name, unit, score, account_name)
        ing_id, ing_name, ing_unit, score, account_name = candidate

        button_text = f"{idx}. {ing_name[:30]} ({account_name}) - {score:.0f}%"
        callback_data = f"invoice_select:{item_index}:{ing_id}:{account_name}"

        keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])

    # Добавить кнопки "Пропустить" и "Отмена"
    keyboard.append([
        InlineKeyboardButton("⏭️ Пропустить этот товар", callback_data=f"invoice_skip:{item_index}")
    ])
    keyboard.append([
        InlineKeyboardButton("❌ Завершить выбор", callback_data="invoice_finish")
    ])

    reply_markup = InlineKeyboardMarkup(keyboard)

    # Отправить или обновить сообщение
    if update.callback_query:
        await update.callback_query.edit_message_text(
            message_text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            message_text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )


async def handle_candidate_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обработать выбор кандидата пользователем

    Callback data format: invoice_select:{item_index}:{ingredient_id}:{account_name}
    """
    query = update.callback_query
    await query.answer()

    selection_data = context.user_data.get('invoice_manual_selection')
    if not selection_data:
        await query.edit_message_text("❌ Сессия выбора истекла. Попробуйте загрузить накладную заново.")
        return

    # Парсим callback data
    parts = query.data.split(':')
    item_index = int(parts[1])
    ingredient_id = int(parts[2])
    account_name = parts[3]

    # Получаем товар
    skipped_items = selection_data['skipped_items_with_candidates']
    item = skipped_items[item_index]

    # Находим выбранного кандидата для получения полной информации
    selected_candidate = None
    for candidate in item['candidates']:
        if candidate[0] == ingredient_id:
            selected_candidate = candidate
            break

    if not selected_candidate:
        await query.edit_message_text("❌ Ошибка: выбранный товар не найден")
        return

    ing_id, ing_name, ing_unit, score, acc_name = selected_candidate

    # Сохраняем выбор
    selection_data['selected_items'].append({
        'original_name': item['name'],
        'ingredient_id': ing_id,
        'ingredient_name': ing_name,
        'account_name': acc_name,
        'quantity': item['quantity'],
        'unit': item['unit'],
        'price': item['price']
    })

    logger.info(f"✅ User selected: '{item['name']}' -> {ing_name} (ID={ing_id}, account={acc_name})")

    # Переходим к следующему товару
    next_index = item_index + 1
    selection_data['current_item_index'] = next_index
    await show_candidate_selection(update, context, next_index)


async def handle_skip_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Пропустить текущий товар

    Callback data format: invoice_skip:{item_index}
    """
    query = update.callback_query
    await query.answer("⏭️ Товар пропущен")

    selection_data = context.user_data.get('invoice_manual_selection')
    if not selection_data:
        await query.edit_message_text("❌ Сессия выбора истекла")
        return

    # Парсим callback data
    item_index = int(query.data.split(':')[1])

    # Переходим к следующему товару
    next_index = item_index + 1
    selection_data['current_item_index'] = next_index
    await show_candidate_selection(update, context, next_index)


async def finalize_manual_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Завершить ручной выбор и создать дополнительные поставки
    """
    query = update.callback_query if update.callback_query else None

    selection_data = context.user_data.get('invoice_manual_selection')
    if not selection_data:
        message = "❌ Нет данных для завершения"
        if query:
            await query.edit_message_text(message)
        else:
            await update.message.reply_text(message)
        return

    selected_items = selection_data['selected_items']

    if not selected_items:
        message = "✅ Выбор завершен. Дополнительных товаров не выбрано."
        if query:
            await query.edit_message_text(message)
        else:
            await update.message.reply_text(message)

        # Очистить контекст
        context.user_data.pop('invoice_manual_selection', None)
        return

    # TODO: Создать дополнительные черновики для выбранных товаров
    # Это можно реализовать позже - сейчас просто показываем что было выбрано

    summary_text = (
        f"✅ Выбор завершен! Выбрано товаров: {len(selected_items)}\n\n"
        "**Выбранные товары:**\n"
    )

    for item in selected_items:
        summary_text += (
            f"• {item['original_name']} → {item['ingredient_name']} "
            f"({item['account_name']})\n"
        )

    summary_text += "\n💡 Сейчас эти товары не добавлены в черновики автоматически. В будущем обновлении они будут создаваться отдельно."

    if query:
        await query.edit_message_text(summary_text, parse_mode='Markdown')
    else:
        await update.message.reply_text(summary_text, parse_mode='Markdown')

    # Очистить контекст
    context.user_data.pop('invoice_manual_selection', None)
