#!/usr/bin/env python3
"""
Script to add Pizzburg-cafe account for the user.
Run this after the database migration is complete.
"""

from database import get_database

def add_pizzburg_cafe_account():
    """Add Pizzburg-cafe account for the user"""

    # User data
    TELEGRAM_USER_ID = 167084307  # Your Telegram ID

    # Pizzburg-cafe credentials
    ACCOUNT_NAME = "Pizzburg-cafe"
    POSTER_TOKEN = "881862:431800518a877398e5c4d1d3b9c76cee"
    POSTER_USER_ID = "881862"  # From token
    POSTER_BASE_URL = "https://joinposter.com/api"
    IS_PRIMARY = False  # Pizzburg is primary

    db = get_database()

    # Check if account already exists
    existing = db.get_account_by_name(TELEGRAM_USER_ID, ACCOUNT_NAME)
    if existing:
        print(f"✅ Account '{ACCOUNT_NAME}' already exists for user {TELEGRAM_USER_ID}")
        return

    # Add the account
    success = db.add_account(
        telegram_user_id=TELEGRAM_USER_ID,
        account_name=ACCOUNT_NAME,
        poster_token=POSTER_TOKEN,
        poster_user_id=POSTER_USER_ID,
        poster_base_url=POSTER_BASE_URL,
        is_primary=IS_PRIMARY
    )

    if success:
        print(f"✅ Successfully added '{ACCOUNT_NAME}' account!")

        # Show all accounts
        accounts = db.get_accounts(TELEGRAM_USER_ID)
        print(f"\n📋 All accounts for user {TELEGRAM_USER_ID}:")
        for acc in accounts:
            primary_marker = " (PRIMARY)" if acc['is_primary'] else ""
            print(f"  - {acc['account_name']}{primary_marker}")
    else:
        print(f"❌ Failed to add '{ACCOUNT_NAME}' account")

if __name__ == "__main__":
    add_pizzburg_cafe_account()
