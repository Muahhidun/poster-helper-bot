"""Advanced supply parser with arithmetic support and flexible matching"""
import re
import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class AdvancedSupplyParser:
    """Parser for supply text with arithmetic operations and flexible format"""

    def __init__(self):
        # Keywords for identifying price vs sum
        self.price_keywords = ['по', 'цена', 'стоимость', 'цене']
        self.sum_keywords = ['за', 'сумма', 'общее', 'всего', 'за все']

        # Keywords for identifying account
        self.account_keywords = ['счет', 'счёт', 'со счета', 'со счёта', 'из']

        # Arithmetic operation keywords
        self.operations = {
            'минус': '-',
            'плюс': '+',
            'умножить': '*',
            'на': '*',  # "10 упаковок на 2.5"
        }

    def parse_supply(self, text: str) -> Optional[Dict]:
        """
        Parse supply text with flexible format

        Format: "Поставка <Supplier> [счёт <Account>] <Item> <Quantity> <по/за> <Price/Sum> ..."

        Examples:
            "Поставка Кюрдамир счёт оставил в кассе айсберг 4.4 по 1450 помидоры 10.5 по 850"
            "Поставка Метро фри 15 упаковок по 2.5 по 3350 за упаковку"
            "Поставка Янс огурцы 5 за 5000 лук 10.5 минус 0.5 по 1800"

        Returns:
            {
                'type': 'supply',
                'supplier': str,
                'account': str (optional),
                'items': [
                    {
                        'name': str,
                        'qty': float,
                        'price': float,
                        'qty_expression': str (original),
                        'price_type': 'price' | 'sum'
                    }
                ]
            }
        """
        text = text.strip()
        logger.info(f"Advanced supply parsing: '{text}'")

        # Check if it's a supply
        if not re.search(r'поставк', text.lower()):
            logger.debug("Not a supply - missing 'поставка' keyword")
            return None

        # Extract supplier (first word after "поставка")
        supplier_match = re.search(
            r'поставк[аи]?\s+(?:от\s+)?(?:поставщик\s+)?([а-яёА-ЯЁ\-\s]+?)(?:\s+сч[её]т|\s+[а-яё]+\s+\d)',
            text,
            re.IGNORECASE
        )

        if not supplier_match:
            logger.warning("Could not extract supplier")
            return None

        supplier = supplier_match.group(1).strip()
        logger.debug(f"Supplier: {supplier}")

        # Extract account (optional)
        account = None
        account_match = re.search(
            r'сч[её]т\s+([а-яёА-ЯЁ\s]+?)(?=\s+[а-яё]+\s+\d|\s+\d)',
            text,
            re.IGNORECASE
        )

        if account_match:
            account = account_match.group(1).strip()
            logger.debug(f"Account: {account}")

        # Extract items
        # Remove "Поставка <supplier> [счёт <account>]" from text
        items_text = text
        if supplier_match:
            items_text = text[supplier_match.end():].strip()
        if account_match:
            items_text = items_text[account_match.end():].strip()

        logger.debug(f"Items text: '{items_text}'")

        # Parse items
        items = self._parse_items(items_text)

        if not items:
            logger.warning("No items found")
            return None

        result = {
            'type': 'supply',
            'supplier': supplier,
            'items': items
        }

        if account:
            result['account'] = account

        logger.info(f"✅ Parsed supply: {supplier}, {len(items)} items")
        return result

    def _parse_items(self, text: str) -> List[Dict]:
        """
        Parse items from text

        Pattern: <name> <quantity_expression> <по/за> <price/sum>

        Examples:
            "айсберг 4.4 по 1450"
            "фри 15 упаковок по 2.5 по 3350"
            "помидоры 10.5 минус 0.5 по 850"
            "огурцы 5 за 5000"
        """
        items = []

        # Split text into tokens
        tokens = text.split()

        i = 0
        while i < len(tokens):
            # Try to find an item pattern
            item = self._extract_item_from_position(tokens, i)

            if item:
                items.append(item)
                # Skip tokens that were consumed
                i = item['_end_index']
            else:
                i += 1

        return items

    def _extract_item_from_position(self, tokens: List[str], start_idx: int) -> Optional[Dict]:
        """
        Try to extract an item starting from a position in tokens

        Pattern: [name words...] [quantity expression] [по/за keyword] [price/sum]
        """
        if start_idx >= len(tokens):
            return None

        # Look for a price keyword (по/за/цена/сумма)
        price_keyword_idx = None
        for i in range(start_idx, min(start_idx + 20, len(tokens))):
            token_lower = tokens[i].lower()
            if token_lower in self.price_keywords or token_lower in self.sum_keywords:
                price_keyword_idx = i
                break

        if price_keyword_idx is None:
            return None

        # Price/sum value should be right after the keyword
        if price_keyword_idx + 1 >= len(tokens):
            return None

        price_value_str = tokens[price_keyword_idx + 1]
        price_value = self._parse_number(price_value_str)

        if price_value is None:
            return None

        # Everything from start to before keyword is: name + quantity
        item_tokens = tokens[start_idx:price_keyword_idx]

        if len(item_tokens) < 2:  # Need at least name + quantity
            return None

        # Find where quantity starts (first number-like token from the end)
        qty_start_idx = None
        for i in range(len(item_tokens) - 1, -1, -1):
            if re.search(r'\d', item_tokens[i]):
                qty_start_idx = i
                break

        if qty_start_idx is None or qty_start_idx == 0:
            return None

        # Name is everything before quantity
        name_tokens = item_tokens[:qty_start_idx]
        name = ' '.join(name_tokens)

        # Quantity expression is from qty_start_idx to end
        qty_tokens = item_tokens[qty_start_idx:]
        qty_expr = ' '.join(qty_tokens)

        # Parse quantity
        qty = self._parse_quantity(qty_expr)

        if qty is None:
            return None

        # Determine price type
        price_type_keyword = tokens[price_keyword_idx].lower()
        price_type = 'sum' if price_type_keyword in self.sum_keywords else 'price'

        # Calculate actual price per unit
        if price_type == 'sum':
            calculated_price = price_value / qty if qty > 0 else 0
        else:
            calculated_price = price_value

        item = {
            'name': name,
            'qty': qty,
            'price': calculated_price,
            'qty_expression': qty_expr,
            'price_type': price_type,
            'price_value': price_value,
            '_end_index': price_keyword_idx + 2  # Skip past price value
        }

        logger.debug(f"Extracted item: {item}")
        return item

    def _parse_quantity(self, expr: str) -> Optional[float]:
        """
        Parse quantity expression with arithmetic

        Examples:
            "4.4" → 4.4
            "15 упаковок по 2.5" → 15 * 2.5 = 37.5
            "10.5 минус 0.5" → 10.5 - 0.5 = 10.0
            "10.8 плюс 10.2" → 10.8 + 10.2 = 21.0
            "10 на 2.5" → 10 * 2.5 = 25.0
        """
        expr = expr.lower().strip()

        # Remove unit words
        expr = re.sub(r'\b(кг|шт|штук|штуки|л|литр|упаковок|упак|упаковка)\b', '', expr)
        expr = expr.strip()

        # Check for arithmetic operations
        # Pattern: number operation number
        for op_word, op_symbol in self.operations.items():
            if op_word in expr:
                parts = re.split(rf'\s*{op_word}\s*', expr)
                if len(parts) == 2:
                    left = self._parse_number(parts[0])
                    right = self._parse_number(parts[1])

                    if left is not None and right is not None:
                        if op_symbol == '+':
                            result = left + right
                        elif op_symbol == '-':
                            result = left - right
                        elif op_symbol == '*':
                            result = left * right
                        else:
                            result = left

                        logger.debug(f"Arithmetic: {left} {op_symbol} {right} = {result}")
                        return result

        # No operations, just parse as number
        return self._parse_number(expr)

    def _parse_number(self, text: str) -> Optional[float]:
        """
        Parse number from text, handling spaces and commas

        Examples:
            "1450" → 1450.0
            "1 450" → 1450.0
            "18 500" → 18500.0
            "4.4" → 4.4
            "4,4" → 4.4
            "18 тысяч 500" → 18500.0
            "1 500.50" → 1500.50
        """
        text = text.strip()

        # Handle "X тысяч Y" format
        thousands_match = re.search(r'(\d+)\s*тысяч[иа]*\s*(\d+)?', text, re.IGNORECASE)
        if thousands_match:
            thousands = int(thousands_match.group(1))
            units = int(thousands_match.group(2)) if thousands_match.group(2) else 0
            result = thousands * 1000 + units
            logger.debug(f"Thousands format: {text} → {result}")
            return float(result)

        # Remove spaces between digits
        text = re.sub(r'(\d)\s+(\d)', r'\1\2', text)

        # Replace comma with dot for decimal separator
        text = text.replace(',', '.')

        # Extract number
        number_match = re.search(r'[\d\.]+', text)
        if number_match:
            try:
                return float(number_match.group())
            except ValueError:
                logger.warning(f"Could not convert to float: {text}")
                return None

        return None


# Singleton instance
_advanced_supply_parser = None


def get_advanced_supply_parser() -> AdvancedSupplyParser:
    """Get singleton AdvancedSupplyParser instance"""
    global _advanced_supply_parser
    if _advanced_supply_parser is None:
        _advanced_supply_parser = AdvancedSupplyParser()
    return _advanced_supply_parser
