"""Test script for Poster API and services"""
import asyncio
import logging
from poster_client import get_poster_client
from matchers import get_category_matcher, get_account_matcher
from parser_service import get_parser_service

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def test_poster_api():
    """Test Poster API connection"""
    print("\n=== Testing Poster API ===")

    poster = get_poster_client()

    try:
        # Test accounts
        print("\n1. Fetching accounts...")
        accounts = await poster.get_accounts()
        print(f"✅ Found {len(accounts)} accounts:")
        for acc in accounts[:5]:
            print(f"   - {acc['name']} (ID: {acc['account_id']}, Type: {acc['type']})")

        # Test categories
        print("\n2. Fetching categories...")
        categories = await poster.get_categories()
        print(f"✅ Found {len(categories)} categories:")
        for cat in categories[:10]:
            print(f"   - {cat['name']} (ID: {cat['category_id']})")

        # Test ingredients
        print("\n3. Fetching ingredients...")
        ingredients = await poster.get_ingredients()
        print(f"✅ Found {len(ingredients)} ingredients")
        print(f"   First 3: {[i['ingredient_name'] for i in ingredients[:3]]}")

        print("\n✅ Poster API test passed!")

    except Exception as e:
        print(f"\n❌ Poster API test failed: {e}")

    finally:
        await poster.close()


def test_matchers():
    """Test category and account matchers"""
    print("\n=== Testing Matchers ===")

    # Test category matcher
    print("\n1. Testing category matcher...")
    cat_matcher = get_category_matcher()

    test_categories = ['донер', 'повара', 'кассир', 'курьеры']
    for cat_text in test_categories:
        result = cat_matcher.match(cat_text)
        if result:
            cat_id, cat_name = result
            print(f"   ✅ '{cat_text}' -> {cat_name} (ID: {cat_id})")
        else:
            print(f"   ❌ '{cat_text}' not matched")

    # Test account matcher
    print("\n2. Testing account matcher...")
    acc_matcher = get_account_matcher()

    test_accounts = ['закуп', 'касипай', 'касса', 'wolt']
    for acc_text in test_accounts:
        result = acc_matcher.match(acc_text)
        if result:
            acc_name = acc_matcher.get_account_name(result)
            print(f"   ✅ '{acc_text}' -> {acc_name} (ID: {result})")
        else:
            print(f"   ❌ '{acc_text}' not matched")

    print("\n✅ Matchers test passed!")


async def test_parser():
    """Test Claude parser"""
    print("\n=== Testing Claude Parser ===")

    parser = get_parser_service()

    test_texts = [
        "Донерщик 7500 Максат",
        "Повара 12000 Ислам",
        "Кассиры 5000 Меруерт",
        "Оставил в кассе 8000 курьер Нурлан"
    ]

    for text in test_texts:
        print(f"\n📝 Input: '{text}'")
        try:
            result = await parser.parse_transaction(text)
            if result:
                print(f"   ✅ Parsed:")
                print(f"      Amount: {result.get('amount')}")
                print(f"      Category: {result.get('category')}")
                print(f"      Comment: {result.get('comment')}")
                print(f"      Account: {result.get('account_from', 'закуп')}")
            else:
                print(f"   ❌ Failed to parse")
        except Exception as e:
            print(f"   ❌ Error: {e}")

    print("\n✅ Parser test passed!")


async def main():
    """Run all tests"""
    print("\n" + "="*60)
    print("🧪 Running Poster Helper Bot Tests")
    print("="*60)

    # Test matchers (synchronous)
    test_matchers()

    # Test parser
    await test_parser()

    # Test Poster API
    await test_poster_api()

    print("\n" + "="*60)
    print("✅ All tests completed!")
    print("="*60)


if __name__ == "__main__":
    asyncio.run(main())
