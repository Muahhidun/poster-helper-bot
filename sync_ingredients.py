"""Script to sync ingredients from Poster API to CSV file (multi-account support)"""
import asyncio
import csv
import shutil
import logging
from pathlib import Path
from poster_client import PosterClient
from database import get_database
import config

logger = logging.getLogger(__name__)

async def sync_ingredients(telegram_user_id: int = None):
    """
    Fetch ingredients from all Poster accounts and save to CSV

    Args:
        telegram_user_id: User ID to sync ingredients for. If None, uses first user.

    Returns:
        Tuple of (total_count, dict of {account_name: [ingredient_ids]})
    """
    db = get_database()

    # Get all poster accounts for user
    if telegram_user_id is None:
        # For backward compatibility - get first user
        # This won't be used in production since we always pass user_id
        logger.warning("No telegram_user_id provided, using legacy mode")
        client = PosterClient(telegram_user_id=None)
        try:
            logger.info("Fetching ingredients from Poster API (legacy mode)...")
            ingredients = await client.get_ingredients()

            if not ingredients:
                logger.warning("No ingredients found!")
                return (0, {})

            # Save to old format
            csv_path = Path(config.DATA_DIR) / "poster_ingredients.csv"
            if csv_path.exists():
                backup_path = csv_path.with_suffix('.csv.backup')
                shutil.copy(csv_path, backup_path)
                logger.info(f"Backup created: {backup_path}")

            ingredient_ids = []
            with open(csv_path, 'w', encoding='utf-8', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['ingredient_id', 'ingredient_name', 'unit'])

                for ingredient in ingredients:
                    ingredient_id = ingredient.get('ingredient_id')
                    name = ingredient.get('ingredient_name', '')
                    unit = ingredient.get('unit', '')

                    writer.writerow([ingredient_id, name, unit])
                    ingredient_ids.append(ingredient_id)

            logger.info(f"✅ Saved {len(ingredients)} ingredients to {csv_path}")
            return (len(ingredients), {'default': ingredient_ids})

        finally:
            await client.close()

    # Multi-account mode
    accounts = db.get_accounts(telegram_user_id)

    if not accounts:
        logger.warning(f"No poster accounts found for user {telegram_user_id}")
        return (0, {})

    logger.info(f"📋 Syncing ingredients from {len(accounts)} account(s)...")

    all_ingredients = []
    account_ingredient_map = {}

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
            ingredients = await client.get_ingredients()

            if not ingredients:
                logger.warning(f"  ⚠️ No ingredients found in {account_name}")
                account_ingredient_map[account_name] = []
                continue

            logger.info(f"  ✅ Found {len(ingredients)} ingredients in {account_name}")

            # Add account_name to each ingredient
            ingredient_ids = []
            for ingredient in ingredients:
                ingredient['account_name'] = account_name
                all_ingredients.append(ingredient)
                ingredient_ids.append(ingredient.get('ingredient_id'))

            account_ingredient_map[account_name] = ingredient_ids

        finally:
            await client.close()

    if not all_ingredients:
        logger.warning("No ingredients found in any account!")
        return (0, account_ingredient_map)

    # Backup old file
    csv_path = Path(config.DATA_DIR) / "poster_ingredients.csv"
    if csv_path.exists():
        backup_path = csv_path.with_suffix('.csv.backup')
        shutil.copy(csv_path, backup_path)
        logger.info(f"Backup created: {backup_path}")

    # Save to CSV with account_name field
    with open(csv_path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['ingredient_id', 'ingredient_name', 'unit', 'account_name'])

        for ingredient in all_ingredients:
            ingredient_id = ingredient.get('ingredient_id')
            name = ingredient.get('ingredient_name', '')
            unit = ingredient.get('unit', '')
            account_name = ingredient.get('account_name', 'Unknown')

            writer.writerow([ingredient_id, name, unit, account_name])

    logger.info(f"✅ Saved {len(all_ingredients)} total ingredients to {csv_path}")
    for account_name, ids in account_ingredient_map.items():
        logger.info(f"   - {account_name}: {len(ids)} ingredients")

    return (len(all_ingredients), account_ingredient_map)

if __name__ == '__main__':
    # Test with your user ID
    asyncio.run(sync_ingredients(telegram_user_id=167084307))

