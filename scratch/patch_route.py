import sys

file_path = "/Users/Dom/.gemini/antigravity/playground/midnight-crab/poster-helper-bot/web_app.py"

with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

target = "@app.route('/api/debug-delete/<int:tx_id>')"
replacement = "@app.route('/mini-app/debug-delete/<int:tx_id>')"

if target not in content:
    print(f"Error: Could not find target in {file_path}")
    sys.exit(1)

new_content = content.replace(target, replacement, 1)

with open(file_path, "w", encoding="utf-8") as f:
    f.write(new_content)

print("✅ Successfully patched route in web_app.py")
