import sqlite3
from pathlib import Path

db_files = [
    Path("data/users.db"),
    Path("database.db"),
    Path("poster_bot.db"),
]

for db_file in db_files:
    if not db_file.exists():
        print(f"File {db_file} does not exist")
        continue
    print(f"\n================ INSPECTING {db_file} ================")
    try:
        conn = sqlite3.connect(db_file)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        tables = [row[0] for row in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        print("Tables:", tables)
        
        if 'users' in tables:
            print("\nUsers count:", cursor.execute("SELECT count(*) FROM users").fetchone()[0])
            for u in cursor.execute("SELECT * FROM users LIMIT 5"):
                print("User:", dict(u))
                
        if 'poster_accounts' in tables:
            print("\nPoster Accounts:")
            for row in cursor.execute("SELECT * FROM poster_accounts"):
                print(dict(row))
                
        if 'ingredient_habits' in tables:
            print("\nIngredient Habits count:", cursor.execute("SELECT count(*) FROM ingredient_habits").fetchone()[0])
            for row in cursor.execute("SELECT * FROM ingredient_habits LIMIT 10"):
                print(dict(row))
                
        if 'ingredient_packaging_rules' in tables:
            print("\nPackaging Rules count:", cursor.execute("SELECT count(*) FROM ingredient_packaging_rules").fetchone()[0])
            for row in cursor.execute("SELECT * FROM ingredient_packaging_rules LIMIT 10"):
                print(dict(row))
                
        conn.close()
    except Exception as e:
        print(f"Error inspecting {db_file}: {e}")
