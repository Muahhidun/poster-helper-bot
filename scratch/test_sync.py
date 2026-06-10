import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from database import get_database

async def test():
    db = get_database()
    conn = db._get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT telegram_user_id FROM users LIMIT 1")
    user = cursor.fetchone()
    if not user:
        print("No users in DB")
        return
    
    user_id = user[0]
    print(f"Running sync for user ID: {user_id}")
    
    from sync_ingredients import sync_ingredients
    from sync_products import sync_products
    
    try:
        total_ing, ing_map = await sync_ingredients(user_id)
        print(f"sync_ingredients completed: {total_ing} ingredients synced.")
        print(ing_map)
    except Exception as e:
        print(f"sync_ingredients FAILED: {e}")
        import traceback
        traceback.print_exc()
        
    try:
        total_prod, prod_map = await sync_products(user_id)
        print(f"sync_products completed: {total_prod} products synced.")
        print(prod_map)
    except Exception as e:
        print(f"sync_products FAILED: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test())
