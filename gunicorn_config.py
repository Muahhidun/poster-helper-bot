"""Gunicorn configuration for production deployment on Railway.

Uses gthread worker class (threads inside a single process) so that the
Telegram bot asyncio event loop running in a daemon thread is shared
across all request-handling threads.
"""
import os

# Bind to the port Railway injects, defaulting to 8080
bind = f"0.0.0.0:{os.environ.get('PORT', '8080')}"

# Single worker process — important because the Telegram bot event loop
# runs in a daemon thread inside this process.  Multiple workers would
# each try to register the webhook, causing conflicts.
workers = 1

# Use 4 threads so multiple HTTP requests can be served concurrently
# (the old werkzeug dev-server was effectively single-threaded).
threads = 4

# Worker class: gthread supports threading inside one process
worker_class = "gthread"

# Timeout: Railway expects a response within 60s for health checks,
# but some Poster API / Gemini calls can be slow.
timeout = 120

# Graceful shutdown timeout
graceful_timeout = 30

# Keep-alive for persistent connections
keepalive = 5

# Access log to stderr (Railway captures stderr)
accesslog = "-"
errorlog = "-"
loglevel = "info"

# Do NOT preload — start_server.py needs to initialize the Telegram
# bot in the worker process (same process that serves requests).
preload_app = False


def post_worker_init(worker):
    """Called after gunicorn worker process initializes.
    
    Starts the Telegram bot event loop in a background daemon thread
    so that webhook updates can be forwarded to it.
    """
    import logging
    import threading
    import time

    logger = logging.getLogger("gunicorn_config")
    logger.info("🔧 post_worker_init: starting Telegram bot thread...")

    # Import here so the module is loaded inside the worker process
    from start_server import run_bot_loop
    bot_thread = threading.Thread(target=run_bot_loop, daemon=True)
    bot_thread.start()

    # Give the bot time to set up webhook before accepting traffic
    time.sleep(2)
    logger.info("✅ Telegram bot thread started, worker ready for traffic")
