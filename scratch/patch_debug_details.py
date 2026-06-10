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
    \"\"\"Debug route to fetch details and test delete options for a transaction\"\"\"
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
        
        async def fetch_details():
            client = PosterClient(
                telegram_user_id=None,
                poster_token=acc['poster_token'],
                poster_user_id=acc['poster_user_id'],
                poster_base_url=acc['poster_base_url']
            )
            
            tx_details = None
            prod_details = None
            err_details = None
            err_delete = None
            res_delete = None
            
            try:
                # Fetch transactions for June 5 to June 8, 2026
                res = await client._request('GET', 'dash.getTransactions', params={
                    'dateFrom': '20260605',
                    'dateTo': '20260608'
                })
                transactions = res.get('response', [])
                for tx in transactions:
                    if int(tx.get('transaction_id', 0)) == tx_id:
                        tx_details = tx
                        break
                        
                if tx_details:
                    # Fetch products
                    prod_res = await client._request('GET', 'dash.getTransactionProducts', params={
                        'transaction_id': tx_id
                    })
                    prod_details = prod_res.get('response', [])
                    
                    # Attempt delete with use_json=False (form) to get raw error
                    try:
                        res_delete = await client._request('POST', 'transactions.removeTransaction', data={
                            'transaction_id': tx_id
                        }, use_json=False)
                    except Exception as de:
                        err_delete = str(de)
                        
            except Exception as e:
                err_details = str(e)
            finally:
                await client.close()
                
            return tx_details, prod_details, err_details, res_delete, err_delete
            
        try:
            tx_det, prod_det, err_det, res_del, err_del = run_async(fetch_details())
        except Exception as e:
            tx_det, prod_det, err_det, res_del, err_del = None, None, f"run_async failed: {e}", None, None
            
        results.append({
            "account": acc_info,
            "transaction": tx_det,
            "products": prod_det,
            "error_fetching": err_det,
            "delete_response": res_del,
            "delete_error": err_del
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

print("✅ Successfully patched debug_delete in web_app.py to get details")
