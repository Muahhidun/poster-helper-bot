"""Simple regex-based parser as fallback"""
import re
import logging
from typing import Optional, Dict

logger = logging.getLogger(__name__)


class SimpleParser:
    """Simple regex-based transaction parser"""

    # Pattern: <category> <amount> <comment>
    # Examples:
    #   "Донерщик 7500 Максат"
    #   "Повара 12000 Ислам"
    #   "Оставил в кассе 8000 курьер Нурлан"

    CATEGORIES = {
        # Зарплаты
        'донер': 'донерщик',
        'донерщик': 'донерщик',
        'донор': 'донерщик',  # частая опечатка
        'повар': 'повара',
        'повара': 'повара',
        'повора': 'повара',  # опечатка
        'поворо': 'повара',  # опечатка
        'кассир': 'кассиры',
        'кассиры': 'кассиры',
        'касир': 'кассиры',  # опечатка
        'курьер': 'курьер',
        'курьеры': 'курьер',
        'курер': 'курьер',  # опечатка
        'кухрабочая': 'кухрабочая',
        'мойщица': 'кухрабочая',
        'официант': 'официанты',
        'официанты': 'официанты',
        'зарплата': 'зарплата',
        # Операционные расходы
        'логистика': 'логистика',
        'доставка': 'логистика',
        'обед': 'обед и развозка',
        'развозка': 'обед и развозка',
        'отчисления': 'отчисления',
        'налог': 'отчисления',
        'коммунальные': 'коммунальные платежи',
        'коммуналка': 'коммунальные платежи',
        'платежи': 'коммунальные платежи',
        'улучшения': 'улучшения апгрейд',
        'апгрейд': 'улучшения апгрейд',
        'ремонт': 'улучшения апгрейд',
        'мыломойка': 'мыломойка',
        'мойка': 'мыломойка',
        'упаковки': 'упаковки и расходники',
        'расходники': 'упаковки и расходники',
        'аренда': 'аренда',
        'маркетинг': 'маркетинг',
        'реклама': 'маркетинг',
        'поставки': 'поставки',
        'хозяйственные': 'хозяйственные расходы',
        'хозрасходы': 'хозяйственные расходы',
        'банк': 'банковские услуги и комиссии',
        'банковские': 'банковские услуги и комиссии',
        'комиссия': 'банковские услуги и комиссии',
        'единовременный': 'единовременный расход',
        'разовый': 'единовременный расход',
        'актуализация': 'актуализация',
        # Доходы
        'приход': 'приход поступления',
        'поступления': 'приход поступления',
        'доход': 'приход поступления',
    }

    ACCOUNTS = {
        'касса': 'касса',
        'касипай': 'касипай',
        'kaspi': 'касипай',
        'каспи': 'касипай',
        'закуп': 'закуп',
        'закупы': 'закуп',
        'оставил': 'закуп',
        'wolt': 'wolt',
        'волт': 'wolt',
        'инкассация': 'инкассация',
        'вечером': 'инкассация',
        'денежный': 'денежный ящик',
        'ящик': 'денежный ящик',
        'кассира': 'денежный ящик',
        'дома': 'деньги дома',
        'отложенные': 'деньги дома',
        'прибыль': 'прибыль',
        'налоги': 'на налоги',
        'форте': 'форте банк',
        'халык': 'халык банк',
    }

    def parse_transfer(self, text: str) -> Optional[Dict]:
        """Parse transfer (перевод) from text"""
        try:
            text_lower = text.lower().strip()
            logger.info(f"Parsing transfer: '{text}'")

            # Check if it's a transfer
            transfer_keywords = ['перевод', 'перевести', 'переведи', 'перевел']
            is_transfer = any(kw in text_lower for kw in transfer_keywords)

            # Also check for "с ... в ..." or "со счёта ... на счёт" pattern
            if not is_transfer:
                if ('с ' in text_lower and ' в ' in text_lower) or \
                   ('со счёт' in text_lower and 'на счёт' in text_lower) or \
                   ('со счет' in text_lower and 'на счет' in text_lower):
                    is_transfer = True

            if not is_transfer:
                return None

            # Extract amount
            amount_pattern = r'(\d[\d\s,]*\d|\d+)'
            amounts = re.findall(amount_pattern, text_lower)
            if not amounts:
                return None

            amount_str = amounts[0].replace(' ', '').replace(',', '')
            amount = int(amount_str)

            # Extract accounts
            # Patterns: "с X в Y", "со счёта X на счёт Y"
            account_from = None
            account_to = None

            # Try to find account names
            for key, value in self.ACCOUNTS.items():
                if key in text_lower:
                    # Determine if it's "from" or "to" based on position
                    pos = text_lower.find(key)

                    # Check what's before this account name
                    before = text_lower[:pos].lower()

                    if any(w in before[-20:] for w in ['с ', 'со ', 'откуда', 'списать']):
                        account_from = value
                    elif any(w in before[-20:] for w in ['в ', 'на ', 'куда', 'зачислить']):
                        account_to = value

            # Extract comment
            words = text.split()
            comment = ""

            # Find "комментарий" keyword
            for i, word in enumerate(words):
                if 'коммент' in word.lower():
                    comment = " ".join(words[i+1:]).strip()
                    break

            result = {
                'type': 'transfer',
                'amount': amount,
                'account_from': account_from or 'касипай',  # default
                'account_to': account_to or 'касса',  # default
                'comment': comment,
                'category': None
            }

            logger.info(f"✅ Transfer parsed: {result}")
            return result

        except Exception as e:
            logger.error(f"Transfer parsing failed: {e}")
            return None

    def parse_transaction(self, text: str) -> Optional[Dict]:
        """
        Parse transaction from text

        Args:
            text: Input text

        Returns:
            Dict with parsed data or None
        """
        try:
            text_lower = text.lower().strip()
            logger.info(f"Simple parsing: '{text}'")

            # Check if it's a transfer first
            transfer = self.parse_transfer(text)
            if transfer:
                return transfer

            # Extract amount (number with optional spaces/commas)
            amount_pattern = r'(\d[\d\s,]*\d|\d+)'
            amounts = re.findall(amount_pattern, text_lower)

            if not amounts:
                logger.warning("No amount found")
                return None

            # Get first number as amount
            amount_str = amounts[0].replace(' ', '').replace(',', '')
            amount = int(amount_str)

            # Find category
            category = None
            for key, value in self.CATEGORIES.items():
                if key in text_lower:
                    category = value
                    break

            if not category:
                logger.warning("No category found")
                return None

            # Find account (optional)
            account_from = 'закуп'  # default
            for key, value in self.ACCOUNTS.items():
                if key in text_lower:
                    account_from = value
                    break

            # Extract comment
            comment = ""

            # Check for explicit "Комментарий X" or "Комментариях X" pattern
            comment_match = re.search(r'коммент[а-я]*\s+(.+?)(?:\.|$)', text, re.IGNORECASE)
            if comment_match:
                comment = comment_match.group(1).strip()
            else:
                # Extract comment (everything after amount that's not a known keyword)
                words = text.split()

                # Find amount position
                amount_word = None
                for word in words:
                    if re.search(r'\d+', word):
                        amount_word = word
                        break

                if amount_word:
                    # Get words after amount
                    try:
                        amount_idx = words.index(amount_word)
                        comment_words = words[amount_idx + 1:]

                        # Filter out known keywords
                        keywords = set(self.CATEGORIES.keys()) | set(self.ACCOUNTS.keys()) | {'тенге', 'тг', 'кзт', 'транзакция', 'оставил', 'в', 'кассе', 'со', 'счёта', 'счета', 'категория', 'категории'}
                        comment_words = [w for w in comment_words if w.lower() not in keywords]

                        comment = " ".join(comment_words).strip()
                    except ValueError:
                        pass

            result = {
                'amount': amount,
                'category': category,
                'comment': comment,
                'account_from': account_from,
                'type': 'expense'
            }

            logger.info(f"✅ Parsed: {result}")
            return result

        except Exception as e:
            logger.error(f"Simple parsing failed: {e}")
            return None


    def parse_supply(self, text: str) -> Optional[Dict]:
        """Parse supply (поставка) from text

        Supports:
        - Simple: "Айсберг 2.2 кг по 1600" → qty=2.2, price=1600
        - Total price: "Фри 2.5 кг за 3350" → qty=2.5, sum=3350, price=3350/2.5
        - Packages: "5 упаковок по 4 кг по 1500" → qty=20, price=1500
        - Tare: "11 кг минус 500 грамм по 850" → qty=10.5, price=850
        """
        try:
            text_lower = text.lower().strip()
            logger.info(f"Parsing supply: '{text}'")

            # Check if it's a supply
            supply_keywords = ['поставка', 'постав', 'закуп']
            is_supply = any(kw in text_lower for kw in supply_keywords)

            if not is_supply:
                return None

            # Extract supplier
            supplier = None
            words = text.split()
            for i, word in enumerate(words):
                if 'поставщ' in word.lower() and i + 1 < len(words):
                    supplier = words[i + 1].strip('.,;')
                    break

            # Extract account (optional)
            account = None
            for key in self.ACCOUNTS.keys():
                if key in text_lower:
                    account = self.ACCOUNTS[key]
                    break

            # Extract items - split by common separators (comma, semicolon, but NOT period for decimals)
            items = []
            # Remove "поставка" keyword first to get just the items text
            items_text = re.sub(r'поста[вw]к[аи]?\s*', '', text, flags=re.IGNORECASE).strip()

            # Split by comma, semicolon, or colon (for "поставщик X:")
            parts = re.split(r'[,;]|(?:поставщ[иа][кч]\s+[^:]+:)', items_text)

            for part in parts:
                part = part.strip()
                if not part:
                    continue

                part_lower = part.lower()

                # Skip if no price indicator
                if 'по' not in part_lower and 'за' not in part_lower:
                    continue

                item_data = self._parse_supply_item(part)
                if item_data:
                    items.append(item_data)

            if not items:
                logger.warning("No items found in supply text")
                return None

            result = {
                'type': 'supply',
                'supplier': supplier,
                'account': account or 'оставил в кассе',
                'items': items
            }

            logger.info(f"✅ Supply parsed: {result}")
            return result

        except Exception as e:
            logger.error(f"Supply parsing failed: {e}")
            return None

    def _parse_supply_item(self, text: str) -> Optional[Dict]:
        """Parse single supply item with advanced quantity/price parsing"""
        try:
            text_lower = text.lower().strip()

            # Extract packages: "5 упаковок по 4 кг"
            packages_match = re.search(r'(\d+\.?\d*)\s*(?:упаковок|упаковки|уп|пачек|пачки|коробок|коробки)', text_lower)
            pack_size_match = re.search(r'по\s+(\d+\.?\d*)\s*(кг|л|г|мл)', text_lower) if packages_match else None

            # Extract tare: "минус 500 грамм" or "минус 0.5 кг"
            tare = 0
            tare_match = re.search(r'минус\s+(\d+\.?\d*)\s*(кг|г|грамм)', text_lower)
            if tare_match:
                tare_val = float(tare_match.group(1))
                tare_unit = tare_match.group(2)
                if tare_unit in ['г', 'грамм']:
                    tare = tare_val / 1000  # convert to kg
                else:
                    tare = tare_val

            # Extract base quantity
            qty_match = re.search(r'(\d+\.?\d*)\s*(кг|л|г|мл|шт|штук|штуки)', text_lower)
            if not qty_match:
                return None

            base_qty = float(qty_match.group(1))
            unit = qty_match.group(2)

            # Convert to base units
            if unit in ['г', 'грамм']:
                base_qty = base_qty / 1000  # to kg
            elif unit in ['мл']:
                base_qty = base_qty / 1000  # to liters

            # Calculate final quantity
            if packages_match and pack_size_match:
                # Packages: qty = num_packages * pack_size
                num_packages = float(packages_match.group(1))
                pack_size = float(pack_size_match.group(1))
                qty = num_packages * pack_size - tare
            else:
                qty = base_qty - tare

            # Extract price or sum
            price = None
            total_sum = None

            # "за X" or "на X" = total sum (check first as it has priority)
            za_match = re.search(r'(?:за|на)\s+(\d+[\d\s,]*)', text_lower)

            if za_match:
                # Total sum specified
                sum_str = za_match.group(1).replace(' ', '').replace(',', '')
                total_sum = int(sum_str)
                price = round(total_sum / qty, 2) if qty > 0 else 0
            else:
                # "по X" = price per unit (find LAST match, not first)
                # This handles "5 упаковок по 4 кг по 1500" - we want 1500, not 4
                po_matches = list(re.finditer(r'по\s+(\d+[\d\s,]*?)(?:\s|$)', text_lower))

                if po_matches:
                    # Get the last "по X" which should be the price
                    last_po = po_matches[-1]

                    # Check if it's followed by a unit (кг, л, г) - if so, it's not the price
                    after_match = text_lower[last_po.end():last_po.end()+5]
                    if not re.match(r'^\s*(?:кг|л|г|мл|шт)', after_match):
                        # It's the price!
                        price_str = last_po.group(1).replace(' ', '').replace(',', '')
                        price = int(price_str)

            if not price and not total_sum:
                return None

            # Extract item name (everything before first number)
            name_match = re.match(r'^([а-яёА-ЯЁa-zA-Z\s-]+)', text)

            if name_match:
                item_name = name_match.group(1).strip()
                # Clean up common words
                item_name = re.sub(r'\b(?:упаковок|упаковки|уп|пачек|пачки|штук|штуки)\b', '', item_name, flags=re.IGNORECASE).strip()
            else:
                # No name found - use generic "Товар"
                item_name = "Товар"

            return {
                'name': item_name,
                'qty': round(qty, 3),
                'price': price if price else round(total_sum / qty, 2)
            }

        except Exception as e:
            logger.error(f"Item parsing failed for '{text}': {e}")
            return None


# Singleton
_simple_parser = None


def get_simple_parser() -> SimpleParser:
    """Get singleton SimpleParser instance"""
    global _simple_parser
    if _simple_parser is None:
        _simple_parser = SimpleParser()
    return _simple_parser
