#!/usr/bin/env python3
"""Test script for ingredient matching with aliases and fuzzy search"""

import sys
from matchers import get_ingredient_matcher

def test_ingredient_matching():
    """Test various ingredient matching scenarios"""

    print("🧪 Testing Ingredient Matcher with Aliases and Fuzzy Search\n")
    print("=" * 60)

    matcher = get_ingredient_matcher()

    # Test cases
    test_cases = [
        # Exact alias matches
        ("брынза", "Should match via alias"),
        ("ананас", "Should match via alias"),
        ("багет", "Should match via alias"),

        # Exact name matches
        ("Виноград", "Should match exact name"),
        ("Горчица (140гр)", "Should match exact name with unit"),

        # Fuzzy matches on aliases
        ("брынзa", "Typo in alias"),
        ("аннанас", "Typo in alias"),

        # Fuzzy matches on names
        ("горчица", "Should fuzzy match name"),
        ("ветчина смол", "Partial match"),

        # Non-existent
        ("несуществующий продукт", "Should not match"),
        ("xyz123", "Should not match"),
    ]

    for test_input, description in test_cases:
        print(f"\n📝 Test: {description}")
        print(f"   Input: '{test_input}'")

        result = matcher.match(test_input)

        if result:
            ing_id, name, unit, score, *rest = result
            account_name = rest[0] if rest else 'Unknown'
            print(f"   ✅ Match found!")
            print(f"      ID: {ing_id}")
            print(f"      Name: {name}")
            print(f"      Unit: {unit}")
            print(f"      Score: {score}")
            print(f"      Account: {account_name}")
        else:
            print(f"   ❌ No match found")

    print("\n" + "=" * 60)
    print("\n🔍 Testing get_top_matches for manual selection\n")

    # Test top matches
    search_terms = ["сыр", "булочка", "xyz"]

    for term in search_terms:
        print(f"\n📝 Top matches for: '{term}'")
        top_matches = matcher.get_top_matches(term, limit=5)

        if top_matches:
            for i, (ing_id, name, unit, score) in enumerate(top_matches, 1):
                print(f"   {i}. {name} (ID: {ing_id}, Score: {score})")
        else:
            print(f"   ❌ No matches found")

    print("\n" + "=" * 60)
    print("✅ Testing complete!\n")

if __name__ == "__main__":
    test_ingredient_matching()
