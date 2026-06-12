import csv
from collections import defaultdict

csv_path = "data/poster_ingredients.csv"
try:
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        id_map = defaultdict(list)
        for row in reader:
            id_map[row['ingredient_id']].append(row)
            
    duplicates = {k: v for k, v in id_map.items() if len(v) > 1}
    print("Found duplicate ingredient IDs:", len(duplicates))
    for k, v in list(duplicates.items())[:5]:
        print(f"ID {k}:")
        for item in v:
            print("  ", item)
except Exception as e:
    print(f"Error: {e}")
