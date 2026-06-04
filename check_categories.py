import sqlite3
import asyncio
from poster_client import PosterClient

async def main():
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT telegram_id, poster_token, poster_user_id, poster_base_url FROM users WHERE poster_base_url LIKE '%cafe%'")
    row = cursor.fetchone()
    conn.close()
    
    if row:
        print(f"Using Cafe user: {row[0]}")
        client = PosterClient(
            telegram_user_id=row[0],
            poster_token=row[1],
            poster_user_id=row[2],
            poster_base_url=row[3]
        )
        
        categories = await client.get_categories()
        print("Categories:")
        for c in categories:
            print(f"ID: {c.get('category_id')}, Name: {c.get('category_name')}")
            
        await client.close()
    else:
        print("No cafe user found")

if __name__ == '__main__':
    asyncio.run(main())
