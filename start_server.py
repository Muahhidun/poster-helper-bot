"""
Unified launcher for Telegram Bot and Flask Web App
Runs both bot.py and web_app.py in the same process
"""
import os
import sys
import subprocess
import threading
import logging
import time

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def start_bot():
    """Start Telegram bot in subprocess"""
    logger.info("🤖 Starting Telegram bot in background...")
    try:
        # Run bot as subprocess WITHOUT capturing output
        # This allows it to run in background without blocking
        process = subprocess.Popen(
            [sys.executable, 'bot.py'],
            # Don't capture output - let it go to stdout directly
        )

        logger.info(f"✅ Bot process started with PID {process.pid}")
    except Exception as e:
        logger.error(f"❌ Error starting bot: {e}")

def start_web_app():
    """Start Flask web app"""
    logger.info("🌐 Starting Flask web app...")
    from web_app import app
    port = int(os.environ.get('PORT', 5000))

    logger.info(f"📡 Flask will listen on port {port}")

    # Use threaded=True for better concurrency
    app.run(
        host='0.0.0.0',
        port=port,
        debug=False,
        threaded=True,
        use_reloader=False  # Important: disable reloader in production
    )

if __name__ == '__main__':
    logger.info("=" * 60)
    logger.info("🚀 Starting Poster Helper Bot + Web App")
    logger.info("=" * 60)

    # Start bot in background subprocess
    start_bot()

    # Give bot a moment to initialize
    logger.info("⏳ Waiting 2 seconds for bot to initialize...")
    time.sleep(2)

    # Start Flask in main thread (Railway needs this for health checks)
    logger.info("🎯 Starting Flask in main thread...")
    start_web_app()
