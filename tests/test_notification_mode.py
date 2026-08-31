"""Telegram stays available for reports without accepting operational commands."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


def test_notification_application_has_no_incoming_handlers():
    import bot

    with (
        patch.object(bot, 'TELEGRAM_BOT_TOKEN', '123456:ABCDEF_fake_token'),
        patch.object(bot, 'validate_config'),
        patch.object(bot, 'get_database'),
        patch.object(bot, 'sync_ingredients_if_needed'),
        patch.object(bot, 'sync_products_if_needed'),
        patch.object(bot, 'fix_user_poster_urls'),
        patch.object(bot, 'migrate_csv_aliases_to_db'),
        patch.object(bot, 'setup_scheduler'),
    ):
        application = bot.initialize_application(interactive=False)

    assert application.handlers == {}
    assert [job.name for job in application.job_queue.jobs()] == ['auto_sync_poster']


@pytest.mark.asyncio
async def test_notification_mode_removes_commands_and_mini_app_button():
    from bot import configure_notification_only_bot

    fake_bot = SimpleNamespace(
        delete_my_commands=AsyncMock(),
        set_chat_menu_button=AsyncMock(),
    )
    await configure_notification_only_bot(SimpleNamespace(bot=fake_bot))

    fake_bot.delete_my_commands.assert_awaited_once()
    fake_bot.set_chat_menu_button.assert_awaited_once()


def test_legacy_telegram_and_mini_app_routes_are_not_registered():
    from web_app import app

    routes = {rule.rule for rule in app.url_map.iter_rules()}
    assert '/telegram-webhook' not in routes
    assert '/mini-app' not in routes
    assert '/mini-app/' not in routes
