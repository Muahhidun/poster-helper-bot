import sys

file_path = "/Users/Dom/.gemini/antigravity/playground/midnight-crab/poster-helper-bot/web_app.py"

with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

start_marker = "@app.route('/login/debug-delete/<int:tx_id>')"
end_marker = "@app.route('/api/assistant/message', methods=['POST'])"

start_idx = content.find(start_marker)
end_idx = content.find(end_marker)

if start_idx == -1 or end_idx == -1:
    print(f"Error: Could not find markers in {file_path}")
    sys.exit(1)

new_function = """@app.route('/login/debug-delete/<int:tx_id>')
def debug_delete(tx_id):
    \"\"\"Debug route to fetch all recent transactions to identify target transaction\"\"\"
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
        
        async def fetch_recent():
            client = PosterClient(
                telegram_user_id=None,
                poster_token=acc['poster_token'],
                poster_user_id=acc['poster_user_id'],
                poster_base_url=acc['poster_base_url']
            )
            
            tx_list = []
            err_details = None
            
            try:
                # Fetch transactions for June 5 to June 8, 2026
                res = await client._request('GET', 'dash.getTransactions', params={
                    'dateFrom': '20260605',
                    'dateTo': '20260608'
                })
                tx_list = res.get('response', [])
            except Exception as e:
                err_details = str(e)
            finally:
                await client.close()
                
            return tx_list, err_details
            
        try:
            tx_list, err_det = run_async(fetch_recent())
        except Exception as e:
            tx_list, err_det = [], f"run_async failed: {e}"
            
        # Format list to return key fields (ID, date, sum, spot_order_num)
        formatted_txs = []
        for tx in tx_list:
            formatted_txs.append({
                "transaction_id": tx.get("transaction_id"),
                "date_close": tx.get("date_close"),
                "payed_sum": tx.get("payed_sum"),
                "spot_order_num": tx.get("spot_order_num"),
                "status": tx.get("status")
            })
            
        results.append({
            "account": acc_info,
            "count": len(tx_list),
            "transactions": formatted_txs[:10], # Return first 10
            "error": err_det
        })
        
    return jsonify({
        "success": True,
        "results": results
    })


"""

new_content = content[:start_idx] + new_function + content[end_idx:]

with open(file_path, "w", encoding="utf-8") as f:
    f.write(new_content)

print("✅ Successfully patched debug_delete in web_app.py to get list")
