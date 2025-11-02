"""Migrate ingredient aliases from CSV to PostgreSQL database

This script imports all existing aliases from CSV files to the database.
Run this once to migrate existing data before deploying to Railway.

Usage:
    python migrate_aliases_to_db.py
"""
import csv
import logging
from pathlib import Path
from database import get_database
import config

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


def migrate_user_aliases(telegram_user_id: int, csv_path: Path):
    """Migrate aliases from CSV file to database for a specific user"""

    if not csv_path.exists():
        logger.warning(f"CSV file not found: {csv_path}")
        return 0

    db = get_database()
    aliases_to_import = []

    # Read CSV file
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Only import ingredient aliases
            if row.get('source', '').strip().lower() != 'ingredient':
                continue

            alias_text = row['alias_text'].strip()
            poster_item_id = int(row['poster_item_id'])
            poster_item_name = row['poster_item_name'].strip()
            source = row.get('source', 'ingredient').strip()
            notes = row.get('notes', '').strip()

            aliases_to_import.append({
                'alias_text': alias_text,
                'poster_item_id': poster_item_id,
                'poster_item_name': poster_item_name,
                'source': source,
                'notes': notes
            })

    # Bulk import to database
    if aliases_to_import:
        count = db.bulk_add_aliases(telegram_user_id, aliases_to_import)
        logger.info(f"✅ User {telegram_user_id}: Imported {count}/{len(aliases_to_import)} aliases")
        return count
    else:
        logger.info(f"No aliases found in {csv_path}")
        return 0


def migrate_all_users():
    """Migrate aliases for all users in /data/users/ directory"""

    users_dir = config.DATA_DIR / "users"

    if not users_dir.exists():
        logger.error(f"Users directory not found: {users_dir}")
        return

    total_imported = 0
    user_count = 0

    # Iterate through user directories
    for user_dir in users_dir.iterdir():
        if not user_dir.is_dir():
            continue

        # Extract telegram_user_id from directory name
        try:
            telegram_user_id = int(user_dir.name)
        except ValueError:
            logger.warning(f"Skipping invalid user directory: {user_dir.name}")
            continue

        # Path to user's alias CSV
        csv_path = user_dir / "alias_item_mapping.csv"

        logger.info(f"\n{'='*60}")
        logger.info(f"Migrating user: {telegram_user_id}")
        logger.info(f"CSV path: {csv_path}")
        logger.info(f"{'='*60}")

        count = migrate_user_aliases(telegram_user_id, csv_path)
        total_imported += count
        user_count += 1

    logger.info(f"\n{'='*60}")
    logger.info(f"✅ MIGRATION COMPLETE")
    logger.info(f"Users processed: {user_count}")
    logger.info(f"Total aliases imported: {total_imported}")
    logger.info(f"{'='*60}")


if __name__ == "__main__":
    logger.info("Starting alias migration from CSV to PostgreSQL...")
    migrate_all_users()
