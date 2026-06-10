import sys

file_path = "/Users/Dom/.gemini/antigravity/playground/midnight-crab/poster-helper-bot/web_app.py"

with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

# Let's find the debug_delete function and replace it completely
# We know the function starts at @app.route('/login/debug-delete/<int:tx_id>')
# and ends right before @app.route('/api/assistant/message', methods=['POST'])

start_marker = "@app.route('/login/debug-delete/<int:tx_id>')"
end_marker = "@app.route('/api/assistant/message', methods=['POST'])"

start_idx = content.find(start_marker)
end_idx = content.find(end_marker)

if start_idx == -1 or end_idx == -1:
    print(f"Error: Could not find markers in {file_path}")
    sys.exit(1)

old_function = content[start_idx:end_idx]

new_function = """@app.route('/login/debug-delete/<int:tx_id>')
def debug_delete(tx_id):
    \"\"\"Debug route to test deleting transaction on Poster API with various options\"\"\"
    db = get_database()
    try:
        user_ids = db.get_all_user_ids_with_accounts()
        accounts = []
        for uid in user_ids:
            accounts.extend(db.get_accounts(uid))
    except Exception as e:
        return jsonify({"success": False, "error": f"Failed to fetch accounts: {e}"})

    results = []
    from poster_client import PosterClient
    
    for acc in accounts:
        acc_info = {
            "account_id": acc['id'],
            "account_name": acc['account_name']
        }
        
        async def test_both():
            client_json = PosterClient(
                telegram_user_id=None,
                poster_token=acc['poster_token'],
                poster_user_id=acc['poster_user_id'],
                poster_base_url=acc['poster_base_url']
            )
            client_form = PosterClient(
                telegram_user_id=None,
                poster_token=acc['poster_token'],
                poster_user_id=acc['poster_user_id'],
                poster_base_url=acc['poster_base_url']
            )
            
            res_j, err_j, res_f, err_f = None, None, None, None
            try:
                res_j = await client_json._request('POST', 'transactions.removeTransaction', data={
                    'transaction_id': tx_id
                }, use_json=True)
            except Exception as e:
                err_j = str(e)
            finally:
                await client_json.close()
                
            try:
                res_f = await client_form._request('POST', 'transactions.removeTransaction', data={
                    'transaction_id': tx_id
                }, use_json=False)
            except Exception as e:
                err_f = str(e)
            finally:
                await client_form.close()
                
            return res_j, err_j, res_f, err_f
            
        try:
            res_json, err_json, res_form, err_form = run_async(test_both())
        except Exception as e:
            res_json, err_json, res_form, err_form = None, f"run_async failed: {e}", None, f"run_async failed: {e}"
        
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

new_content = content[:start_idx] + new_function + content[end_idx:]

with open(file_path, "w", encoding="utf-8") as f:
    f.write(new_content)

print("✅ Successfully patched debug_delete to use one loop in web_app.py")
