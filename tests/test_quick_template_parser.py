"""Tests for quick template syntax parsing"""
import re
from typing import Optional, Tuple

import pytest


def try_parse_quick_template(text: str) -> Optional[Tuple[str, int]]:
    """Parse quick template syntax like 'лаваш 400'.
    Duplicated from shipment_templates.py to avoid importing telegram dependency."""
    pattern = r'^([а-яёa-z\s]+?)\s+(\d+)$'
    match = re.match(pattern, text.strip().lower(), re.IGNORECASE)
    if match:
        template_name = match.group(1).strip()
        quantity = int(match.group(2))
        return (template_name, quantity)
    return None


@pytest.mark.parametrize("text,expected", [
    ("лаваш 400", ("лаваш", 400)),
    ("Лаваш 400", ("лаваш", 400)),
    ("айран 50", ("айран", 50)),
    ("донер маринад 100", ("донер маринад", 100)),
    ("ЛАВАШ 250", ("лаваш", 250)),
    # Invalid inputs
    ("лаваш", None),       # Missing quantity
    ("400", None),          # Missing name
    ("лаваш abc", None),   # Invalid quantity
])
def test_parse_quick_template(text, expected):
    """Test parsing of quick template syntax like 'лаваш 400'"""
    result = try_parse_quick_template(text)
    assert result == expected, f"Input '{text}': expected {expected}, got {result}"
