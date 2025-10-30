#!/usr/bin/env python3
"""Check suppliers from Poster API"""

import asyncio
from poster_client import PosterClient

async def main():
    poster = PosterClient()
    try:
        suppliers = await poster.get_suppliers()
        print(f"\n📋 Найдено поставщиков в Poster: {len(suppliers)}\n")

        # Sort by ID
        suppliers_sorted = sorted(suppliers, key=lambda x: x['supplier_id'])

        for supplier in suppliers_sorted:
            supplier_id = supplier['supplier_id']
            supplier_name = supplier['supplier_name']
            print(f"  ID {supplier_id:3d}: {supplier_name}")

        # Check for Lavash suppliers
        print("\n🔍 Поставщики с 'Лаваш' в названии:")
        lavash_suppliers = [s for s in suppliers if 'лаваш' in s['supplier_name'].lower() or 'lavash' in s['supplier_name'].lower()]
        for supplier in lavash_suppliers:
            print(f"  ID {supplier['supplier_id']:3d}: {supplier['supplier_name']}")

    finally:
        await poster.close()

if __name__ == "__main__":
    asyncio.run(main())
