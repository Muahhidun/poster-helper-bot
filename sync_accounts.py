"""Script to sync accounts from Poster API to CSV file"""
import asyncio
import csv
import shutil
import logging
from pathlib import Path
from poster_client import get_poster_client
import config

logger = logging.getLogger(__name__)

async def sync_accounts():
    """Fetch accounts from Poster API and save to CSV"""
    client = get_poster_client()

    try:
        logger.info("Fetching accounts from Poster API...")
        accounts = await client.get_accounts()

        if not accounts:
            logger.warning("No accounts found!")
            return 0

        logger.info(f"Found {len(accounts)} accounts")

        csv_path = Path(config.DATA_DIR) / "poster_accounts.csv"

        # Загрузить старые aliases из CSV (если существует)
        old_aliases = {}
        if csv_path.exists():
            try:
                with open(csv_path, 'r', encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        account_id = row.get('account_id')
                        aliases = row.get('aliases', '')
                        if account_id:
                            old_aliases[account_id] = aliases
                logger.info(f"Loaded aliases for {len(old_aliases)} existing accounts")
            except Exception as e:
                logger.warning(f"Could not load old aliases: {e}")

        # Backup старого файла
        if csv_path.exists():
            backup_path = csv_path.with_suffix('.csv.backup')
            shutil.copy(csv_path, backup_path)
            logger.info(f"Backup created: {backup_path}")

        # Сохранить новый файл
        with open(csv_path, 'w', encoding='utf-8', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['account_id', 'name', 'type', 'aliases'])

            for account in accounts:
                account_id = str(account.get('account_id'))
                name = account.get('name', '')
                account_type = account.get('type', '')

                # Сохранить старые aliases или пустую строку для новых счетов
                aliases = old_aliases.get(account_id, '')

                writer.writerow([account_id, name, account_type, aliases])

        logger.info(f"✅ Saved {len(accounts)} accounts to {csv_path}")
        return len(accounts)

    finally:
        await client.close()

if __name__ == '__main__':
    asyncio.run(sync_accounts())
