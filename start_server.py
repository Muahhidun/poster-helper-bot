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
    logger.info("🤖 Starting Telegram bot...")
    try:
        # Run bot as subprocess
        process = subprocess.Popen(
            [sys.executable, 'bot.py'],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True
        )

        # Stream output
        for line in process.stdout:
            print(line, end='')

        process.wait()
    except Exception as e:
        logger.error(f"Error starting bot: {e}")

def start_web_app():
    """Start Flask web app"""
    logger.info("🌐 Starting Flask web app...")
    from web_app import app
    port = int(os.environ.get('PORT', 5000))

    # Use threaded=True for better concurrency
    app.run(
        host='0.0.0.0',
        port=port,
        debug=False,
        threaded=True
    )

if __name__ == '__main__':
    logger.info("=" * 60)
    logger.info("🚀 Starting Poster Helper Bot + Web App")
    logger.info("=" * 60)

    # Start bot in background thread
    bot_thread = threading.Thread(target=start_bot, daemon=True)
    bot_thread.start()

    # Give bot a moment to initialize
    time.sleep(2)

    # Start Flask in main thread (Railway needs this for health checks)
    start_web_app()
