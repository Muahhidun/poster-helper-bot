#!/bin/bash
# Check bot status

echo "🔍 Checking for running bot processes..."
PROCESSES=$(ps aux | grep "[p]ython3 bot.py")

if [ -z "$PROCESSES" ]; then
    echo "❌ No bot processes found"
else
    echo "✅ Found bot processes:"
    echo "$PROCESSES"
    echo ""
    echo "Process count: $(echo "$PROCESSES" | wc -l | tr -d ' ')"
fi

echo ""
echo "📝 Recent bot logs (last 10 lines):"
tail -10 ~/poster-helper-bot/bot.log 2>/dev/null || echo "No log file found"
