import sys

file_path = "/Users/Dom/.gemini/antigravity/playground/midnight-crab/poster-helper-bot/web_app.py"

with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

old_block = """    for acc in accounts:
        acc_info = {
            "account_id": acc['id'],
            "account_name": acc['account_name']
        }
        
        client = PosterClient(
            telegram_user_id=None,
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
            
        run_sync_helper(client.close())"""

new_block = """    for acc in accounts:
        acc_info = {
            "account_id": acc['id'],
            "account_name": acc['account_name']
        }
        
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
                client = PosterClient(
                    telegram_user_id=None,
                    poster_token=acc['poster_token'],
                    poster_user_id=acc['poster_user_id'],
                    poster_base_url=acc['poster_base_url']
                )
                try:
                    return await client._request('POST', 'transactions.removeTransaction', data={
                        'transaction_id': tx_id
                    }, use_json=True)
                finally:
                    await client.close()
            res_json = run_sync_helper(test_json())
        except Exception as e:
            err_json = str(e)
            
        try:
            async def test_form():
                client = PosterClient(
                    telegram_user_id=None,
                    poster_token=acc['poster_token'],
                    poster_user_id=acc['poster_user_id'],
                    poster_base_url=acc['poster_base_url']
                )
                try:
                    return await client._request('POST', 'transactions.removeTransaction', data={
                        'transaction_id': tx_id
                    }, use_json=False)
                finally:
                    await client.close()
            res_form = run_sync_helper(test_form())
        except Exception as e:
            err_form = str(e)"""

if old_block not in content:
    print(f"Error: Could not find old_block in {file_path}")
    sys.exit(1)

new_content = content.replace(old_block, new_block, 1)

with open(file_path, "w", encoding="utf-8") as f:
    f.write(new_content)

print("✅ Successfully patched session management in web_app.py")
