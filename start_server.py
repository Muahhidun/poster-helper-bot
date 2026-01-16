"""
Unified launcher for Telegram Bot and Flask Web App
Integrates Telegram webhook with Flask
"""
import os
import asyncio
import logging
from flask import Flask, request
from telegram import Update
from bot import initialize_application
from config import WEBHOOK_URL, WEBHOOK_PATH, TELEGRAM_BOT_TOKEN

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize the Telegram bot application
logger.info("🔧 Initializing Telegram bot...")
telegram_app = initialize_application()

# Import Flask app from web_app
from web_app import app

# Add webhook endpoint to Flask app
@app.route(WEBHOOK_PATH, methods=['POST'])
async def telegram_webhook():
    """Handle incoming Telegram updates via webhook"""
    try:
        # Get the update from request
        update_data = request.get_json(force=True)
        update = Update.de_json(update_data, telegram_app.bot)

        # Process the update
        await telegram_app.process_update(update)

        return 'OK', 200
    except Exception as e:
        logger.error(f"Error processing webhook: {e}", exc_info=True)
        return 'Error', 500

async def setup_webhook():
    """Setup webhook on Telegram"""
    try:
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

def run_server():
    """Run the Flask server with webhook"""
    port = int(os.environ.get('PORT', 5000))

    logger.info("=" * 60)
    logger.info("🚀 Starting Poster Helper Bot + Web App (WEBHOOK mode)")
    logger.info("=" * 60)
    logger.info(f"📡 Flask will listen on port {port}")

    # Setup webhook asynchronously
    webhook_success = asyncio.run(setup_webhook())

    if not webhook_success:
        logger.warning("⚠️  Webhook setup failed, but Flask will still start")
        logger.warning("   Add WEBHOOK_URL environment variable to enable webhook")

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
