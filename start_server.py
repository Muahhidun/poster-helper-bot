"""Flask launcher with a background Telegram notification service."""
import sys
import os

current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)
if '/app' not in sys.path:
    sys.path.insert(0, '/app')

import asyncio
import logging
import threading
from bot import initialize_application, configure_notification_only_bot
from config import LOG_LEVEL

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Global variables
telegram_app = None
bot_event_loop = None

# Import Flask app from web_app
from web_app import app

async def setup_notifications():
    """Start outbound Telegram delivery without accepting incoming updates."""
    global bot_event_loop, telegram_app

    try:
        # Store the current event loop
        bot_event_loop = asyncio.get_running_loop()
        logger.info(f"✅ Bot event loop captured")

        # Initialize the Telegram bot application in THIS event loop
        logger.info("🔧 Initializing Telegram notification service...")
        telegram_app = initialize_application(interactive=False)

        # Initialize bot
        await telegram_app.initialize()
        await telegram_app.bot.delete_webhook(drop_pending_updates=True)
        await configure_notification_only_bot(telegram_app)
        await telegram_app.start()
        logger.info("✅ Telegram notification service started; incoming updates disabled")
        return True

    except Exception as e:
        logger.error(f"❌ Error setting up Telegram notifications: {e}", exc_info=True)
        return False

def run_bot_loop():
    """Run the bot event loop in a separate thread"""
    global bot_event_loop

    # Create and set event loop for this thread
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    bot_event_loop = loop

    try:
        notification_success = loop.run_until_complete(setup_notifications())

        if not notification_success:
            logger.warning("⚠️ Telegram notification service failed to start")

        # Keep the loop running for scheduled jobs and outbound messages.
        loop.run_forever()
    except Exception as e:
        logger.error(f"Error in bot event loop: {e}", exc_info=True)
    finally:
        loop.close()

def run_server():
    """Run the server — local dev fallback when gunicorn is not available.

    In production on Railway, gunicorn is used instead (see Procfile).
    The bot thread is started by gunicorn's post_worker_init hook in
    gunicorn_config.py.
    """
    port = int(os.environ.get('PORT', 5000))

    logger.info("=" * 60)
    logger.info("🚀 Starting Flask + Telegram notifications")
    logger.info("=" * 60)
    logger.info(f"📡 Flask will listen on port {port}")

    # Start bot event loop in a separate thread
    bot_thread = threading.Thread(target=run_bot_loop, daemon=True)
    bot_thread.start()

    # Wait a bit for the bot to initialize
    import time
    time.sleep(2)

    # Start Flask dev server (local only)
    logger.info("🎯 Starting Flask dev server...")
    app.run(
        host='0.0.0.0',
        port=port,
        debug=False,
        threaded=True,
        use_reloader=False
    )

if __name__ == '__main__':
    run_server()
