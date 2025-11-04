"""Script to sync products from Poster API to CSV file"""
import asyncio
import csv
from pathlib import Path
from poster_client import get_poster_client
import config

async def sync_products():
    """Fetch products from Poster API and save to CSV"""
    client = get_poster_client()

    try:
        print("Fetching products from Poster API...")
        products = await client.get_products()

        if not products:
            print("No products found!")
            return

        print(f"Found {len(products)} products")

        # Save to CSV
        csv_path = Path(config.DATA_DIR) / "poster_products.csv"

        with open(csv_path, 'w', encoding='utf-8', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['product_id', 'product_name', 'category_name'])

            for product in products:
                product_id = product.get('product_id')
                name = product.get('product_name', '')
                category = product.get('category_name', '')

                writer.writerow([product_id, name, category])

        print(f"✅ Saved {len(products)} products to {csv_path}")

    finally:
        await client.close()

if __name__ == '__main__':
    asyncio.run(sync_products())
