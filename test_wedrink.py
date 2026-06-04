import asyncio
from poster_client import PosterClient
import json

async def run():
    client = PosterClient(167084307)
    # Get cafe account token
    from database import get_database
    db = get_database()
    accounts = db.get_accounts(167084307)
    cafe = next(a for a in accounts if not a.get('is_primary'))
    client.poster_token = cafe['poster_token']
    client.poster_user_id = cafe['poster_user_id']
    client.poster_base_url = cafe['poster_base_url']
    
    res = await client._request('GET', 'dash.getProductsSales', params={'dateFrom': '20260515', 'dateTo': '20260515'})
    
    wedrink_products = [p for p in res.get('response', []) if 'wedrink' in p.get('category_name', '').lower()]
    print(json.dumps(wedrink_products[:2], indent=2, ensure_ascii=False))
    
    await client.close()

asyncio.run(run())
