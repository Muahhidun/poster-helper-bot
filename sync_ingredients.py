"""Script to sync ingredients from Poster API to CSV file"""
import asyncio
import csv
from pathlib import Path
from poster_client import get_poster_client
import config

async def sync_ingredients():
    """Fetch ingredients from Poster API and save to CSV"""
    client = get_poster_client()

    try:
        print("Fetching ingredients from Poster API...")
        ingredients = await client.get_ingredients()

        if not ingredients:
            print("No ingredients found!")
            return

        print(f"Found {len(ingredients)} ingredients")

        # Save to CSV
        csv_path = Path(config.DATA_DIR) / "poster_ingredients.csv"

        with open(csv_path, 'w', encoding='utf-8', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['ingredient_id', 'ingredient_name', 'unit'])

            for ingredient in ingredients:
                ingredient_id = ingredient.get('ingredient_id')
                name = ingredient.get('ingredient_name', '')
                unit = ingredient.get('unit', '')

                writer.writerow([ingredient_id, name, unit])

        print(f"✅ Saved {len(ingredients)} ingredients to {csv_path}")

    finally:
        await client.close()

if __name__ == '__main__':
    asyncio.run(sync_ingredients())
