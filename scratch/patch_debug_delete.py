import sys

file_path = "/Users/Dom/.gemini/antigravity/playground/midnight-crab/poster-helper-bot/web_app.py"

with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

# Let's find target block and replace it
target_start = "@app.route('/login/debug-delete/<int:tx_id>')"
target_end = "results = []"

if target_start not in content:
    print(f"Error: Could not find target start in {file_path}")
    sys.exit(1)

# Find the exact function to replace
# We want to replace the try-except-finally block inside debug_delete
old_function_start = """@app.route('/login/debug-delete/<int:tx_id>')
def debug_delete(tx_id):
    \"\"\"Debug route to test deleting transaction on Poster API with various options\"\"\"
    db = get_database()
    conn = db._get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id, telegram_user_id, account_name, poster_token, poster_user_id, poster_base_url FROM poster_accounts")
        accounts = [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        return jsonify({"success": False, "error": f"Failed to fetch accounts: {e}"})
    finally:
        conn.close()"""

new_function_start = """@app.route('/login/debug-delete/<int:tx_id>')
def debug_delete(tx_id):
    \"\"\"Debug route to test deleting transaction on Poster API with various options\"\"\"
    db = get_database()
    try:
        user_ids = db.get_all_user_ids_with_accounts()
        accounts = []
        for uid in user_ids:
            accounts.extend(db.get_accounts(uid))
    except Exception as e:
        return jsonify({"success": False, "error": f"Failed to fetch accounts: {e}"})"""

new_content = content.replace(old_function_start, new_function_start, 1)

with open(file_path, "w", encoding="utf-8") as f:
    f.write(new_content)

print("✅ Successfully patched debug_delete function in web_app.py")
