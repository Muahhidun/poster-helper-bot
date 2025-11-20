"""Script to sync products from Poster API to CSV file (multi-account support)"""
import asyncio
import csv
import shutil
import logging
from pathlib import Path
from poster_client import PosterClient
from database import get_database
import config

logger = logging.getLogger(__name__)

async def sync_products(telegram_user_id: int = None):
    """
    Fetch products from all Poster accounts and save to CSV

    Args:
        telegram_user_id: User ID to sync products for. If None, uses legacy mode.

    Returns:
        Tuple of (total_count, dict of {account_name: [product_ids]})
    """
    db = get_database()

    # Get all poster accounts for user
    if telegram_user_id is None:
        # For backward compatibility - get first user
        logger.warning("No telegram_user_id provided, using legacy mode")
        client = PosterClient(telegram_user_id=None)
        try:
            logger.info("Fetching products from Poster API (legacy mode)...")
            products = await client.get_products()

            if not products:
                logger.warning("No products found!")
                return (0, {})

            # Save to old format
            csv_path = Path(config.DATA_DIR) / "poster_products.csv"
            if csv_path.exists():
                backup_path = csv_path.with_suffix('.csv.backup')
                shutil.copy(csv_path, backup_path)
                logger.info(f"Backup created: {backup_path}")

            product_ids = []
            with open(csv_path, 'w', encoding='utf-8', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['product_id', 'product_name', 'category_name'])

                for product in products:
                    product_id = product.get('product_id')
                    name = product.get('product_name', '')
                    category = product.get('category_name', '')

                    writer.writerow([product_id, name, category])
                    product_ids.append(product_id)

            logger.info(f"✅ Saved {len(products)} products to {csv_path}")
            return (len(products), {'default': product_ids})

        finally:
            await client.close()

    # Multi-account mode
    accounts = db.get_accounts(telegram_user_id)

    if not accounts:
        logger.warning(f"No poster accounts found for user {telegram_user_id}")
        return (0, {})

    logger.info(f"📋 Syncing products from {len(accounts)} account(s)...")

    all_products = []
    account_product_map = {}

    for account in accounts:
        account_name = account['account_name']
        logger.info(f"  📥 Fetching from {account_name}...")

        # Create client for this account
        client = PosterClient(
            telegram_user_id=telegram_user_id,
            poster_token=account['poster_token'],
            poster_user_id=account['poster_user_id'],
            poster_base_url=account['poster_base_url']
        )

        try:
            products = await client.get_products()

            if not products:
                logger.warning(f"  ⚠️ No products found in {account_name}")
                account_product_map[account_name] = []
                continue

            logger.info(f"  ✅ Found {len(products)} products in {account_name}")

            # Add account_name to each product
            product_ids = []
            for product in products:
                product['account_name'] = account_name
                all_products.append(product)
                product_ids.append(product.get('product_id'))

            account_product_map[account_name] = product_ids

        finally:
            await client.close()

    if not all_products:
        logger.warning("No products found in any account!")
        return (0, account_product_map)

    # Backup old file
    csv_path = Path(config.DATA_DIR) / "poster_products.csv"
    if csv_path.exists():
        backup_path = csv_path.with_suffix('.csv.backup')
        shutil.copy(csv_path, backup_path)
        logger.info(f"Backup created: {backup_path}")

    # Save to CSV with account_name field
    with open(csv_path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['product_id', 'product_name', 'category_name', 'account_name'])

        for product in all_products:
            product_id = product.get('product_id')
            name = product.get('product_name', '')
            category = product.get('category_name', '')
            account_name = product.get('account_name', 'Unknown')

            writer.writerow([product_id, name, category, account_name])

    logger.info(f"✅ Saved {len(all_products)} total products to {csv_path}")
    for account_name, ids in account_product_map.items():
        logger.info(f"   - {account_name}: {len(ids)} products")

    return (len(all_products), account_product_map)

if __name__ == '__main__':
    # Test with your user ID
    asyncio.run(sync_products(telegram_user_id=167084307))
