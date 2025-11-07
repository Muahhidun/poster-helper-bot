"""
Shipment Templates - Quick templates for recurring shipments

This module provides functionality for creating and managing templates
for frequently repeated shipments (e.g., bread, ayran, doner marinade).

Users can:
- Create templates with supplier, account, and items
- Edit template details (prices, supplier, etc.)
- Delete templates
- Quick-create shipments with just "Template 400" syntax
"""

import logging
import re
from typing import Optional, Dict, List, Tuple
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from database import get_database
from poster_client import get_poster_client

logger = logging.getLogger(__name__)

CURRENCY = "₸"


# ============================================================================
# Template Management Commands
# ============================================================================

async def templates_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show list of all shipment templates"""
    telegram_user_id = update.effective_user.id
    db = get_database()

    templates = db.get_shipment_templates(telegram_user_id)

    if not templates:
        await update.message.reply_text(
            "📋 У вас пока нет шаблонов поставок.\n\n"
            "Чтобы создать шаблон:\n"
            "1. Создайте обычную поставку\n"
            "2. После проверки нажмите 'Сохранить как шаблон'\n\n"
            "Или используйте команду /create_template"
        )
        return

    # Build list of templates
    message_lines = ["📋 *Ваши шаблоны поставок:*\n"]

    for template in templates:
        items_summary = []
        for item in template['items']:
            items_summary.append(f"{item['name']}: {item['price']:,} {CURRENCY}")

        items_text = "\n    • ".join(items_summary)

        message_lines.append(
            f"*{template['template_name'].upper()}*\n"
            f"  Поставщик: {template['supplier_name']}\n"
            f"  Счет: {template['account_name']}\n"
            f"  Товары:\n    • {items_text}\n"
        )

    message_lines.append(
        "\n💡 *Как использовать:*\n"
        f"Просто напишите: `{templates[0]['template_name']} 400`\n"
        f"где 400 - это количество\n\n"
        "Команды:\n"
        "/edit_template - Редактировать шаблон\n"
        "/delete_template - Удалить шаблон"
    )

    await update.message.reply_text(
        "\n".join(message_lines),
        parse_mode='Markdown'
    )


async def edit_template_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Edit a shipment template"""
    telegram_user_id = update.effective_user.id
    db = get_database()

    templates = db.get_shipment_templates(telegram_user_id)

    if not templates:
        await update.message.reply_text(
            "У вас нет шаблонов для редактирования.\n"
            "Используйте /templates для просмотра."
        )
        return

    # Show template selection buttons
    keyboard = []
    for template in templates:
        keyboard.append([
            InlineKeyboardButton(
                template['template_name'].upper(),
                callback_data=f"edit_template:{template['template_name']}"
            )
        ])

    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Выберите шаблон для редактирования:",
        reply_markup=reply_markup
    )


async def delete_template_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete a shipment template"""
    telegram_user_id = update.effective_user.id
    db = get_database()

    templates = db.get_shipment_templates(telegram_user_id)

    if not templates:
        await update.message.reply_text(
            "У вас нет шаблонов для удаления.\n"
            "Используйте /templates для просмотра."
        )
        return

    # Show template selection buttons
    keyboard = []
    for template in templates:
        keyboard.append([
            InlineKeyboardButton(
                template['template_name'].upper(),
                callback_data=f"delete_template:{template['template_name']}"
            )
        ])

    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "⚠️ Выберите шаблон для удаления:",
        reply_markup=reply_markup
    )


# ============================================================================
# Quick Shipment Creation
# ============================================================================

def try_parse_quick_template(text: str) -> Optional[Tuple[str, int]]:
    """
    Try to parse quick template syntax like "лаваш 400" or "айран 50"

    Returns:
        Tuple of (template_name, quantity) or None if not a match
    """
    # Pattern: word(s) + number
    # Examples: "лаваш 400", "донер маринад 50", "ayran 100"
    pattern = r'^([а-яёa-z\s]+?)\s+(\d+)$'
    match = re.match(pattern, text.strip().lower(), re.IGNORECASE)

    if match:
        template_name = match.group(1).strip()
        quantity = int(match.group(2))
        return (template_name, quantity)

    return None


async def create_shipment_from_template(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    template_name: str,
    quantity: int
) -> bool:
    """
    Create a shipment draft from a template

    Returns:
        True if successful, False otherwise
    """
    telegram_user_id = update.effective_user.id
    db = get_database()

    # Get template
    template = db.get_shipment_template(telegram_user_id, template_name)

    if not template:
        logger.info(f"Template '{template_name}' not found for user {telegram_user_id}")
        return False

    logger.info(f"📦 Creating shipment from template '{template_name}' with quantity {quantity}")

    # Build items list with the specified quantity
    items = []
    total_amount = 0

    for template_item in template['items']:
        item_sum = int(quantity * template_item['price'])
        items.append({
            'id': template_item['id'],
            'name': template_item['name'],
            'num': quantity,
            'price': template_item['price'],
            'sum': item_sum,
            'match_score': 100,  # Perfect match since it's from template
            'original_name': template_item['name']
        })
        total_amount += item_sum

    # Build supply draft
    draft = {
        'type': 'supply',
        'supplier_id': template['supplier_id'],
        'supplier_name': template['supplier_name'],
        'account_id': template['account_id'],
        'account_name': template['account_name'],
        'storage_id': template.get('storage_id', 1),
        'storage_name': 'Продукты',
        'items': items,
        'total_amount': total_amount,
        'date': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        'from_template': template_name  # Mark that this is from a template
    }

    # Import show_supply_draft from bot.py to display the draft
    from bot import show_supply_draft

    # Show supply draft
    message = await show_supply_draft(update, context, draft)

    # Store draft with message_id as key
    if message:
        if 'drafts' not in context.user_data:
            context.user_data['drafts'] = {}
        context.user_data['drafts'][message.message_id] = draft
        context.user_data['current_message_id'] = message.message_id
        logger.info(f"✅ Template draft created: template={template_name}, quantity={quantity}")

        # Add hint about saving as template
        await update.message.reply_text(
            f"📦 Поставка создана из шаблона *{template_name.upper()}*\n\n"
            "💡 Если цена изменилась, можете обновить шаблон после активации.",
            parse_mode='Markdown'
        )

    return True


# ============================================================================
# Save Draft as Template
# ============================================================================

async def save_draft_as_template(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    draft: Dict
):
    """Save a supply draft as a template"""
    telegram_user_id = update.effective_user.id

    # Ask for template name
    context.user_data['pending_template_draft'] = draft

    await update.callback_query.message.reply_text(
        "📝 Введите название для шаблона:\n"
        "Например: лаваш, айран, донер маринад\n\n"
        "Это название будет использоваться для быстрого создания поставок."
    )

    # Set state to wait for template name
    context.user_data['waiting_for_template_name'] = True


async def handle_template_name_input(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    template_name: str
) -> bool:
    """Handle template name input and save the template"""
    if not context.user_data.get('waiting_for_template_name'):
        return False

    telegram_user_id = update.effective_user.id
    db = get_database()

    draft = context.user_data.get('pending_template_draft')
    if not draft:
        await update.message.reply_text("❌ Ошибка: черновик не найден.")
        return True

    # Prepare items for template (store only id, name, and price)
    template_items = []
    for item in draft['items']:
        template_items.append({
            'id': item['id'],
            'name': item['name'],
            'price': item['price']
        })

    # Create template
    success = db.create_shipment_template(
        telegram_user_id=telegram_user_id,
        template_name=template_name,
        supplier_id=draft['supplier_id'],
        supplier_name=draft['supplier_name'],
        account_id=draft['account_id'],
        account_name=draft['account_name'],
        items=template_items,
        storage_id=draft.get('storage_id', 1)
    )

    if success:
        await update.message.reply_text(
            f"✅ Шаблон *{template_name.upper()}* сохранен!\n\n"
            f"Теперь можете использовать:\n"
            f"`{template_name} <количество>`\n\n"
            f"Например: `{template_name} 400`",
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            "❌ Ошибка при сохранении шаблона. "
            "Возможно, шаблон с таким именем уже существует."
        )

    # Clean up context
    context.user_data.pop('waiting_for_template_name', None)
    context.user_data.pop('pending_template_draft', None)

    return True


# ============================================================================
# Callback Handlers
# ============================================================================

async def handle_edit_template_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    template_name: str
):
    """Handle edit template callback"""
    query = update.callback_query
    await query.answer()

    telegram_user_id = update.effective_user.id
    db = get_database()

    template = db.get_shipment_template(telegram_user_id, template_name)

    if not template:
        await query.message.edit_text("❌ Шаблон не найден.")
        return

    # Show template details with edit options
    items_text = []
    for idx, item in enumerate(template['items']):
        items_text.append(f"{idx+1}. {item['name']}: {item['price']:,} {CURRENCY}")

    message = (
        f"📝 Редактирование шаблона *{template['template_name'].upper()}*\n\n"
        f"Поставщик: {template['supplier_name']}\n"
        f"Счет: {template['account_name']}\n\n"
        f"Товары:\n" + "\n".join(items_text) + "\n\n"
        "Что хотите изменить?"
    )

    keyboard = [
        [InlineKeyboardButton("💰 Изменить цены", callback_data=f"edit_template_prices:{template_name}")],
        [InlineKeyboardButton("🏪 Изменить поставщика", callback_data=f"edit_template_supplier:{template_name}")],
        [InlineKeyboardButton("💳 Изменить счет", callback_data=f"edit_template_account:{template_name}")],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel")]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.message.edit_text(message, reply_markup=reply_markup, parse_mode='Markdown')


async def handle_delete_template_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    template_name: str
):
    """Handle delete template callback"""
    query = update.callback_query
    await query.answer()

    telegram_user_id = update.effective_user.id
    db = get_database()

    # Confirm deletion
    keyboard = [
        [InlineKeyboardButton("✅ Да, удалить", callback_data=f"confirm_delete_template:{template_name}")],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel")]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.message.edit_text(
        f"⚠️ Вы уверены, что хотите удалить шаблон *{template_name.upper()}*?",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )


async def handle_confirm_delete_template_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    template_name: str
):
    """Handle confirm delete template callback"""
    query = update.callback_query
    await query.answer()

    telegram_user_id = update.effective_user.id
    db = get_database()

    success = db.delete_shipment_template(telegram_user_id, template_name)

    if success:
        await query.message.edit_text(
            f"✅ Шаблон *{template_name.upper()}* удален.",
            parse_mode='Markdown'
        )
    else:
        await query.message.edit_text(
            f"❌ Ошибка при удалении шаблона *{template_name.upper()}*.",
            parse_mode='Markdown'
        )


async def handle_edit_template_prices_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    template_name: str
):
    """Handle edit template prices callback"""
    query = update.callback_query
    await query.answer()

    telegram_user_id = update.effective_user.id
    db = get_database()

    template = db.get_shipment_template(telegram_user_id, template_name)

    if not template:
        await query.message.edit_text("❌ Шаблон не найден.")
        return

    # Store template for later
    context.user_data['editing_template'] = template_name
    context.user_data['waiting_for_template_prices'] = True

    items_text = []
    for idx, item in enumerate(template['items']):
        items_text.append(f"{idx+1}. {item['name']}: {item['price']:,} {CURRENCY}")

    await query.message.edit_text(
        f"📝 Введите новые цены для шаблона *{template_name.upper()}*\n\n"
        f"Текущие цены:\n" + "\n".join(items_text) + "\n\n"
        "Формат: просто укажите новую цену (если товар один)\n"
        "или укажите номер позиции и новую цену: `1 45`\n\n"
        "Например: `45` (для одного товара) или `1 45` (для первой позиции)",
        parse_mode='Markdown'
    )


async def handle_template_price_update(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str
) -> bool:
    """Handle template price update input"""
    if not context.user_data.get('waiting_for_template_prices'):
        return False

    telegram_user_id = update.effective_user.id
    db = get_database()

    template_name = context.user_data.get('editing_template')
    if not template_name:
        await update.message.reply_text("❌ Ошибка: шаблон не найден.")
        return True

    template = db.get_shipment_template(telegram_user_id, template_name)
    if not template:
        await update.message.reply_text("❌ Ошибка: шаблон не найден.")
        return True

    # Parse input: either "price" or "index price"
    parts = text.strip().split()

    try:
        if len(parts) == 1:
            # Single item template
            new_price = int(parts[0])
            if len(template['items']) != 1:
                await update.message.reply_text(
                    "❌ В шаблоне несколько товаров. Укажите номер позиции: `1 45`",
                    parse_mode='Markdown'
                )
                return True
            template['items'][0]['price'] = new_price
        elif len(parts) == 2:
            # Multi-item template
            item_index = int(parts[0]) - 1
            new_price = int(parts[1])
            if item_index < 0 or item_index >= len(template['items']):
                await update.message.reply_text("❌ Неверный номер позиции.")
                return True
            template['items'][item_index]['price'] = new_price
        else:
            await update.message.reply_text(
                "❌ Неверный формат. Используйте: `цена` или `номер цена`",
                parse_mode='Markdown'
            )
            return True

        # Update template
        success = db.update_shipment_template(
            telegram_user_id=telegram_user_id,
            template_name=template_name,
            items=template['items']
        )

        if success:
            items_text = []
            for idx, item in enumerate(template['items']):
                items_text.append(f"{idx+1}. {item['name']}: {item['price']:,} {CURRENCY}")

            await update.message.reply_text(
                f"✅ Цены обновлены для шаблона *{template_name.upper()}*\n\n"
                f"Новые цены:\n" + "\n".join(items_text),
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text("❌ Ошибка при обновлении шаблона.")

    except ValueError:
        await update.message.reply_text("❌ Ошибка: введите числовое значение.")

    # Clean up context
    context.user_data.pop('waiting_for_template_prices', None)
    context.user_data.pop('editing_template', None)

    return True
