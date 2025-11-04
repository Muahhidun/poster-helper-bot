"""Test that IngredientMatcher loads aliases from database"""
import logging
from matchers import IngredientMatcher

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

def test_matcher():
    """Test ingredient matcher with database aliases"""

    telegram_user_id = 167084307

    print(f"\n{'='*60}")
    print(f"Testing IngredientMatcher for user {telegram_user_id}")
    print(f"{'='*60}\n")

    # Initialize matcher (should load from database)
    matcher = IngredientMatcher(telegram_user_id)

    print(f"✅ Loaded {len(matcher.aliases)} aliases")
    print(f"✅ Loaded {len(matcher.ingredients)} ingredients")

    # Test some aliases
    test_cases = [
        "брынза",
        "филе цб",
        "крыло цыпленка",
        "перчатки пнд",
        "картофель фри farm frites",
        "кремчиз"
    ]

    print(f"\n{'='*60}")
    print("Testing alias matching:")
    print(f"{'='*60}\n")

    for alias_text in test_cases:
        result = matcher.match(alias_text)
        if result:
            ingredient_id, name, unit, score = result
            print(f"✅ '{alias_text}' -> {name} (ID={ingredient_id}, score={score})")
        else:
            print(f"❌ '{alias_text}' -> NOT FOUND")

    print(f"\n{'='*60}")
    print("✅ Test complete!")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    test_matcher()
