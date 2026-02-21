"""
Unified launcher for Telegram Bot and Flask Web App
Integrates Telegram webhook with Flask
"""
import os
import asyncio
import logging
import threading
from flask import Flask, request
from telegram import Update
from bot import initialize_application
from config import WEBHOOK_URL, WEBHOOK_PATH, TELEGRAM_BOT_TOKEN, LOG_LEVEL

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

# Add webhook endpoint to Flask app
@app.route(WEBHOOK_PATH, methods=['POST'])
def telegram_webhook():
    """Handle incoming Telegram updates via webhook"""
    try:
        # Check if bot is initialized
        if not telegram_app or not bot_event_loop:
            logger.error("Bot not initialized yet")
            return 'Service Unavailable', 503

        # Get the update from request
        update_data = request.get_json(force=True)
        update = Update.de_json(update_data, telegram_app.bot)

        # Schedule update processing in the bot's event loop
        asyncio.run_coroutine_threadsafe(
            telegram_app.update_queue.put(update),
            bot_event_loop
        )

        return 'OK', 200
    except Exception as e:
        logger.error(f"Error processing webhook: {e}", exc_info=True)
        return 'Error', 500

async def setup_webhook():
    """Setup webhook on Telegram"""
    global bot_event_loop, telegram_app

    try:
        # Store the current event loop
        bot_event_loop = asyncio.get_running_loop()
        logger.info(f"✅ Bot event loop captured")

        # Initialize the Telegram bot application in THIS event loop
        logger.info("🔧 Initializing Telegram bot...")
        telegram_app = initialize_application()

        # Validate WEBHOOK_URL is set
        if not WEBHOOK_URL:
            logger.error("❌ WEBHOOK_URL is not set in environment variables!")
            logger.error("   Please add WEBHOOK_URL to Railway environment variables")
            logger.error("   Example: WEBHOOK_URL=https://your-app.up.railway.app")
            return False

        webhook_url = f"{WEBHOOK_URL}{WEBHOOK_PATH}"
        logger.info(f"🔗 Setting up webhook: {webhook_url}")

        # Initialize bot
        await telegram_app.initialize()
        await telegram_app.start()

        # Set webhook
        await telegram_app.bot.set_webhook(
            url=webhook_url,
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True
        )

        webhook_info = await telegram_app.bot.get_webhook_info()
        logger.info(f"✅ Webhook set successfully!")
        logger.info(f"   URL: {webhook_info.url}")
        logger.info(f"   Pending updates: {webhook_info.pending_update_count}")
        return True

    except Exception as e:
        logger.error(f"❌ Error setting up webhook: {e}", exc_info=True)
        return False

def run_bot_loop():
    """Run the bot event loop in a separate thread"""
    global bot_event_loop

    # Create and set event loop for this thread
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    bot_event_loop = loop

    try:
        # Setup webhook
        webhook_success = loop.run_until_complete(setup_webhook())

        if not webhook_success:
            logger.warning("⚠️  Webhook setup failed")
            logger.warning("   Add WEBHOOK_URL environment variable to enable webhook")

        # Keep the loop running to process updates
        loop.run_forever()
    except Exception as e:
        logger.error(f"Error in bot event loop: {e}", exc_info=True)
    finally:
        loop.close()

def run_server():
    """Run the Flask server with webhook"""
    port = int(os.environ.get('PORT', 5000))

    logger.info("=" * 60)
    logger.info("🚀 Starting Poster Helper Bot + Web App (WEBHOOK mode)")
    logger.info("=" * 60)
    logger.info(f"📡 Flask will listen on port {port}")

    # Start bot event loop in a separate thread
    bot_thread = threading.Thread(target=run_bot_loop, daemon=True)
    bot_thread.start()

    # Wait a bit for the bot to initialize
    import time
    time.sleep(2)

    # Start Flask server
    logger.info("🎯 Starting Flask server...")
    app.run(
        host='0.0.0.0',
        port=port,
        debug=False,
        threaded=True,
        use_reloader=False
    )

if __name__ == '__main__':
    run_server()
