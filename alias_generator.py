"""Automatic alias generation from Poster data"""
import csv
import logging
import re
from pathlib import Path
from typing import List, Set, Dict

logger = logging.getLogger(__name__)


class AliasGenerator:
    """Generate aliases automatically from Poster API data"""

    @staticmethod
    def generate_category_aliases(category_name: str) -> List[str]:
        """
        Generate multiple aliases for a category name

        Examples:
            "Мыломойка" → ["мыломойка", "мойка", "мыломоечная"]
            "Kaspi Pay" → ["каспи", "kaspi", "kaspi pay", "каспи пей"]
            "Логистика - Доставка продуктов" → ["логистика", "доставка", "логистика доставка"]
        """
        aliases = set()
        name_lower = category_name.strip().lower()

        # 1. Full name
        aliases.add(name_lower)

        # 2. Remove special characters and split
        cleaned = re.sub(r'[^\w\s]', ' ', name_lower)
        words = [w for w in cleaned.split() if len(w) > 2]

        # Add each word
        for word in words:
            aliases.add(word)

        # 3. Combinations of first + last word
        if len(words) >= 2:
            aliases.add(f"{words[0]} {words[-1]}")

        # 4. First word only if it's meaningful
        if words and len(words[0]) >= 4:
            aliases.add(words[0])

        # 5. Remove duplicates and sort by length (longer = more specific)
        return sorted(aliases, key=len, reverse=True)[:5]

    @staticmethod
    def generate_account_aliases(account_name: str) -> List[str]:
        """
        Generate aliases for account names

        Examples:
            "Kaspi Pay" → ["каспи", "kaspi", "kaspi pay"]
            "Инкассация (вечером)" → ["инкассация", "вечер", "инкассация вечер"]
        """
        aliases = set()
        name_lower = account_name.strip().lower()

        # 1. Full name
        aliases.add(name_lower)

        # 2. Remove parentheses content and special chars
        without_parens = re.sub(r'\([^)]*\)', '', name_lower)
        cleaned = re.sub(r'[^\w\s]', ' ', without_parens)
        words = [w for w in cleaned.split() if len(w) > 2]

        # Add main words
        for word in words:
            aliases.add(word)

        # 3. Two-word combinations
        if len(words) >= 2:
            aliases.add(f"{words[0]} {words[1]}")

        return sorted(aliases, key=len, reverse=True)[:4]

    @staticmethod
    def generate_supplier_aliases(supplier_name: str) -> List[str]:
        """
        Generate aliases for supplier names

        Examples:
            "ТОО Инарин" → ["инарин", "тоо инарин"]
            "Yaposha Market" → ["япоша", "yaposha", "yaposha market"]
        """
        aliases = set()
        name_lower = supplier_name.strip().lower()

        # 1. Full name
        aliases.add(name_lower)

        # 2. Remove "ТОО", "ИП", "ООО" prefixes
        cleaned = re.sub(r'\b(тоо|ип|ооо|llc)\b', '', name_lower).strip()
        if cleaned and cleaned != name_lower:
            aliases.add(cleaned)

        # 3. Remove special characters
        cleaned = re.sub(r'[^\w\s]', ' ', cleaned)
        words = [w for w in cleaned.split() if len(w) > 2]

        # Add each significant word
        for word in words:
            if len(word) >= 4:
                aliases.add(word)

        # 4. First word if meaningful
        if words and len(words[0]) >= 3:
            aliases.add(words[0])

        return sorted(aliases, key=len, reverse=True)[:4]

    @classmethod
    def create_category_aliases_csv(
        cls,
        categories: List[Dict],
        csv_path: Path
    ) -> int:
        """
        Create alias_category_mapping.csv from Poster categories

        Args:
            categories: List of categories from Poster API
            csv_path: Path to save CSV file

        Returns:
            Number of aliases created
        """
        csv_path.parent.mkdir(parents=True, exist_ok=True)

        aliases_created = 0
        with open(csv_path, 'w', encoding='utf-8', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['alias_text', 'poster_category_id', 'poster_category_name', 'notes'])

            for category in categories:
                category_id = category.get('category_id')
                category_name = category.get('category_name', '')

                if not category_id or not category_name:
                    continue

                # Generate aliases
                aliases = cls.generate_category_aliases(category_name)

                for alias in aliases:
                    writer.writerow([alias, category_id, category_name, 'Auto-generated'])
                    aliases_created += 1

        logger.info(f"Created {aliases_created} category aliases at {csv_path}")
        return aliases_created

    @classmethod
    def create_account_aliases_csv(
        cls,
        accounts: List[Dict],
        csv_path: Path
    ) -> int:
        """
        Create/update poster_accounts.csv with aliases

        Args:
            accounts: List of accounts from Poster API
            csv_path: Path to save CSV file

        Returns:
            Number of accounts processed
        """
        csv_path.parent.mkdir(parents=True, exist_ok=True)

        accounts_processed = 0
        with open(csv_path, 'w', encoding='utf-8', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['account_id', 'name', 'type', 'aliases'])

            for account in accounts:
                account_id = account.get('account_id') or account.get('finance_id')
                account_name = account.get('name') or account.get('finance_name', '')
                account_type = account.get('type', '')

                if not account_id or not account_name:
                    continue

                # Generate aliases
                aliases = cls.generate_account_aliases(account_name)
                aliases_str = '|'.join(aliases)

                writer.writerow([account_id, account_name, account_type, aliases_str])
                accounts_processed += 1

        logger.info(f"Created {accounts_processed} account entries at {csv_path}")
        return accounts_processed

    @classmethod
    def create_supplier_aliases_csv(
        cls,
        suppliers: List[Dict],
        csv_path: Path
    ) -> int:
        """
        Create/update poster_suppliers.csv with aliases

        Args:
            suppliers: List of suppliers from Poster API
            csv_path: Path to save CSV file

        Returns:
            Number of suppliers processed
        """
        csv_path.parent.mkdir(parents=True, exist_ok=True)

        suppliers_processed = 0
        with open(csv_path, 'w', encoding='utf-8', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['supplier_id', 'name', 'aliases'])

            for supplier in suppliers:
                supplier_id = supplier.get('supplier_id')
                supplier_name = supplier.get('supplier_name', '')

                if not supplier_id or not supplier_name:
                    continue

                # Generate aliases
                aliases = cls.generate_supplier_aliases(supplier_name)
                aliases_str = '|'.join(aliases)

                writer.writerow([supplier_id, supplier_name, aliases_str])
                suppliers_processed += 1

        logger.info(f"Created {suppliers_processed} supplier entries at {csv_path}")
        return suppliers_processed
