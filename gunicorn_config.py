"""Gunicorn configuration for production deployment on Railway.

Uses gthread worker class (threads inside a single process) so the outbound
Telegram notification scheduler runs exactly once alongside Flask.
"""
import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)
if '/app' not in sys.path:
    sys.path.insert(0, '/app')

# Bind to the port Railway injects, defaulting to 8080
bind = f"0.0.0.0:{os.environ.get('PORT', '8080')}"

# Single worker process — important because every process would otherwise
# start its own scheduler and send duplicate reports and notifications.
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

# Do NOT preload — start_server.py initializes Telegram notifications and
# scheduled jobs in the worker process that also serves Flask requests.
preload_app = False


def post_worker_init(worker):
    """Called after gunicorn worker process initializes.
    
    Starts the outbound Telegram notification scheduler in a daemon thread.
    """
    import logging
    import threading
    import time

    logger = logging.getLogger("gunicorn_config")
    logger.info("🔧 post_worker_init: starting Telegram notification thread...")

    # Import here so the module is loaded inside the worker process
    from start_server import run_bot_loop
    bot_thread = threading.Thread(target=run_bot_loop, daemon=True)
    bot_thread.start()

    # Give the notification scheduler time to initialize before serving traffic.
    time.sleep(2)
    logger.info("✅ Telegram notification thread started, worker ready for traffic")
