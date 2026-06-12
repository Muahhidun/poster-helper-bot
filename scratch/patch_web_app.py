import sys

file_path = "/Users/Dom/.gemini/antigravity/playground/midnight-crab/poster-helper-bot/web_app.py"

with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

target = "@app.route('/api/assistant/message', methods=['POST'])"

if target not in content:
    print(f"Error: Could not find target in {file_path}")
    sys.exit(1)

debug_route = """@app.route('/api/debug-delete/<int:tx_id>')
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
        conn.close()

    results = []
    from poster_client import PosterClient
    import asyncio
    
    for acc in accounts:
        acc_info = {
            "account_id": acc['id'],
            "account_name": acc['account_name'],
            "telegram_user_id": acc['telegram_user_id']
        }
        
        client = PosterClient(
            telegram_user_id=acc['telegram_user_id'],
            poster_token=acc['poster_token'],
            poster_user_id=acc['poster_user_id'],
            poster_base_url=acc['poster_base_url']
        )
        
        res_json = None
        err_json = None
        res_form = None
        err_form = None
        
        def run_sync_helper(coro):
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(coro)
            finally:
                loop.close()
                
        try:
            async def test_json():
                return await client._request('POST', 'transactions.removeTransaction', data={
                    'transaction_id': tx_id
                }, use_json=True)
            res_json = run_sync_helper(test_json())
        except Exception as e:
            err_json = str(e)
            
        try:
            async def test_form():
                return await client._request('POST', 'transactions.removeTransaction', data={
                    'transaction_id': tx_id
                }, use_json=False)
            res_form = run_sync_helper(test_form())
        except Exception as e:
            err_form = str(e)
            
        run_sync_helper(client.close())
        
        results.append({
            "account": acc_info,
            "json_attempt": {"response": res_json, "error": err_json},
            "form_attempt": {"response": res_form, "error": err_form}
        })
        
    return jsonify({
        "success": True,
        "transaction_id": tx_id,
        "results": results
    })


"""

new_content = content.replace(target, debug_route + target, 1)

with open(file_path, "w", encoding="utf-8") as f:
    f.write(new_content)

print("✅ Successfully patched web_app.py")
