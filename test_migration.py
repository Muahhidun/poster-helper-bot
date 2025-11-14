#!/usr/bin/env python3
"""
Test script to verify database migration works correctly
"""

from database import get_database

def test_migration():
    """Test the multi-account migration"""
    print("🧪 Testing multi-account migration...\n")

    db = get_database()

    # Test user ID (yours)
    test_user_id = 167084307

    # Check if user exists
    user = db.get_user(test_user_id)
    if user:
        print(f"✅ User {test_user_id} found in database")
        print(f"   Subscription: {user['subscription_status']}")
    else:
        print(f"❌ User {test_user_id} not found")
        return

    # Get all accounts for the user
    accounts = db.get_accounts(test_user_id)
    print(f"\n📋 Found {len(accounts)} account(s) for user:")
    for acc in accounts:
        primary = " (PRIMARY)" if acc.get('is_primary') else ""
        print(f"   - {acc['account_name']}{primary}")
        print(f"     Token: {acc['poster_token'][:20]}...")
        print(f"     User ID: {acc['poster_user_id']}")

    # Get primary account
    primary = db.get_primary_account(test_user_id)
    if primary:
        print(f"\n✅ Primary account: {primary['account_name']}")
    else:
        print(f"\n❌ No primary account found")

    print("\n✅ Migration test complete!")

if __name__ == "__main__":
    test_migration()
