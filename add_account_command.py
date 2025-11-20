"""
Admin command to add Pizzburg-cafe account
"""
from database import get_database
import logging

logger = logging.getLogger(__name__)

async def add_second_account_command(update, context):
    """Add Pizzburg-cafe account for admin user"""
    user_id = update.effective_user.id

    # Only for specific admin
    if user_id != 167084307:
        await update.message.reply_text("⛔ Эта команда доступна только администратору")
        return

    db = get_database()

    # Check if account already exists
    existing = db.get_account_by_name(user_id, "Pizzburg-cafe")
    if existing:
        await update.message.reply_text("✅ Аккаунт 'Pizzburg-cafe' уже существует!")

        # Show all accounts
        accounts = db.get_accounts(user_id)
        msg = "📋 Ваши аккаунты:\n\n"
        for acc in accounts:
            primary = " (ОСНОВНОЙ)" if acc.get('is_primary') else ""
            msg += f"  • {acc['account_name']}{primary}\n"

        await update.message.reply_text(msg)
        return

    # Add Pizzburg-cafe account
    success = db.add_account(
        telegram_user_id=user_id,
        account_name="Pizzburg-cafe",
        poster_token="881862:431800518a877398e5c4d1d3b9c76cee",
        poster_user_id="881862",
        poster_base_url="https://joinposter.com/api",
        is_primary=False
    )

    if success:
        # Show all accounts
        accounts = db.get_accounts(user_id)
        msg = "✅ Аккаунт 'Pizzburg-cafe' успешно добавлен!\n\n📋 Ваши аккаунты:\n\n"
        for acc in accounts:
            primary = " (ОСНОВНОЙ)" if acc.get('is_primary') else ""
            msg += f"  • {acc['account_name']}{primary}\n"

        msg += "\n🎯 Теперь можно переходить к Этапу 2: синхронизация справочников из обоих отделов"

        await update.message.reply_text(msg)
        logger.info(f"✅ Pizzburg-cafe account added for user {user_id}")
    else:
        await update.message.reply_text("❌ Ошибка добавления аккаунта. Проверьте логи.")
        logger.error(f"Failed to add Pizzburg-cafe account for user {user_id}")
