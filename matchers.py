"""Matching and aliasing services for categories and accounts"""
import csv
import logging
from typing import Optional, Dict, List, Tuple
from pathlib import Path
from rapidfuzz import fuzz, process
from config import CATEGORY_ALIASES_CSV, ACCOUNTS_CSV
import config

logger = logging.getLogger(__name__)


def normalize_text_for_matching(text: str) -> str:
    """
    Normalize text for better fuzzy matching

    - Remove leading/trailing quotes
    - Remove extra whitespace
    - Lowercase
    """
    if not text:
        return ""

    # Strip and lowercase
    text = text.strip().lower()

    # Remove leading/trailing quotes (", ', «, »)
    text = text.strip('"\'«»')

    # Remove extra spaces
    text = ' '.join(text.split())

    return text


class CategoryMatcher:
    """Matcher for finance categories using aliases"""

    def __init__(self, telegram_user_id: Optional[int] = None):
        """
        Initialize CategoryMatcher for a specific user

        Args:
            telegram_user_id: Telegram user ID for multi-tenant support.
                             If None, uses global data directory (legacy mode)
        """
        self.telegram_user_id = telegram_user_id
        self.aliases: Dict[str, Tuple[int, str]] = {}  # alias -> (category_id, category_name)

        # Determine CSV path based on user (with fallback to global)
        if telegram_user_id:
            user_dir = config.get_user_data_dir(telegram_user_id)
            user_csv = user_dir / "alias_category_mapping.csv"
            # Use global CSV if user-specific one doesn't exist
            self.csv_path = user_csv if user_csv.exists() else CATEGORY_ALIASES_CSV
        else:
            self.csv_path = CATEGORY_ALIASES_CSV

        self.load_aliases()

    def load_aliases(self):
        """Load category aliases from CSV"""
        if not self.csv_path.exists():
            logger.warning(f"Category aliases file not found: {self.csv_path}")
            return

        logger.info(f"Loading category aliases from: {self.csv_path}")

        with open(self.csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                alias = row['alias_text'].strip().lower()
                category_id = int(row['poster_category_id'])
                category_name = row['poster_category_name'].strip()
                self.aliases[alias] = (category_id, category_name)

        logger.info(f"Loaded {len(self.aliases)} category aliases for user {self.telegram_user_id}")

    def match(self, text: str, score_cutoff: int = 80) -> Optional[Tuple[int, str]]:
        """
        Match category by text (exact or fuzzy)

        Args:
            text: Category text to match
            score_cutoff: Minimum fuzzy match score

        Returns:
            Tuple of (category_id, category_name) or None
        """
        if not text:
            return None

        text_lower = text.strip().lower()

        # 1. Exact match
        if text_lower in self.aliases:
            logger.debug(f"Category exact match: '{text}' -> {self.aliases[text_lower]}")
            return self.aliases[text_lower]

        # 2. Fuzzy match
        aliases_list = list(self.aliases.keys())
        match = process.extractOne(
            text_lower,
            aliases_list,
            scorer=fuzz.WRatio,
            score_cutoff=score_cutoff
        )

        if match:
            matched_alias = match[0]
            score = match[1]
            result = self.aliases[matched_alias]
            logger.debug(f"Category fuzzy match: '{text}' -> {result} (score={score})")
            return result

        logger.warning(f"Category not matched: '{text}'")
        return None

    def add_alias(self, alias_text: str, category_id: int, category_name: str):
        """Add new alias and save to CSV"""
        alias_lower = alias_text.strip().lower()
        self.aliases[alias_lower] = (category_id, category_name)

        # Append to CSV (use user-specific or global path)
        with open(self.csv_path, 'a', encoding='utf-8', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([alias_text, category_id, category_name, ""])

        logger.info(f"Added category alias: '{alias_text}' -> {category_id} (user {self.telegram_user_id})")


class AccountMatcher:
    """Matcher for accounts (cash/bank) using aliases"""

    def __init__(self, telegram_user_id: Optional[int] = None):
        """
        Initialize AccountMatcher for a specific user

        Args:
            telegram_user_id: Telegram user ID for multi-tenant support.
                             If None, uses global data directory (legacy mode)
        """
        self.telegram_user_id = telegram_user_id
        self.accounts: Dict[int, Dict] = {}  # account_id -> account_info
        self.aliases: Dict[str, int] = {}  # alias -> account_id

        # Determine CSV path based on user (with fallback to global)
        if telegram_user_id:
            user_dir = config.get_user_data_dir(telegram_user_id)
            user_csv = user_dir / "poster_accounts.csv"
            # Use global CSV if user-specific one doesn't exist
            self.csv_path = user_csv if user_csv.exists() else ACCOUNTS_CSV
        else:
            self.csv_path = ACCOUNTS_CSV

        self.load_accounts()

    def load_accounts(self):
        """Load accounts from CSV"""
        if not self.csv_path.exists():
            logger.warning(f"Accounts file not found: {self.csv_path}")
            return

        logger.info(f"Loading accounts from: {self.csv_path}")

        with open(self.csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                account_id = int(row['account_id'])
                # Support both 'name' and 'account_name' column names
                name = row.get('name', row.get('account_name', '')).strip()
                # Support both 'type' and 'account_type' column names
                account_type = row.get('type', row.get('account_type', '')).strip()
                aliases_str = row.get('aliases', '').strip()

                self.accounts[account_id] = {
                    'id': account_id,
                    'name': name,
                    'type': account_type
                }

                # Add main name as alias
                self.aliases[name.lower()] = account_id

                # Add additional aliases
                if aliases_str:
                    for alias in aliases_str.split('|'):
                        self.aliases[alias.strip().lower()] = account_id

        logger.info(f"Loaded {len(self.accounts)} accounts with {len(self.aliases)} aliases for user {self.telegram_user_id}")

    def match(self, text: str, score_cutoff: int = 80) -> Optional[int]:
        """
        Match account by text (exact or fuzzy)

        Args:
            text: Account text to match
            score_cutoff: Minimum fuzzy match score

        Returns:
            Account ID or None
        """
        if not text:
            return None

        text_lower = text.strip().lower()

        # 1. Exact match
        if text_lower in self.aliases:
            account_id = self.aliases[text_lower]
            logger.debug(f"Account exact match: '{text}' -> {account_id}")
            return account_id

        # 2. Fuzzy match
        aliases_list = list(self.aliases.keys())
        match = process.extractOne(
            text_lower,
            aliases_list,
            scorer=fuzz.WRatio,
            score_cutoff=score_cutoff
        )

        if match:
            matched_alias = match[0]
            score = match[1]
            account_id = self.aliases[matched_alias]
            logger.debug(f"Account fuzzy match: '{text}' -> {account_id} (score={score})")
            return account_id

        logger.warning(f"Account not matched: '{text}'")
        return None

    def get_account_name(self, account_id: int) -> Optional[str]:
        """Get account name by ID"""
        account = self.accounts.get(account_id)
        return account['name'] if account else None


class SupplierMatcher:
    """Matcher for suppliers using aliases"""

    def __init__(self, telegram_user_id: Optional[int] = None):
        """
        Initialize SupplierMatcher for a specific user

        Args:
            telegram_user_id: Telegram user ID for multi-tenant support.
                             If None, uses global data directory (legacy mode)
        """
        self.telegram_user_id = telegram_user_id
        self.suppliers: Dict[int, Dict] = {}  # supplier_id -> supplier_info
        self.aliases: Dict[str, int] = {}  # alias -> supplier_id

        # Determine CSV path based on user (with fallback to global)
        if telegram_user_id:
            user_dir = config.get_user_data_dir(telegram_user_id)
            user_csv = user_dir / "poster_suppliers.csv"
            # Fallback to global CSV if user-specific doesn't exist
            self.csv_path = user_csv if user_csv.exists() else (config.DATA_DIR / "poster_suppliers.csv")
        else:
            self.csv_path = config.DATA_DIR / "poster_suppliers.csv"

        self.load_suppliers()

    def load_suppliers(self):
        """Load suppliers from CSV"""
        if not self.csv_path.exists():
            logger.warning(f"Suppliers file not found: {self.csv_path}")
            return

        with open(self.csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                supplier_id = int(row['supplier_id'])
                name = row['name'].strip()
                aliases_str = row.get('aliases', '').strip()

                self.suppliers[supplier_id] = {
                    'id': supplier_id,
                    'name': name
                }

                # Add main name as alias
                self.aliases[name.lower()] = supplier_id

                # Add additional aliases
                if aliases_str:
                    for alias in aliases_str.split('|'):
                        self.aliases[alias.strip().lower()] = supplier_id

        logger.info(f"Loaded {len(self.suppliers)} suppliers with {len(self.aliases)} aliases for user {self.telegram_user_id}")

    def match(self, text: str, score_cutoff: int = 80) -> Optional[int]:
        """Match supplier by text"""
        if not text:
            return None

        text_lower = text.strip().lower()

        # 1. Exact match
        if text_lower in self.aliases:
            supplier_id = self.aliases[text_lower]
            logger.debug(f"Supplier exact match: '{text}' -> {supplier_id}")
            return supplier_id

        # 2. Fuzzy match
        aliases_list = list(self.aliases.keys())
        match = process.extractOne(
            text_lower,
            aliases_list,
            scorer=fuzz.WRatio,
            score_cutoff=score_cutoff
        )

        if match:
            matched_alias = match[0]
            score = match[1]
            supplier_id = self.aliases[matched_alias]
            logger.debug(f"Supplier fuzzy match: '{text}' -> {supplier_id} (score={score})")
            return supplier_id

        logger.warning(f"Supplier not matched: '{text}'")
        return None

    def get_supplier_name(self, supplier_id: int) -> Optional[str]:
        """Get supplier name by ID"""
        supplier = self.suppliers.get(supplier_id)
        return supplier['name'] if supplier else None


class IngredientMatcher:
    """Matcher for ingredients using fuzzy search and aliases"""

    def __init__(self, telegram_user_id: Optional[int] = None):
        """
        Initialize IngredientMatcher for a specific user

        Args:
            telegram_user_id: Telegram user ID for multi-tenant support.
                             If None, uses global data directory (legacy mode)
        """
        self.telegram_user_id = telegram_user_id
        self.ingredients: Dict[int, Dict] = {}  # ingredient_id -> ingredient_info
        self.names: Dict[str, int] = {}  # name -> ingredient_id
        self.aliases: Dict[str, int] = {}  # alias -> ingredient_id

        # Determine CSV paths based on user (with fallback to global)
        if telegram_user_id:
            user_dir = config.get_user_data_dir(telegram_user_id)
            user_ingredients = user_dir / "poster_ingredients.csv"
            user_aliases = user_dir / "alias_item_mapping.csv"
            # Fallback to global CSVs if user-specific don't exist
            self.ingredients_csv = user_ingredients if user_ingredients.exists() else (config.DATA_DIR / "poster_ingredients.csv")
            self.aliases_csv = user_aliases if user_aliases.exists() else (config.DATA_DIR / "alias_item_mapping.csv")
        else:
            self.ingredients_csv = config.DATA_DIR / "poster_ingredients.csv"
            self.aliases_csv = config.DATA_DIR / "alias_item_mapping.csv"

        self.load_ingredients()
        self.load_aliases()

    def load_ingredients(self):
        """Load ingredients from CSV (with account_name for multi-account support)"""
        if not self.ingredients_csv.exists():
            logger.warning(f"Ingredients file not found: {self.ingredients_csv}")
            return

        logger.info(f"Loading ingredients from: {self.ingredients_csv}")

        with open(self.ingredients_csv, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                ingredient_id = int(row['ingredient_id'])
                name = row['ingredient_name'].strip()
                unit = row.get('unit', '').strip()
                account_name = row.get('account_name', 'Unknown').strip()

                # Poster API type: "1"=ingredient, "2"=semi-product
                # Map to internal type names for Poster supply API
                poster_type = row.get('type', '1').strip()
                type_map = {'1': 'ingredient', '2': 'semi_product'}
                item_type = type_map.get(poster_type, 'ingredient')

                self.ingredients[ingredient_id] = {
                    'id': ingredient_id,
                    'name': name,
                    'unit': unit,
                    'account_name': account_name,
                    'type': item_type
                }

                # Add name for matching
                self.names[name.lower()] = ingredient_id

        logger.info(f"✅ Loaded {len(self.ingredients)} ingredients from CSV for user {self.telegram_user_id}")

        # Debug: показать первые 5 ID
        if self.ingredients:
            sample_ids = list(self.ingredients.keys())[:5]
            logger.info(f"   Sample ingredient IDs: {sample_ids}")

    def load_aliases(self):
        """Load ingredient aliases from database (with CSV fallback)"""
        # Try loading from database first (for Railway)
        if self.telegram_user_id:
            try:
                from database import get_database
                db = get_database()
                db_aliases = db.get_ingredient_aliases(self.telegram_user_id)

                logger.info(f"📋 Found {len(db_aliases)} aliases in database for user {self.telegram_user_id}")

                filtered_count = 0
                for row in db_aliases:
                    # Only load ingredient aliases (skip product aliases)
                    if row.get('source', '').strip().lower() != 'ingredient':
                        continue

                    # Normalize alias text (same as input text normalization)
                    alias = normalize_text_for_matching(row['alias_text'])
                    item_id = int(row['poster_item_id'])

                    # Verify that this ingredient exists
                    if item_id in self.ingredients:
                        self.aliases[alias] = item_id
                    else:
                        filtered_count += 1
                        if filtered_count <= 3:  # Показать первые 3 отфильтрованных
                            logger.warning(f"⚠️ Alias '{alias}' references non-existent ingredient ID {item_id}")

                if filtered_count > 0:
                    logger.warning(f"⚠️ Filtered out {filtered_count}/{len(db_aliases)} aliases (ingredient IDs not found in self.ingredients)")

                logger.info(f"✅ Loaded {len(self.aliases)} ingredient aliases from database for user {self.telegram_user_id}")

                # Debug: показать первые 3 алиаса
                if self.aliases:
                    sample_aliases = list(self.aliases.items())[:3]
                    logger.info(f"   Sample aliases: {sample_aliases}")

                return  # Successfully loaded from DB

            except Exception as e:
                logger.warning(f"Could not load aliases from database: {e}. Falling back to CSV...")

        # Fallback: load from CSV (for local development)
        if not self.aliases_csv.exists():
            logger.warning(f"Item aliases file not found: {self.aliases_csv}")
            return

        with open(self.aliases_csv, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Only load ingredient aliases
                if row.get('source', '').strip().lower() != 'ingredient':
                    continue

                # Normalize alias text
                alias = normalize_text_for_matching(row['alias_text'])
                item_id = int(row['poster_item_id'])

                # Verify that this ingredient exists
                if item_id in self.ingredients:
                    self.aliases[alias] = item_id
                else:
                    logger.warning(f"Alias '{alias}' references non-existent ingredient {item_id}")

        logger.info(f"Loaded {len(self.aliases)} ingredient aliases from CSV for user {self.telegram_user_id}")

    def match(self, text: str, score_cutoff: int = 75) -> Optional[Tuple[int, str, str, int, str]]:
        """
        Match ingredient by text (aliases, exact, or fuzzy)

        Args:
            text: Ingredient text to match
            score_cutoff: Minimum fuzzy match score

        Returns:
            Tuple of (ingredient_id, name, unit, score, account_name) or None
            Score is 100 for exact/alias matches, lower for fuzzy
        """
        if not text:
            return None

        # Normalize text for better matching
        text_lower = normalize_text_for_matching(text)

        # 1. Exact alias match (highest priority)
        if text_lower in self.aliases:
            ingredient_id = self.aliases[text_lower]
            ingredient = self.ingredients[ingredient_id]
            logger.debug(f"Ingredient alias match: '{text}' -> {ingredient}")
            return (ingredient_id, ingredient['name'], ingredient['unit'], 100, ingredient.get('account_name', 'Unknown'))

        # 2. Exact name match
        if text_lower in self.names:
            ingredient_id = self.names[text_lower]
            ingredient = self.ingredients[ingredient_id]
            logger.debug(f"Ingredient exact match: '{text}' -> {ingredient}")
            return (ingredient_id, ingredient['name'], ingredient['unit'], 100, ingredient.get('account_name', 'Unknown'))

        # 3. Fuzzy match on aliases first (higher confidence)
        if self.aliases:
            aliases_list = list(self.aliases.keys())

            # Try token_set_ratio first - better for partial word matches
            alias_match = process.extractOne(
                text_lower,
                aliases_list,
                scorer=fuzz.token_set_ratio,
                score_cutoff=score_cutoff
            )

            # Log top 3 matches for debugging (always show for ingredients)
            top_matches = process.extract(text_lower, aliases_list, scorer=fuzz.token_set_ratio, limit=3)
            logger.info(f"      Top 3 ingredient alias matches: {[(m[0][:40], f'{m[1]:.1f}') for m in top_matches]}")

            if alias_match:  # Removed higher threshold - use same as score_cutoff
                matched_alias = alias_match[0]
                score = alias_match[1]

                # Защита от false positives при token_set_ratio:
                # Если score очень высокий (>95), но совпадает только часть токенов,
                # перепроверяем с WRatio чтобы избежать match только по одному общему слову
                alias_words = set(matched_alias.split())
                text_words = set(text_lower.split())
                common_tokens = alias_words & text_words

                # Suspicious: высокий score, но либо input/alias разной длины, либо мало общих токенов
                is_suspicious = (
                    score > 95 and (
                        (len(alias_words) > 1 and len(common_tokens) == 1) or  # Только 1 общий токен
                        (len(alias_words) <= 2 and len(text_words) >= 3) or     # Короткий alias, длинный input
                        (len(text_words) == 1 and len(alias_words) > 1)         # Короткий input, длинный alias
                    )
                )

                if is_suspicious:
                    # Проверяем как длину так и WRatio
                    wratio_score = fuzz.WRatio(text_lower, matched_alias)
                    length_ratio = len(text_lower) / len(matched_alias) if len(matched_alias) > 0 else 1.0

                    logger.info(f"      ⚠️  Suspicious match: '{text_lower}' → '{matched_alias}' (token_set={score:.1f}, WRatio={wratio_score:.1f}, length_ratio={length_ratio:.2f}, common_tokens={len(common_tokens)})")

                    # Reject если:
                    # 1. Только 1 общий токен И (WRatio < 85 ИЛИ длина input < 60% от alias)
                    # 2. ИЛИ длина input < 40% от alias (слишком короткий)
                    should_reject = (
                        (len(common_tokens) == 1 and (wratio_score < 85 or length_ratio < 0.6)) or
                        (length_ratio < 0.4)
                    )

                    if should_reject:
                        # False positive - пропускаем этот alias
                        logger.info(f"      ❌ Rejected due to suspicious match")
                        alias_match = None  # Nullify match, will try names next

                if alias_match:  # Re-check after potential rejection
                    ingredient_id = self.aliases[matched_alias]
                    ingredient = self.ingredients[ingredient_id]
                    logger.info(f"✅ Ingredient fuzzy alias match: '{text}' -> {ingredient['name']} (score={score})")
                    return (ingredient_id, ingredient['name'], ingredient['unit'], score, ingredient.get('account_name', 'Unknown'))

        # 4. Fuzzy match on ingredient names
        names_list = list(self.names.keys())
        name_match = process.extractOne(
            text_lower,
            names_list,
            scorer=fuzz.WRatio,
            score_cutoff=score_cutoff
        )

        # Log top 3 ingredient name matches
        if names_list:
            top_name_matches = process.extract(text_lower, names_list, scorer=fuzz.WRatio, limit=3)
            logger.info(f"      Top 3 ingredient name matches: {[(m[0][:40], f'{m[1]:.1f}') for m in top_name_matches]}")

        if name_match:
            matched_name = name_match[0]
            score = name_match[1]
            ingredient_id = self.names[matched_name]
            ingredient = self.ingredients[ingredient_id]
            logger.debug(f"Ingredient fuzzy match: '{text}' -> {ingredient} (score={score})")
            return (ingredient_id, ingredient['name'], ingredient['unit'], score, ingredient.get('account_name', 'Unknown'))

        logger.warning(f"Ingredient not matched: '{text}'")
        return None

    def match_with_priority(self, text: str, score_cutoff: int = 75, primary_account: str = "Pizzburg") -> Optional[Tuple[int, str, str, int, str]]:
        """
        Match ingredient with priority: search in primary account first, then secondary accounts

        Logic: "Всё общее идет автоматически в Pizzburg, а то что нет в Pizzburg - поставляется в Pizzburg-cafe"

        Args:
            text: Ingredient text to match
            score_cutoff: Minimum fuzzy match score
            primary_account: Name of primary account (default: "Pizzburg")

        Returns:
            Tuple of (ingredient_id, name, unit, score, account_name) or None
        """
        if not text:
            return None

        # Normalize text for better matching
        text_lower = normalize_text_for_matching(text)

        # Get all possible matches across all accounts
        all_matches = []

        # 1. Check aliases first
        if text_lower in self.aliases:
            ingredient_id = self.aliases[text_lower]
            ingredient = self.ingredients[ingredient_id]
            all_matches.append((ingredient_id, ingredient['name'], ingredient['unit'], 100, ingredient.get('account_name', 'Unknown')))

        # 2. Check exact name matches
        if text_lower in self.names and not all_matches:
            ingredient_id = self.names[text_lower]
            ingredient = self.ingredients[ingredient_id]
            all_matches.append((ingredient_id, ingredient['name'], ingredient['unit'], 100, ingredient.get('account_name', 'Unknown')))

        # 3. Fuzzy matching - search in aliases first
        if not all_matches and self.aliases:
            aliases_list = list(self.aliases.keys())
            alias_matches = process.extract(
                text_lower,
                aliases_list,
                scorer=fuzz.token_set_ratio,
                score_cutoff=score_cutoff,
                limit=10  # Get top 10 to find best across accounts
            )

            for matched_alias, score, _ in alias_matches:
                ingredient_id = self.aliases[matched_alias]
                ingredient = self.ingredients[ingredient_id]
                all_matches.append((ingredient_id, ingredient['name'], ingredient['unit'], score, ingredient.get('account_name', 'Unknown')))

        # 4. Fuzzy matching - search in names
        if not all_matches:
            names_list = list(self.names.keys())
            name_matches = process.extract(
                text_lower,
                names_list,
                scorer=fuzz.WRatio,
                score_cutoff=score_cutoff,
                limit=10
            )

            for matched_name, score, _ in name_matches:
                ingredient_id = self.names[matched_name]
                ingredient = self.ingredients[ingredient_id]
                all_matches.append((ingredient_id, ingredient['name'], ingredient['unit'], score, ingredient.get('account_name', 'Unknown')))

        if not all_matches:
            logger.warning(f"Ingredient not matched (priority search): '{text}'")
            return None

        # Priority logic: prefer primary account (Pizzburg), then others
        primary_matches = [m for m in all_matches if m[4] == primary_account]
        secondary_matches = [m for m in all_matches if m[4] != primary_account]

        if primary_matches:
            # Return best match from primary account
            best_match = max(primary_matches, key=lambda x: x[3])  # Sort by score
            logger.info(f"✅ Found in PRIMARY account ({primary_account}): '{text}' -> {best_match[1]} (score={best_match[3]})")
            return best_match
        elif secondary_matches:
            # Return best match from secondary accounts
            best_match = max(secondary_matches, key=lambda x: x[3])
            logger.info(f"✅ Found in SECONDARY account ({best_match[4]}): '{text}' -> {best_match[1]} (score={best_match[3]})")
            return best_match

        return None

    def get_ingredient_info(self, ingredient_id: int) -> Optional[Dict]:
        """Get ingredient info by ID"""
        return self.ingredients.get(ingredient_id)

    def add_alias(self, alias_text: str, ingredient_id: int, notes: str = ""):
        """
        Add new ingredient alias and save to database (with CSV fallback)

        Args:
            alias_text: The alias text to add
            ingredient_id: The ingredient ID this alias maps to
            notes: Optional notes about this alias
        """
        if ingredient_id not in self.ingredients:
            logger.error(f"Cannot add alias: ingredient {ingredient_id} does not exist")
            return False

        alias_lower = normalize_text_for_matching(alias_text)
        ingredient = self.ingredients[ingredient_id]

        # Check if alias already exists (avoid duplicates)
        if alias_lower in self.aliases and self.aliases[alias_lower] == ingredient_id:
            logger.info(f"Alias already exists: '{alias_text}' -> {ingredient_id}")
            return True  # Not an error, just already exists

        # Add to memory
        self.aliases[alias_lower] = ingredient_id

        # Try saving to database first (for Railway/PostgreSQL)
        if self.telegram_user_id:
            try:
                from database import get_database
                db = get_database()
                success = db.add_ingredient_alias(
                    telegram_user_id=self.telegram_user_id,
                    alias_text=alias_text,
                    poster_item_id=ingredient_id,
                    poster_item_name=ingredient['name'],
                    source='ingredient',
                    notes=notes
                )
                if success:
                    logger.info(f"✅ Added ingredient alias to database: '{alias_text}' -> {ingredient_id} ({ingredient['name']}) for user {self.telegram_user_id}")
                    return True
            except Exception as e:
                logger.warning(f"Could not save alias to database: {e}. Falling back to CSV...")

        # Fallback: save to CSV (for local development)
        try:
            with open(self.aliases_csv, 'a', encoding='utf-8', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    alias_text,
                    ingredient_id,
                    ingredient['name'],
                    'ingredient',
                    notes
                ])
            logger.info(f"Added ingredient alias to CSV: '{alias_text}' -> {ingredient_id} ({ingredient['name']}) for user {self.telegram_user_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to save alias to CSV: {e}")
            return False

    def get_top_matches(self, text: str, limit: int = 5, score_cutoff: int = 60) -> List[Tuple[int, str, str, int]]:
        """
        Get top N matching ingredients for manual selection

        Args:
            text: Text to match
            limit: Maximum number of results
            score_cutoff: Minimum score threshold

        Returns:
            List of tuples: (ingredient_id, name, unit, score)
        """
        text_lower = text.strip().lower()
        results = []

        # Search in both aliases and names
        search_space = {**self.aliases, **self.names}
        search_list = list(search_space.keys())

        if not search_list:
            return []

        matches = process.extract(
            text_lower,
            search_list,
            scorer=fuzz.WRatio,
            score_cutoff=score_cutoff,
            limit=limit * 3  # Get more to account for duplicates
        )

        for match_result in matches:
            matched_text, score, _ = match_result
            ingredient_id = search_space[matched_text]
            ingredient = self.ingredients[ingredient_id]
            results.append((
                ingredient_id,
                ingredient['name'],
                ingredient['unit'],
                score
            ))

        # Remove duplicates (same ingredient_id) keeping highest score
        seen = {}
        for ing_id, name, unit, score in results:
            if ing_id not in seen or seen[ing_id][3] < score:
                seen[ing_id] = (ing_id, name, unit, score)

        # Sort by score descending
        final_results = sorted(seen.values(), key=lambda x: x[3], reverse=True)
        return final_results[:limit]


class ProductMatcher:
    """Matcher for products using fuzzy search and aliases"""

    def __init__(self, telegram_user_id: Optional[int] = None):
        """
        Initialize ProductMatcher for a specific user

        Args:
            telegram_user_id: Telegram user ID for multi-tenant support.
                             If None, uses global data directory (legacy mode)
        """
        self.telegram_user_id = telegram_user_id
        self.products: Dict[int, Dict] = {}  # product_id -> product_info
        self.names: Dict[str, int] = {}  # name -> product_id
        self.aliases: Dict[str, int] = {}  # alias -> product_id

        # Determine CSV paths based on user (with fallback to global)
        if telegram_user_id:
            user_dir = config.get_user_data_dir(telegram_user_id)
            user_products = user_dir / "poster_products.csv"
            user_aliases = user_dir / "alias_item_mapping.csv"
            # Fallback to global CSVs if user-specific don't exist
            self.products_csv = user_products if user_products.exists() else (config.DATA_DIR / "poster_products.csv")
            self.aliases_csv = user_aliases if user_aliases.exists() else (config.DATA_DIR / "alias_item_mapping.csv")
        else:
            self.products_csv = config.DATA_DIR / "poster_products.csv"
            self.aliases_csv = config.DATA_DIR / "alias_item_mapping.csv"

        self.load_products()
        self.load_aliases()

    def load_products(self):
        """Load products from CSV (with account_name for multi-account support)"""
        if not self.products_csv.exists():
            logger.warning(f"Products file not found: {self.products_csv}")
            return

        logger.info(f"Loading products from: {self.products_csv}")

        with open(self.products_csv, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                product_id = int(row['product_id'])
                name = row['product_name'].strip()
                category = row.get('category_name', '').strip()
                account_name = row.get('account_name', 'Unknown').strip()

                # Только товары категории "Напитки" могут быть в поставках
                # Остальные категории (Бургеры, Пиццы и т.д.) - это техкарты, они не закупаются
                if category != 'Напитки':
                    continue

                self.products[product_id] = {
                    'id': product_id,
                    'name': name,
                    'category': category,
                    'unit': 'шт',  # Products are usually counted in pieces
                    'account_name': account_name
                }

                # Add name for matching
                self.names[name.lower()] = product_id

        logger.info(f"✅ Loaded {len(self.products)} products from CSV for user {self.telegram_user_id}")

        # Debug: показать первые 5 ID
        if self.products:
            sample_ids = list(self.products.keys())[:5]
            logger.info(f"   Sample product IDs: {sample_ids}")

    def load_aliases(self):
        """Load product aliases from database (with CSV fallback)"""
        # Try loading from database first (for Railway)
        if self.telegram_user_id:
            try:
                from database import get_database
                db = get_database()
                db_aliases = db.get_ingredient_aliases(self.telegram_user_id)

                logger.info(f"📋 Found {len(db_aliases)} aliases in database for user {self.telegram_user_id}")

                filtered_count = 0
                product_count = 0

                for row in db_aliases:
                    # Only load product aliases (skip ingredient aliases)
                    if row.get('source', '').strip().lower() != 'product':
                        continue

                    product_count += 1
                    # Normalize alias text (same as input text normalization)
                    alias = normalize_text_for_matching(row['alias_text'])
                    item_id = int(row['poster_item_id'])

                    # Verify that this product exists
                    if item_id in self.products:
                        self.aliases[alias] = item_id
                    else:
                        filtered_count += 1
                        if filtered_count <= 3:  # Показать первые 3 отфильтрованных
                            logger.warning(f"⚠️ Alias '{alias}' references non-existent product ID {item_id}")

                if filtered_count > 0:
                    logger.warning(f"⚠️ Filtered out {filtered_count}/{product_count} product aliases (product IDs not found in self.products)")

                logger.info(f"✅ Loaded {len(self.aliases)} product aliases from database for user {self.telegram_user_id}")

                # Debug: показать первые 3 алиаса
                if self.aliases:
                    sample_aliases = list(self.aliases.items())[:3]
                    logger.info(f"   Sample product aliases: {sample_aliases}")

                return  # Successfully loaded from DB

            except Exception as e:
                logger.warning(f"Could not load product aliases from database: {e}. Falling back to CSV...")

        # Fallback: load from CSV (for local development)
        if not self.aliases_csv.exists():
            logger.warning(f"Item aliases file not found: {self.aliases_csv}")
            return

        with open(self.aliases_csv, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Only load product aliases
                if row.get('source', '').strip().lower() != 'product':
                    continue

                # Normalize alias text
                alias = normalize_text_for_matching(row['alias_text'])
                item_id = int(row['poster_item_id'])

                # Verify that this product exists
                if item_id in self.products:
                    self.aliases[alias] = item_id
                else:
                    logger.warning(f"Alias '{alias}' references non-existent product {item_id}")

        logger.info(f"Loaded {len(self.aliases)} product aliases from CSV for user {self.telegram_user_id}")

    def match(self, text: str, score_cutoff: int = 75) -> Optional[Tuple[int, str, str, int, str]]:
        """
        Match product by text (aliases, exact, or fuzzy)

        Args:
            text: Product text to match
            score_cutoff: Minimum fuzzy match score

        Returns:
            Tuple of (product_id, name, unit, score, account_name) or None
            Score is 100 for exact/alias matches, lower for fuzzy
        """
        if not text:
            return None

        # Normalize text for better matching
        text_lower = normalize_text_for_matching(text)

        # 1. Exact alias match (highest priority)
        if text_lower in self.aliases:
            product_id = self.aliases[text_lower]
            product = self.products[product_id]
            logger.debug(f"Product alias match: '{text}' -> {product}")
            return (product_id, product['name'], product['unit'], 100, product.get('account_name', 'Unknown'))

        # 2. Exact name match
        if text_lower in self.names:
            product_id = self.names[text_lower]
            product = self.products[product_id]
            logger.debug(f"Product exact match: '{text}' -> {product}")
            return (product_id, product['name'], product['unit'], 100, product.get('account_name', 'Unknown'))

        # 3. Fuzzy match on aliases first (higher confidence)
        if self.aliases:
            aliases_list = list(self.aliases.keys())

            # Try token_set_ratio first - better for partial word matches
            alias_match = process.extractOne(
                text_lower,
                aliases_list,
                scorer=fuzz.token_set_ratio,
                score_cutoff=score_cutoff
            )

            # Log top 3 matches for debugging (always show for products)
            top_matches = process.extract(text_lower, aliases_list, scorer=fuzz.token_set_ratio, limit=3)
            logger.info(f"      Top 3 product alias matches: {[(m[0][:40], f'{m[1]:.1f}') for m in top_matches]}")

            if alias_match:  # Removed higher threshold - use same as score_cutoff
                matched_alias = alias_match[0]
                score = alias_match[1]
                product_id = self.aliases[matched_alias]
                product = self.products[product_id]
                logger.info(f"✅ Product fuzzy alias match: '{text}' -> {product['name']} (score={score})")
                return (product_id, product['name'], product['unit'], score, product.get('account_name', 'Unknown'))

        # 4. Fuzzy match on product names
        names_list = list(self.names.keys())
        name_match = process.extractOne(
            text_lower,
            names_list,
            scorer=fuzz.WRatio,
            score_cutoff=score_cutoff
        )

        # Log top 3 product name matches
        if names_list:
            top_name_matches = process.extract(text_lower, names_list, scorer=fuzz.WRatio, limit=3)
            logger.info(f"      Top 3 product name matches: {[(m[0][:40], f'{m[1]:.1f}') for m in top_name_matches]}")

        if name_match:
            matched_name = name_match[0]
            score = name_match[1]
            product_id = self.names[matched_name]
            product = self.products[product_id]
            logger.debug(f"Product fuzzy match: '{text}' -> {product} (score={score})")
            return (product_id, product['name'], product['unit'], score, product.get('account_name', 'Unknown'))

        logger.warning(f"Product not matched: '{text}'")
        return None

    def match_with_priority(self, text: str, score_cutoff: int = 75, primary_account: str = "Pizzburg") -> Optional[Tuple[int, str, str, int, str]]:
        """
        Match product with priority: search in primary account first, then secondary accounts

        Logic: "Всё общее идет автоматически в Pizzburg, а то что нет в Pizzburg - поставляется в Pizzburg-cafe"

        Args:
            text: Product text to match
            score_cutoff: Minimum fuzzy match score
            primary_account: Name of primary account (default: "Pizzburg")

        Returns:
            Tuple of (product_id, name, unit, score, account_name) or None
        """
        if not text:
            return None

        # Normalize text for better matching
        text_lower = normalize_text_for_matching(text)

        # Get all possible matches across all accounts
        all_matches = []

        # 1. Check aliases first
        if text_lower in self.aliases:
            product_id = self.aliases[text_lower]
            product = self.products[product_id]
            all_matches.append((product_id, product['name'], product['unit'], 100, product.get('account_name', 'Unknown')))

        # 2. Check exact name matches
        if text_lower in self.names and not all_matches:
            product_id = self.names[text_lower]
            product = self.products[product_id]
            all_matches.append((product_id, product['name'], product['unit'], 100, product.get('account_name', 'Unknown')))

        # 3. Fuzzy matching - search in aliases first
        if not all_matches and self.aliases:
            aliases_list = list(self.aliases.keys())
            alias_matches = process.extract(
                text_lower,
                aliases_list,
                scorer=fuzz.token_set_ratio,
                score_cutoff=score_cutoff,
                limit=10  # Get top 10 to find best across accounts
            )

            for matched_alias, score, _ in alias_matches:
                product_id = self.aliases[matched_alias]
                product = self.products[product_id]
                all_matches.append((product_id, product['name'], product['unit'], score, product.get('account_name', 'Unknown')))

        # 4. Fuzzy matching - search in names
        if not all_matches:
            names_list = list(self.names.keys())
            name_matches = process.extract(
                text_lower,
                names_list,
                scorer=fuzz.WRatio,
                score_cutoff=score_cutoff,
                limit=10
            )

            for matched_name, score, _ in name_matches:
                product_id = self.names[matched_name]
                product = self.products[product_id]
                all_matches.append((product_id, product['name'], product['unit'], score, product.get('account_name', 'Unknown')))

        if not all_matches:
            logger.warning(f"Product not matched (priority search): '{text}'")
            return None

        # Priority logic: prefer primary account (Pizzburg), then others
        primary_matches = [m for m in all_matches if m[4] == primary_account]
        secondary_matches = [m for m in all_matches if m[4] != primary_account]

        if primary_matches:
            # Return best match from primary account
            best_match = max(primary_matches, key=lambda x: x[3])  # Sort by score
            logger.info(f"✅ Found in PRIMARY account ({primary_account}): '{text}' -> {best_match[1]} (score={best_match[3]})")
            return best_match
        elif secondary_matches:
            # Return best match from secondary accounts
            best_match = max(secondary_matches, key=lambda x: x[3])
            logger.info(f"✅ Found in SECONDARY account ({best_match[4]}): '{text}' -> {best_match[1]} (score={best_match[3]})")
            return best_match

        return None

    def get_product_info(self, product_id: int) -> Optional[Dict]:
        """Get product info by ID"""
        return self.products.get(product_id)

    def add_alias(self, alias_text: str, product_id: int, notes: str = ""):
        """
        Add new product alias and save to database (with CSV fallback)

        Args:
            alias_text: The alias text to add
            product_id: The product ID this alias maps to
            notes: Optional notes about this alias
        """
        if product_id not in self.products:
            logger.error(f"Cannot add alias: product {product_id} does not exist")
            return False

        alias_lower = normalize_text_for_matching(alias_text)
        product = self.products[product_id]

        # Check if alias already exists (avoid duplicates)
        if alias_lower in self.aliases and self.aliases[alias_lower] == product_id:
            logger.info(f"Alias already exists: '{alias_text}' -> {product_id}")
            return True  # Not an error, just already exists

        # Add to memory
        self.aliases[alias_lower] = product_id

        # Try saving to database first (for Railway/PostgreSQL)
        if self.telegram_user_id:
            try:
                from database import get_database
                db = get_database()
                success = db.add_ingredient_alias(
                    telegram_user_id=self.telegram_user_id,
                    alias_text=alias_text,
                    poster_item_id=product_id,
                    poster_item_name=product['name'],
                    source='product',
                    notes=notes
                )
                if success:
                    logger.info(f"✅ Added product alias to database: '{alias_text}' -> {product_id} ({product['name']}) for user {self.telegram_user_id}")
                    return True
            except Exception as e:
                logger.warning(f"Could not save alias to database: {e}. Falling back to CSV...")

        # Fallback: save to CSV (for local development)
        try:
            with open(self.aliases_csv, 'a', encoding='utf-8', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    alias_text,
                    product_id,
                    product['name'],
                    'product',
                    notes
                ])
            logger.info(f"Added product alias to CSV: '{alias_text}' -> {product_id} ({product['name']}) for user {self.telegram_user_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to save alias to CSV: {e}")
            return False

    def get_top_matches(self, text: str, limit: int = 5, score_cutoff: int = 60) -> List[Tuple[int, str, str, int]]:
        """
        Get top N matching products for manual selection

        Args:
            text: Text to match
            limit: Maximum number of results
            score_cutoff: Minimum score threshold

        Returns:
            List of tuples: (product_id, name, unit, score)
        """
        text_lower = text.strip().lower()
        results = []

        # Search in both aliases and names
        search_space = {**self.aliases, **self.names}
        search_list = list(search_space.keys())

        if not search_list:
            return []

        matches = process.extract(
            text_lower,
            search_list,
            scorer=fuzz.WRatio,
            score_cutoff=score_cutoff,
            limit=limit * 3  # Get more to account for duplicates
        )

        for match_result in matches:
            matched_text, score, _ = match_result
            product_id = search_space[matched_text]
            product = self.products[product_id]
            results.append((
                product_id,
                product['name'],
                product['unit'],
                score
            ))

        # Remove duplicates (same product_id) keeping highest score
        seen = {}
        for prod_id, name, unit, score in results:
            if prod_id not in seen or seen[prod_id][3] < score:
                seen[prod_id] = (prod_id, name, unit, score)

        # Sort by score descending
        final_results = sorted(seen.values(), key=lambda x: x[3], reverse=True)
        return final_results[:limit]


# Cache for user-specific matchers (changed from singleton to per-user cache)
_category_matchers: Dict[Optional[int], CategoryMatcher] = {}
_account_matchers: Dict[Optional[int], AccountMatcher] = {}
_supplier_matchers: Dict[Optional[int], SupplierMatcher] = {}
_ingredient_matchers: Dict[Optional[int], IngredientMatcher] = {}
_product_matchers: Dict[Optional[int], ProductMatcher] = {}


def get_category_matcher(telegram_user_id: Optional[int] = None) -> CategoryMatcher:
    """
    Get CategoryMatcher instance for a specific user

    Args:
        telegram_user_id: Telegram user ID. If None, uses legacy config mode

    Returns:
        CategoryMatcher instance for the user
    """
    global _category_matchers
    if telegram_user_id not in _category_matchers:
        _category_matchers[telegram_user_id] = CategoryMatcher(telegram_user_id)
    return _category_matchers[telegram_user_id]


def get_account_matcher(telegram_user_id: Optional[int] = None) -> AccountMatcher:
    """
    Get AccountMatcher instance for a specific user

    Args:
        telegram_user_id: Telegram user ID. If None, uses legacy config mode

    Returns:
        AccountMatcher instance for the user
    """
    global _account_matchers
    if telegram_user_id not in _account_matchers:
        _account_matchers[telegram_user_id] = AccountMatcher(telegram_user_id)
    return _account_matchers[telegram_user_id]


def get_supplier_matcher(telegram_user_id: Optional[int] = None) -> SupplierMatcher:
    """
    Get SupplierMatcher instance for a specific user

    Args:
        telegram_user_id: Telegram user ID. If None, uses legacy config mode

    Returns:
        SupplierMatcher instance for the user
    """
    global _supplier_matchers
    if telegram_user_id not in _supplier_matchers:
        _supplier_matchers[telegram_user_id] = SupplierMatcher(telegram_user_id)
    return _supplier_matchers[telegram_user_id]


def get_ingredient_matcher(telegram_user_id: Optional[int] = None) -> IngredientMatcher:
    """
    Get IngredientMatcher instance for a specific user

    Args:
        telegram_user_id: Telegram user ID. If None, uses legacy config mode

    Returns:
        IngredientMatcher instance for the user
    """
    global _ingredient_matchers
    if telegram_user_id not in _ingredient_matchers:
        _ingredient_matchers[telegram_user_id] = IngredientMatcher(telegram_user_id)
    return _ingredient_matchers[telegram_user_id]


def get_product_matcher(telegram_user_id: Optional[int] = None) -> ProductMatcher:
    """
    Get ProductMatcher instance for a specific user

    Args:
        telegram_user_id: Telegram user ID. If None, uses legacy config mode

    Returns:
        ProductMatcher instance for the user
    """
    global _product_matchers
    if telegram_user_id not in _product_matchers:
        _product_matchers[telegram_user_id] = ProductMatcher(telegram_user_id)
    return _product_matchers[telegram_user_id]
