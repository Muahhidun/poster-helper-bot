#!/bin/bash
# Start script for Poster Helper Bot

cd "$(dirname "$0")"

echo "🚀 Starting Poster Helper Bot..."

# Activate venv
source venv/bin/activate

# Run bot
python3 bot.py
