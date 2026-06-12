import sqlite3
db_path = "/Users/Dom/.gemini/antigravity/playground/midnight-crab/poster-helper-bot/data/users.db"
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

try:
    cursor.execute("SELECT * FROM daily_transactions_log LIMIT 5")
    rows = cursor.fetchall()
    print("=== daily_transactions_log ===")
    for r in rows:
        print(dict(r))
except Exception as e:
    print("Error:", e)

try:
    cursor.execute("SELECT * FROM assistant_chat_messages ORDER BY id DESC LIMIT 10")
    rows = cursor.fetchall()
    print("\n=== assistant_chat_messages ===")
    for r in rows:
        print(dict(r))
except Exception as e:
    print("Error:", e)

conn.close()
