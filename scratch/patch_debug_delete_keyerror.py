import sys

file_path = "/Users/Dom/.gemini/antigravity/playground/midnight-crab/poster-helper-bot/web_app.py"

with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

old_block = """    for acc in accounts:
        acc_info = {
            "account_id": acc['id'],
            "account_name": acc['account_name'],
            "telegram_user_id": acc['telegram_user_id']
        }
        
        client = PosterClient(
            telegram_user_id=acc['telegram_user_id'],
            poster_token=acc['poster_token'],
            poster_user_id=acc['poster_user_id'],
            poster_base_url=acc['poster_base_url']
        )"""

new_block = """    for acc in accounts:
        acc_info = {
            "account_id": acc['id'],
            "account_name": acc['account_name']
        }
        
        client = PosterClient(
            telegram_user_id=None,
            poster_token=acc['poster_token'],
            poster_user_id=acc['poster_user_id'],
            poster_base_url=acc['poster_base_url']
        )"""

if old_block not in content:
    print(f"Error: Could not find old_block in {file_path}")
    sys.exit(1)

new_content = content.replace(old_block, new_block, 1)

with open(file_path, "w", encoding="utf-8") as f:
    f.write(new_content)

print("✅ Successfully patched keyerror in web_app.py")
