#!/bin/bash
# Script to restart Poster Helper Bot via LaunchAgent

echo "🔄 Restarting bot via LaunchAgent..."
launchctl unload ~/Library/LaunchAgents/com.posterbot.telegram.plist 2>/dev/null || true

echo "⏳ Waiting for process to terminate..."
sleep 2

echo "🚀 Starting bot..."
launchctl load ~/Library/LaunchAgents/com.posterbot.telegram.plist

echo "⏳ Waiting for bot to start..."
sleep 3

if pgrep -f "python.*bot.py" > /dev/null; then
    echo "✅ Bot started successfully!"
    echo "📝 To view logs: tail -f ~/poster-helper-bot/logs/bot.log"
    echo "📝 To view errors: tail -f ~/poster-helper-bot/logs/bot_error.log"
else
    echo "❌ Bot failed to start. Check error log:"
    tail -20 ~/poster-helper-bot/logs/bot_error.log
fi
