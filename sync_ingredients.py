"""Script to sync ingredients from Poster API to CSV file"""
import asyncio
import csv
import shutil
import logging
from pathlib import Path
from poster_client import get_poster_client
import config

logger = logging.getLogger(__name__)

async def sync_ingredients():
    """
    Fetch ingredients from Poster API and save to CSV

    Returns:
        Tuple of (count, list of ingredient_ids)
    """
    client = get_poster_client()

    try:
        logger.info("Fetching ingredients from Poster API...")
        ingredients = await client.get_ingredients()

        if not ingredients:
            logger.warning("No ingredients found!")
            return (0, [])

        logger.info(f"Found {len(ingredients)} ingredients")

        # Backup старого файла
        csv_path = Path(config.DATA_DIR) / "poster_ingredients.csv"
        if csv_path.exists():
            backup_path = csv_path.with_suffix('.csv.backup')
            shutil.copy(csv_path, backup_path)
            logger.info(f"Backup created: {backup_path}")

        # Сохранить новый файл и собрать список ID
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
        return (len(ingredients), ingredient_ids)

    finally:
        await client.close()

if __name__ == '__main__':
    asyncio.run(sync_ingredients())
