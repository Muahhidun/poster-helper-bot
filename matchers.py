"""Matching and aliasing services for categories and accounts"""
import csv
import logging
from typing import Optional, Dict, List, Tuple
from pathlib import Path
from rapidfuzz import fuzz, process
from config import CATEGORY_ALIASES_CSV, ACCOUNTS_CSV, MIN_MATCH_CONFIDENCE
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

def normalize_supplier_text(text: str) -> str:
    """Normalize supplier name by removing quotes, legal forms, and standardizing spaces."""
    if not text:
        return ""
    import re
    # Lowercase
    t = text.lower().strip()
    # Remove quotes and common symbols
    t = re.sub(r'["\'«»“”`~!@#$%^&*()_+=]', '', t)
    # Remove common corporate prefixes/suffixes (legal forms)
    prefixes = [
        r'\bтоварищество\s+с\s+ограниченной\s+ответственностью\b',
        r'\bограниченной\s+ответственностью\b',
        r'\bтоварищество\b',
        r'\bиндивидуальный\s+предприниматель\b',
        r'\bакционерное\s+общество\b',
        r'\bтоо\b',
        r'\bип\b',
        r'\bао\b',
        r'\bллп\b',
        r'\bllp\b',
    ]
    for pattern in prefixes:
        t = re.sub(pattern, '', t)
    # Strip remaining spaces and punctuation
    t = t.strip(' .,-()')
    # Replace multiple spaces with a single space
    t = re.sub(r'\s+', ' ', t)
    return t


def transliterate_latin_to_cyrillic(text: str) -> str:
    """Transliterate basic English phonetics of supplier names to Cyrillic."""
    if not text:
        return ""
    translit_map = {
        'shch': 'щ', 'sh': 'ш', 'ch': 'ч', 'zh': 'ж', 'yo': 'ё', 'yu': 'ю', 'ya': 'я', 'kh': 'х', 'ts': 'ц',
        'a': 'а', 'b': 'б', 'v': 'в', 'g': 'г', 'd': 'д', 'e': 'е', 'z': 'з', 'i': 'и', 'j': 'й',
        'k': 'к', 'l': 'л', 'm': 'м', 'n': 'н', 'o': 'о', 'p': 'п', 'r': 'р', 's': 'с',
        't': 'т', 'u': 'у', 'f': 'ф', 'h': 'х', 'c': 'к', 'y': 'и', 'x': 'кс', 'w': 'в', 'q': 'к'
    }
    res = text.lower()
    # Sort keys by length descending to match multi-char sequences first
    for key in sorted(translit_map.keys(), key=len, reverse=True):
        res = res.replace(key, translit_map[key])
    return res



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
        self.normalized_aliases: Dict[str, int] = {}  # normalized_alias -> supplier_id

        # Determine CSV path based on user (with fallback to global)
        if telegram_user_id:
            user_dir = config.get_user_data_dir(telegram_user_id)
            user_csv = user_dir / "poster_suppliers.csv"
            # Fallback to global CSV if user-specific doesn't exist
            self.csv_path = user_csv if user_csv.exists() else (config.DATA_DIR / "poster_suppliers.csv")
        else:
            self.csv_path = config.DATA_DIR / "poster_suppliers.csv"

        self.load_suppliers()
        self.load_aliases()

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
                name_clean = name.lower()
                self.aliases[name_clean] = supplier_id
                norm_name = normalize_supplier_text(name_clean)
                if norm_name:
                    self.normalized_aliases[norm_name] = supplier_id

                # Add additional aliases
                if aliases_str:
                    for alias in aliases_str.split('|'):
                        alias_clean = alias.strip().lower()
                        self.aliases[alias_clean] = supplier_id
                        norm_alias = normalize_supplier_text(alias_clean)
                        if norm_alias:
                            self.normalized_aliases[norm_alias] = supplier_id

        logger.info(f"Loaded {len(self.suppliers)} suppliers with {len(self.aliases)} aliases ({len(self.normalized_aliases)} normalized) for user {self.telegram_user_id}")

    def load_aliases(self):
        """Load supplier aliases from database"""
        if self.telegram_user_id:
            try:
                from database import get_database
                db = get_database()
                db_aliases = db.get_supplier_aliases(self.telegram_user_id)
                
                logger.info(f"📋 Found {len(db_aliases)} supplier aliases in database for user {self.telegram_user_id}")
                
                for row in db_aliases:
                    alias_text = row['alias_text'].strip().lower()
                    supplier_id = int(row['poster_supplier_id'])
                    
                    # Verify supplier exists in our database/CSV
                    if supplier_id in self.suppliers:
                        self.aliases[alias_text] = supplier_id
                        norm_alias = normalize_supplier_text(alias_text)
                        if norm_alias:
                            self.normalized_aliases[norm_alias] = supplier_id
                            
                logger.info(f"✅ Loaded supplier aliases from database for user {self.telegram_user_id}")
            except Exception as e:
                logger.warning(f"Could not load supplier aliases from database: {e}")

    def match(self, text: str, score_cutoff: int = 80) -> Optional[int]:
        """Match supplier by text"""
        if not text:
            return None

        # 1. First, try exact match on raw lower text
        text_lower = text.strip().lower()
        if text_lower in self.aliases:
            supplier_id = self.aliases[text_lower]
            logger.info(f"Supplier exact match: '{text}' -> {supplier_id}")
            return supplier_id

        # 2. Try exact match on normalized text
        norm_text = normalize_supplier_text(text)
        if norm_text in self.normalized_aliases:
            supplier_id = self.normalized_aliases[norm_text]
            logger.info(f"Supplier exact match (normalized): '{text}' -> {supplier_id}")
            return supplier_id

        # 3. Transliterate normalized text and try exact match
        translit_text = transliterate_latin_to_cyrillic(norm_text)
        if translit_text in self.normalized_aliases:
            supplier_id = self.normalized_aliases[translit_text]
            logger.info(f"Supplier exact match (transliterated): '{text}' -> {supplier_id}")
            return supplier_id

        # 4. Fuzzy match normalized and transliterated text against normalized aliases
        candidates = [norm_text]
        if translit_text != norm_text:
            candidates.append(translit_text)
            
        norm_aliases_list = list(self.normalized_aliases.keys())
        if not norm_aliases_list:
            return None
            
        best_match = None
        best_score = -1
        
        for candidate in candidates:
            if not candidate:
                continue
            match = process.extractOne(
                candidate,
                norm_aliases_list,
                scorer=fuzz.WRatio,
                score_cutoff=score_cutoff
            )
            if match:
                matched_alias = match[0]
                score = match[1]
                if score > best_score:
                    best_score = score
                    best_match = matched_alias

        if best_match and best_score >= score_cutoff:
            supplier_id = self.normalized_aliases[best_match]
            logger.info(f"Supplier fuzzy match (normalized/translit): '{text}' -> {supplier_id} (score={best_score}, matched_alias='{best_match}')")
            return supplier_id

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
        self.ingredients: Dict[Tuple[int, str], Dict] = {}  # (ingredient_id, account_name) -> ingredient_info
        self.names: Dict[str, List[Tuple[int, str]]] = {}  # name.lower() -> [(ingredient_id, account_name), ...]
        self.aliases: Dict[str, List[Tuple[int, str]]] = {}  # alias.lower() -> [(ingredient_id, account_name), ...]
        self._name_to_info: Dict[Tuple[str, str], Dict] = {}  # (name.lower(), account_name) -> full ingredient info
        self._id_entries: Dict[int, list] = {}  # ingredient_id -> [info1, info2, ...] for all accounts

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

                info = {
                    'id': ingredient_id,
                    'name': name,
                    'unit': unit,
                    'account_name': account_name,
                    'type': item_type
                }
                self.ingredients[(ingredient_id, account_name)] = info

                # Add name for matching
                self.names.setdefault(name.lower(), []).append((ingredient_id, account_name))
                self._name_to_info[(name.lower(), account_name)] = info
                self._id_entries.setdefault(ingredient_id, []).append(info)

        logger.info(f"✅ Loaded {len(self.ingredients)} ingredients from CSV for user {self.telegram_user_id}")

        # Debug: показать первые 5 ID
        if self.ingredients:
            sample_ids = list(self.ingredients.keys())[:5]
            logger.info(f"   Sample ingredient keys: {sample_ids}")

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

                    # Find candidates by ID to resolve collisions
                    candidates = self._id_entries.get(item_id, [])
                    if not candidates:
                        filtered_count += 1
                        continue

                    target_info = None
                    if len(candidates) == 1:
                        target_info = candidates[0]
                    else:
                        db_item_name = row.get('poster_item_name', '').strip().lower()
                        best_match = None
                        best_score = -1
                        for cand in candidates:
                            cand_name = cand['name'].lower()
                            if db_item_name == cand_name:
                                target_info = cand
                                break
                            score = fuzz.ratio(db_item_name, cand_name)
                            if score > best_score:
                                best_score = score
                                best_match = cand
                        if not target_info and best_match and best_score >= 80:
                            target_info = best_match
                        if not target_info:
                            for cand in candidates:
                                if cand.get('account_name') == 'Pizzburg':
                                    target_info = cand
                                    break
                            if not target_info:
                                target_info = candidates[0]

                    if target_info:
                        self.aliases.setdefault(alias, []).append((target_info['id'], target_info['account_name']))
                    else:
                        filtered_count += 1

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

                candidates = self._id_entries.get(item_id, [])
                for cand in candidates:
                    self.aliases.setdefault(alias, []).append((cand['id'], cand['account_name']))

        logger.info(f"Loaded {len(self.aliases)} ingredient aliases from CSV for user {self.telegram_user_id}")

    def match(self, text: str, score_cutoff: int = MIN_MATCH_CONFIDENCE, target_account: Optional[str] = None) -> Optional[Tuple[int, str, str, int, str]]:
        """
        Match ingredient by text (aliases, exact, or fuzzy)

        Args:
            text: Ingredient text to match
            score_cutoff: Minimum fuzzy match score
            target_account: Optional target account name to prioritize

        Returns:
            Tuple of (ingredient_id, name, unit, score, account_name) or None
            Score is 100 for exact/alias matches, lower for fuzzy
        """
        return self.match_with_priority(text, score_cutoff=score_cutoff, target_account=target_account)

    def match_with_priority(self, text: str, score_cutoff: int = MIN_MATCH_CONFIDENCE, primary_account: str = "Pizzburg", target_account: Optional[str] = None) -> Optional[Tuple[int, str, str, int, str]]:
        """
        Match ingredient with priority: search in primary/target account first, then secondary accounts

        Logic: "Всё общее идет автоматически в Pizzburg, а то что нет в Pizzburg - поставляется в Pizzburg-cafe"

        Args:
            text: Ingredient text to match
            score_cutoff: Minimum fuzzy match score
            primary_account: Name of primary account (default: "Pizzburg")
            target_account: Optional target account to prioritize above primary

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
            for ingredient_id, account_name in self.aliases[text_lower]:
                ingredient = self.ingredients.get((ingredient_id, account_name))
                if ingredient:
                    all_matches.append((ingredient_id, ingredient['name'], ingredient['unit'], 100, account_name))

        # 2. Check exact name matches
        if not all_matches and text_lower in self.names:
            for ingredient_id, account_name in self.names[text_lower]:
                ingredient = self.ingredients.get((ingredient_id, account_name))
                if ingredient:
                    all_matches.append((ingredient_id, ingredient['name'], ingredient['unit'], 100, account_name))

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
                text_words = set(text_lower.split())
                matched_words = set(matched_alias.split())
                common_tokens = text_words & matched_words
                
                # Stricter rejection for aliases 
                is_suspicious = (
                    score < 88 and 
                    len(common_tokens) < 1 and 
                    len(text_words) > 0 and 
                    len(matched_words) > 0
                )
                
                is_generic_overlap = (
                    score >= 80 and 
                    len(common_tokens) == 1 and 
                    len(text_words) > 1 and 
                    len(matched_words) > 1 and
                    len(list(common_tokens)[0]) < 4
                )

                if is_suspicious or is_generic_overlap:
                    logger.info(f"      ❌ Rejected alias priority match: '{text_lower}' → '{matched_alias}' (score={score:.1f})")
                    continue

                for ingredient_id, account_name in self.aliases[matched_alias]:
                    ingredient = self.ingredients.get((ingredient_id, account_name))
                    if not ingredient:
                        continue
                    all_matches.append((ingredient_id, ingredient['name'], ingredient['unit'], score, account_name))

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
                text_words = set(text_lower.split())
                matched_words = set(matched_name.split())
                common_tokens = text_words & matched_words
                
                # Stricter rejection for raw names 
                is_suspicious = (
                    score < 85 and 
                    len(common_tokens) < 2 and 
                    len(text_words) > 1 and 
                    len(matched_words) > 1
                )
                
                is_generic_overlap = (
                    score >= 85 and 
                    len(common_tokens) == 1 and 
                    len(text_words) > 1 and 
                    len(matched_words) > 1 and
                    len(list(common_tokens)[0]) < 5
                )

                if is_suspicious or is_generic_overlap:
                    logger.info(f"      ❌ Rejected name priority match: '{text_lower}' → '{matched_name}' (score={score:.1f})")
                    continue
                    
                for ingredient_id, account_name in self.names[matched_name]:
                    ingredient = self.ingredients.get((ingredient_id, account_name))
                    if not ingredient:
                        continue
                    all_matches.append((ingredient['id'], ingredient['name'], ingredient['unit'], score, account_name))

        if not all_matches:
            logger.warning(f"Ingredient not matched (priority search): '{text}'")
            return None

        # Priority logic: sort matches
        def sort_key(m):
            acc = m[4]
            is_target = (acc == target_account) if target_account else False
            is_primary = (acc == primary_account)
            return (is_target, is_primary, m[3])

        best_match = max(all_matches, key=sort_key)
        logger.info(f"✅ Found ingredient match: '{text}' -> {best_match[1]} (score={best_match[3]}, account={best_match[4]})")
        return best_match

    def get_ingredient_info(self, ingredient_id: int, account_name: Optional[str] = None) -> Optional[Dict]:
        """Get ingredient info by ID (and optional account name)"""
        if account_name:
            return self.ingredients.get((ingredient_id, account_name))
        
        candidates = self._id_entries.get(ingredient_id, [])
        if not candidates:
            return None
        
        # Prefer Pizzburg if present
        for cand in candidates:
            if cand.get('account_name') == 'Pizzburg':
                return cand
        return candidates[0]

    def add_alias(self, alias_text: str, ingredient_id: int, notes: str = "", account_name: Optional[str] = None):
        """
        Add new ingredient alias and save to database (with CSV fallback)

        Args:
            alias_text: The alias text to add
            ingredient_id: The ingredient ID this alias maps to
            notes: Optional notes about this alias
            account_name: Optional account name this alias maps to
        """
        candidates = self._id_entries.get(ingredient_id, [])
        if not candidates:
            logger.error(f"Cannot add alias: ingredient {ingredient_id} does not exist")
            return False

        target_candidates = [c for c in candidates if c['account_name'] == account_name] if account_name else candidates
        if not target_candidates:
            target_candidates = candidates

        alias_lower = normalize_text_for_matching(alias_text)

        # Add to memory for all matching candidates
        for cand in target_candidates:
            acc_name = cand['account_name']
            current_aliases = self.aliases.setdefault(alias_lower, [])
            if (ingredient_id, acc_name) not in current_aliases:
                current_aliases.append((ingredient_id, acc_name))

        # Save to database (only for the first/main candidate to avoid duplicates, but with correct name)
        target_cand = target_candidates[0]
        db_success = False
        if self.telegram_user_id:
            try:
                from database import get_database
                db = get_database()
                db_success = db.add_ingredient_alias(
                    telegram_user_id=self.telegram_user_id,
                    alias_text=alias_text,
                    poster_item_id=ingredient_id,
                    poster_item_name=target_cand['name'],
                    source='ingredient',
                    notes=notes
                )
            except Exception as e:
                logger.error(f"Failed to save alias to database: {e}")

        # Fallback to CSV if not saved to DB
        if not db_success:
            try:
                # Append to CSV
                with open(self.aliases_csv, 'a', encoding='utf-8', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow([
                        self.telegram_user_id or "",
                        alias_text,
                        ingredient_id,
                        target_cand['name'],
                        'ingredient',
                        notes
                    ])
                logger.info(f"Added ingredient alias to CSV: '{alias_text}' -> {ingredient_id} ({target_cand['name']}) for user {self.telegram_user_id}")
                return True
            except Exception as e:
                logger.error(f"Failed to save alias to CSV: {e}")
                return False
        return True

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
            for ingredient_id, account_name in search_space[matched_text]:
                ingredient = self.ingredients.get((ingredient_id, account_name))
                if not ingredient:
                    continue
                display_name = ingredient['name']
                if account_name != 'Pizzburg':
                    display_name = f"{display_name} ({account_name})"
                
                results.append((
                    ingredient_id,
                    display_name,
                    ingredient['unit'],
                    score
                ))

        # Remove duplicates (same ingredient_id and display_name combo) keeping highest score
        seen = {}
        for ing_id, name, unit, score in results:
            combo_key = (ing_id, name)
            if combo_key not in seen or seen[combo_key][3] < score:
                seen[combo_key] = (ing_id, name, unit, score)

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
        self.products: Dict[Tuple[int, str], Dict] = {}  # (product_id, account_name) -> product_info
        self.names: Dict[str, List[Tuple[int, str]]] = {}  # name.lower() -> [(product_id, account_name), ...]
        self.aliases: Dict[str, List[Tuple[int, str]]] = {}  # alias.lower() -> [(product_id, account_name), ...]
        self._name_to_info: Dict[Tuple[str, str], Dict] = {}  # (name.lower(), account_name) -> full product info
        self._id_entries: Dict[int, list] = {}  # product_id -> [info1, info2, ...] for all accounts

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

                info = {
                    'id': product_id,
                    'name': name,
                    'category': category,
                    'unit': 'шт',
                    'account_name': account_name
                }
                self.products[(product_id, account_name)] = info

                # Add name for matching
                self.names.setdefault(name.lower(), []).append((product_id, account_name))
                self._name_to_info[(name.lower(), account_name)] = info
                self._id_entries.setdefault(product_id, []).append(info)

        logger.info(f"✅ Loaded {len(self.products)} products from CSV for user {self.telegram_user_id}")

        # Debug: показать первые 5 ID
        if self.products:
            sample_ids = list(self.products.keys())[:5]
            logger.info(f"   Sample product keys: {sample_ids}")

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

                    # Find candidates by ID to resolve collisions
                    candidates = self._id_entries.get(item_id, [])
                    if not candidates:
                        filtered_count += 1
                        continue

                    target_info = None
                    if len(candidates) == 1:
                        target_info = candidates[0]
                    else:
                        db_item_name = row.get('poster_item_name', '').strip().lower()
                        best_match = None
                        best_score = -1
                        for cand in candidates:
                            cand_name = cand['name'].lower()
                            if db_item_name == cand_name:
                                target_info = cand
                                break
                            score = fuzz.ratio(db_item_name, cand_name)
                            if score > best_score:
                                best_score = score
                                best_match = cand
                        if not target_info and best_match and best_score >= 80:
                            target_info = best_match
                        if not target_info:
                            for cand in candidates:
                                if cand.get('account_name') == 'Pizzburg':
                                    target_info = cand
                                    break
                            if not target_info:
                                target_info = candidates[0]

                    if target_info:
                        self.aliases.setdefault(alias, []).append((target_info['id'], target_info['account_name']))
                    else:
                        filtered_count += 1

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

                candidates = self._id_entries.get(item_id, [])
                for cand in candidates:
                    self.aliases.setdefault(alias, []).append((cand['id'], cand['account_name']))

        logger.info(f"Loaded {len(self.aliases)} product aliases from CSV for user {self.telegram_user_id}")

    def match(self, text: str, score_cutoff: int = MIN_MATCH_CONFIDENCE, target_account: Optional[str] = None) -> Optional[Tuple[int, str, str, int, str]]:
        """
        Match product by text (aliases, exact, or fuzzy)

        Args:
            text: Product text to match
            score_cutoff: Minimum fuzzy match score
            target_account: Optional target account name to prioritize

        Returns:
            Tuple of (product_id, name, unit, score, account_name) or None
            Score is 100 for exact/alias matches, lower for fuzzy
        """
        return self.match_with_priority(text, score_cutoff=score_cutoff, target_account=target_account)

    def match_with_priority(self, text: str, score_cutoff: int = MIN_MATCH_CONFIDENCE, primary_account: str = "Pizzburg", target_account: Optional[str] = None) -> Optional[Tuple[int, str, str, int, str]]:
        """
        Match product with priority: search in primary/target account first, then secondary accounts

        Logic: "Всё общее идет автоматически в Pizzburg, а то что нет в Pizzburg - поставляется в Pizzburg-cafe"

        Args:
            text: Product text to match
            score_cutoff: Minimum fuzzy match score
            primary_account: Name of primary account (default: "Pizzburg")
            target_account: Optional target account to prioritize above primary

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
            for product_id, account_name in self.aliases[text_lower]:
                product = self.products.get((product_id, account_name))
                if product:
                    all_matches.append((product_id, product['name'], product['unit'], 100, account_name))

        # 2. Check exact name matches
        if not all_matches and text_lower in self.names:
            for product_id, account_name in self.names[text_lower]:
                product = self.products.get((product_id, account_name))
                if product:
                    all_matches.append((product_id, product['name'], product['unit'], 100, account_name))

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
                text_words = set(text_lower.split())
                matched_words = set(matched_alias.split())
                common_tokens = text_words & matched_words
                
                # Stricter rejection for aliases 
                is_suspicious = (
                    score < 88 and 
                    len(common_tokens) < 1 and 
                    len(text_words) > 0 and 
                    len(matched_words) > 0
                )
                
                is_generic_overlap = (
                    score >= 80 and 
                    len(common_tokens) == 1 and 
                    len(text_words) > 1 and 
                    len(matched_words) > 1 and
                    len(list(common_tokens)[0]) < 4
                )

                if is_suspicious or is_generic_overlap:
                    logger.info(f"      ❌ Rejected alias priority match: '{text_lower}' → '{matched_alias}' (score={score:.1f})")
                    continue

                for product_id, account_name in self.aliases[matched_alias]:
                    product = self.products.get((product_id, account_name))
                    if not product:
                        continue
                    all_matches.append((product_id, product['name'], product['unit'], score, account_name))

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
                for product_id, account_name in self.names[matched_name]:
                    product = self.products.get((product_id, account_name))
                    if not product:
                        continue
                    all_matches.append((product['id'], product['name'], product['unit'], score, account_name))

        if not all_matches:
            logger.warning(f"Product not matched (priority search): '{text}'")
            return None

        # Priority logic: sort matches
        def sort_key(m):
            acc = m[4]
            is_target = (acc == target_account) if target_account else False
            is_primary = (acc == primary_account)
            return (is_target, is_primary, m[3])

        best_match = max(all_matches, key=sort_key)
        logger.info(f"✅ Found product match: '{text}' -> {best_match[1]} (score={best_match[3]}, account={best_match[4]})")
        return best_match

    def get_product_info(self, product_id: int, account_name: Optional[str] = None) -> Optional[Dict]:
        """Get product info by ID (and optional account name)"""
        if account_name:
            return self.products.get((product_id, account_name))
        
        candidates = self._id_entries.get(product_id, [])
        if not candidates:
            return None
        
        # Prefer Pizzburg if present
        for cand in candidates:
            if cand.get('account_name') == 'Pizzburg':
                return cand
        return candidates[0]

    def add_alias(self, alias_text: str, product_id: int, notes: str = "", account_name: Optional[str] = None):
        """
        Add new product alias and save to database (with CSV fallback)

        Args:
            alias_text: The alias text to add
            product_id: The product ID this alias maps to
            notes: Optional notes about this alias
            account_name: Optional account name this alias maps to
        """
        candidates = self._id_entries.get(product_id, [])
        if not candidates:
            logger.error(f"Cannot add alias: product {product_id} does not exist")
            return False

        target_candidates = [c for c in candidates if c['account_name'] == account_name] if account_name else candidates
        if not target_candidates:
            target_candidates = candidates

        alias_lower = normalize_text_for_matching(alias_text)

        # Add to memory for all matching candidates
        for cand in target_candidates:
            acc_name = cand['account_name']
            current_aliases = self.aliases.setdefault(alias_lower, [])
            if (product_id, acc_name) not in current_aliases:
                current_aliases.append((product_id, acc_name))

        # Save to database (only for the first/main candidate to avoid duplicates, but with correct name)
        target_cand = target_candidates[0]
        db_success = False
        if self.telegram_user_id:
            try:
                from database import get_database
                db = get_database()
                db_success = db.add_ingredient_alias(
                    telegram_user_id=self.telegram_user_id,
                    alias_text=alias_text,
                    poster_item_id=product_id,
                    poster_item_name=target_cand['name'],
                    source='product',
                    notes=notes
                )
            except Exception as e:
                logger.error(f"Failed to save product alias to database: {e}")

        # Fallback to CSV if not saved to DB
        if not db_success:
            try:
                # Append to CSV
                with open(self.aliases_csv, 'a', encoding='utf-8', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow([
                        self.telegram_user_id or "",
                        alias_text,
                        product_id,
                        target_cand['name'],
                        'product',
                        notes
                    ])
                logger.info(f"Added product alias to CSV: '{alias_text}' -> {product_id} ({target_cand['name']}) for user {self.telegram_user_id}")
                return True
            except Exception as e:
                logger.error(f"Failed to save product alias to CSV: {e}")
                return False
        return True

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
            for product_id, account_name in search_space[matched_text]:
                product = self.products.get((product_id, account_name))
                if not product:
                    continue
                display_name = product['name']
                if account_name != 'Pizzburg':
                    display_name = f"{display_name} ({account_name})"
                
                results.append((
                    product_id,
                    display_name,
                    product['unit'],
                    score
                ))

        # Remove duplicates (same product_id and display_name combo) keeping highest score
        seen = {}
        for prod_id, name, unit, score in results:
            combo_key = (prod_id, name)
            if combo_key not in seen or seen[combo_key][3] < score:
                seen[combo_key] = (prod_id, name, unit, score)

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
