"""Script to sync suppliers from Poster API to CSV file"""
import asyncio
import csv
import shutil
import logging
from pathlib import Path
from poster_client import get_poster_client
import config

logger = logging.getLogger(__name__)

async def sync_suppliers():
    """Fetch suppliers from Poster API and save to CSV"""
    client = get_poster_client()

    try:
        logger.info("Fetching suppliers from Poster API...")
        suppliers = await client.get_suppliers()

        if not suppliers:
            logger.warning("No suppliers found!")
            return 0

        logger.info(f"Found {len(suppliers)} suppliers")

        csv_path = Path(config.DATA_DIR) / "poster_suppliers.csv"

        # Загрузить старые aliases из CSV (если существует)
        old_aliases = {}
        if csv_path.exists():
            try:
                with open(csv_path, 'r', encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        supplier_id = row.get('supplier_id')
                        aliases = row.get('aliases', '')
                        if supplier_id:
                            old_aliases[supplier_id] = aliases
                logger.info(f"Loaded aliases for {len(old_aliases)} existing suppliers")
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
            writer.writerow(['supplier_id', 'name', 'aliases'])

            for supplier in suppliers:
                supplier_id = str(supplier.get('supplier_id'))
                name = supplier.get('supplier_name', '')

                # Сохранить старые aliases или пустую строку для новых поставщиков
                aliases = old_aliases.get(supplier_id, '')

                writer.writerow([supplier_id, name, aliases])

        logger.info(f"✅ Saved {len(suppliers)} suppliers to {csv_path}")
        return len(suppliers)

    finally:
        await client.close()

if __name__ == '__main__':
    asyncio.run(sync_suppliers())
