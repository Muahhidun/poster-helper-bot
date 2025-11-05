"""Script to sync products from Poster API to CSV file"""
import asyncio
import csv
import shutil
import logging
from pathlib import Path
from poster_client import get_poster_client
import config

logger = logging.getLogger(__name__)

async def sync_products():
    """Fetch products from Poster API and save to CSV"""
    client = get_poster_client()

    try:
        logger.info("Fetching products from Poster API...")
        products = await client.get_products()

        if not products:
            logger.warning("No products found!")
            return 0

        logger.info(f"Found {len(products)} products")

        # Backup старого файла
        csv_path = Path(config.DATA_DIR) / "poster_products.csv"
        if csv_path.exists():
            backup_path = csv_path.with_suffix('.csv.backup')
            shutil.copy(csv_path, backup_path)
            logger.info(f"Backup created: {backup_path}")

        # Сохранить новый файл
        with open(csv_path, 'w', encoding='utf-8', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['product_id', 'product_name', 'category_name'])

            for product in products:
                product_id = product.get('product_id')
                name = product.get('product_name', '')
                category = product.get('category_name', '')

                writer.writerow([product_id, name, category])

        logger.info(f"✅ Saved {len(products)} products to {csv_path}")
        return len(products)

    finally:
        await client.close()

if __name__ == '__main__':
    asyncio.run(sync_products())
