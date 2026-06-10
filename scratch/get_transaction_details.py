import asyncio
import os
import sys
from datetime import datetime

# Add project root to path
sys.path.append("/Users/Dom/.gemini/antigravity/playground/midnight-crab/poster-helper-bot")

from database import get_database
from poster_client import PosterClient

async def get_details():
    db = get_database()
    user_ids = db.get_all_user_ids_with_accounts()
    accounts = []
    for uid in user_ids:
        accounts.extend(db.get_accounts(uid))
        
    tx_ids = [1780839278852, 1780838894269]
    
    print("=======================================")
    print("FETCHING DETAILS FOR TRANSACTIONS:")
    print(tx_ids)
    print("=======================================")
    
    # We will query GET dash.getTransactions or GET transactions.getTransaction or similar
    # Wait, getTransactions returns transactions. Let's see if we can search for these transaction_ids
    for acc in accounts:
        print(f"\nAccount: {acc['account_name']} (ID: {acc['id']})")
        client = PosterClient(
            telegram_user_id=None,
            poster_token=acc['poster_token'],
            poster_user_id=acc['poster_user_id'],
            poster_base_url=acc['poster_base_url']
        )
        
        try:
            # We can search by fetching transactions for the last 3 days
            # or just call dash.getTransactions for today/yesterday
            # Since transaction IDs start with 17808... they are very recent (June 2026).
            # Let's get transactions from June 5 to June 8, 2026.
            result = await client._request('GET', 'dash.getTransactions', params={
                'dateFrom': '20260605',
                'dateTo': '20260608'
            })
            
            transactions = result.get('response', [])
            print(f"Fetched {len(transactions)} transactions in total.")
            
            for tx in transactions:
                tx_id = int(tx.get('transaction_id', 0))
                if tx_id in tx_ids:
                    print(f"\nFound Transaction {tx_id}:")
                    for k, v in tx.items():
                        print(f"  {k}: {v}")
                    
                    # Also try to fetch products for this transaction
                    try:
                        prod_res = await client._request('GET', 'dash.getTransactionProducts', params={
                            'transaction_id': tx_id
                        })
                        print(f"  Products: {prod_res.get('response', [])}")
                    except Exception as pe:
                        print(f"  Failed to get products: {pe}")
                        
        except Exception as e:
            print(f"Error for account {acc['account_name']}: {e}")
        finally:
            await client.close()

if __name__ == "__main__":
    asyncio.run(get_details())
