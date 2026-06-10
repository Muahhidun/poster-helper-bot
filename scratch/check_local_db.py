import sqlite3

conn = sqlite3.connect("data/users.db")
cursor = conn.cursor()

print("--- TABLES ---")
cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
for table in cursor.fetchall():
    print(table[0])

print("\n--- POSTER ACCOUNTS ---")
cursor.execute("SELECT * FROM poster_accounts;")
cols = [d[0] for d in cursor.description]
print(cols)
for row in cursor.fetchall():
    print(row)

print("\n--- WEB USERS ---")
cursor.execute("SELECT * FROM web_users;")
cols = [d[0] for d in cursor.description]
print(cols)
for row in cursor.fetchall():
    print(row)

conn.close()
