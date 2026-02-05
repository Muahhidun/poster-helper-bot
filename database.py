"""Database management for multi-tenant bot - supports both SQLite and PostgreSQL"""
import os
import logging
from pathlib import Path
from typing import Optional, Dict
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Detect database type based on environment
DATABASE_URL = os.getenv("DATABASE_URL")  # Railway provides this for PostgreSQL

if DATABASE_URL:
    # PostgreSQL on Railway
    import psycopg2
    from psycopg2.extras import RealDictCursor
    DB_TYPE = "postgresql"
    logger.info("Using PostgreSQL database")
else:
    # SQLite locally
    import sqlite3
    from config import DATABASE_PATH
    DB_TYPE = "sqlite"
    logger.info(f"Using SQLite database at {DATABASE_PATH}")


class UserDatabase:
    """Database for managing user accounts"""

    def __init__(self):
        if DB_TYPE == "sqlite":
            from config import DATABASE_PATH
            self.db_path = DATABASE_PATH
        else:
            self.db_url = DATABASE_URL

        self._init_db()

    def _get_connection(self):
        """Get database connection based on type"""
        if DB_TYPE == "sqlite":
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            return conn
        else:
            # PostgreSQL
            return psycopg2.connect(self.db_url)

    def _init_db(self):
        """Initialize database with tables"""
        if DB_TYPE == "sqlite":
            # Create data directory if not exists
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

        conn = self._get_connection()
        cursor = conn.cursor()

        if DB_TYPE == "sqlite":
            # SQLite syntax
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    telegram_user_id INTEGER PRIMARY KEY,
                    poster_token TEXT NOT NULL,
                    poster_user_id TEXT NOT NULL,
                    poster_base_url TEXT NOT NULL,
                    subscription_status TEXT NOT NULL DEFAULT 'trial',
                    subscription_expires_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

            # Table for poster accounts (multi-account support)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS poster_accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_user_id INTEGER NOT NULL,
                    account_name TEXT NOT NULL,
                    poster_token TEXT NOT NULL,
                    poster_user_id TEXT NOT NULL,
                    poster_base_url TEXT NOT NULL,
                    is_primary INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(telegram_user_id, account_name),
                    FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id) ON DELETE CASCADE
                )
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_accounts_user
                ON poster_accounts(telegram_user_id)
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_settings (
                    telegram_user_id INTEGER PRIMARY KEY,
                    language TEXT DEFAULT 'ru',
                    timezone TEXT DEFAULT 'UTC+6',
                    notifications_enabled INTEGER DEFAULT 1,
                    FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id)
                )
            """)

            # Table for ingredient aliases (multi-tenant)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ingredient_aliases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_user_id INTEGER NOT NULL,
                    alias_text TEXT NOT NULL,
                    poster_item_id INTEGER NOT NULL,
                    poster_item_name TEXT NOT NULL,
                    source TEXT DEFAULT 'user',
                    notes TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(telegram_user_id, alias_text),
                    FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id) ON DELETE CASCADE
                )
            """)

            # Index for fast alias lookups
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_aliases_user_alias
                ON ingredient_aliases(telegram_user_id, alias_text)
            """)

            # Table for supplier aliases (ИП Федорова → Кока-Кола)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS supplier_aliases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_user_id INTEGER NOT NULL,
                    alias_text TEXT NOT NULL,
                    poster_supplier_id INTEGER NOT NULL,
                    poster_supplier_name TEXT NOT NULL,
                    notes TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(telegram_user_id, alias_text),
                    FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id) ON DELETE CASCADE
                )
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_supplier_aliases_user_alias
                ON supplier_aliases(telegram_user_id, alias_text)
            """)

            # Table for shipment templates (quick templates for recurring shipments)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS shipment_templates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_user_id INTEGER NOT NULL,
                    template_name TEXT NOT NULL,
                    supplier_id INTEGER NOT NULL,
                    supplier_name TEXT NOT NULL,
                    account_id INTEGER NOT NULL,
                    account_name TEXT NOT NULL,
                    storage_id INTEGER DEFAULT 1,
                    items TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(telegram_user_id, template_name),
                    FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id) ON DELETE CASCADE
                )
            """)

            # Index for fast template lookups
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_templates_user_name
                ON shipment_templates(telegram_user_id, template_name)
            """)

            # Table for ingredient price history (for smart price monitoring)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ingredient_price_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_user_id INTEGER NOT NULL,
                    ingredient_id INTEGER NOT NULL,
                    ingredient_name TEXT,
                    supplier_id INTEGER,
                    supplier_name TEXT,
                    date DATE NOT NULL,
                    price DECIMAL(10, 2) NOT NULL,
                    quantity DECIMAL(10, 3),
                    unit TEXT,
                    supply_id INTEGER,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id) ON DELETE CASCADE
                )
            """)

            # Indexes for fast lookups
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_price_history_ingredient_date
                ON ingredient_price_history(telegram_user_id, ingredient_id, date)
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_price_history_supplier
                ON ingredient_price_history(telegram_user_id, supplier_id)
            """)

            # Table for employees (for salary tracking with names)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS employees (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_user_id INTEGER NOT NULL,
                    employee_name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    last_mentioned_date TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(telegram_user_id, employee_name, role),
                    FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id) ON DELETE CASCADE
                )
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_employees_user_role
                ON employees(telegram_user_id, role)
            """)

            # Table for expense drafts (черновики расходов для веб-интерфейса)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS expense_drafts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_user_id INTEGER NOT NULL,
                    amount REAL NOT NULL,
                    description TEXT NOT NULL,
                    expense_type TEXT NOT NULL DEFAULT 'transaction',
                    category TEXT,
                    source TEXT NOT NULL DEFAULT 'cash',
                    source_account TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    quantity REAL,
                    unit TEXT,
                    price_per_unit REAL,
                    account_id INTEGER,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    processed_at TEXT,
                    FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id) ON DELETE CASCADE
                )
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_expense_drafts_user_status
                ON expense_drafts(telegram_user_id, status)
            """)

            # Migration: add account_id column if not exists
            try:
                cursor.execute("ALTER TABLE expense_drafts ADD COLUMN account_id INTEGER")
            except Exception:
                pass  # Column already exists

            # Migration: add poster_account_id column if not exists (for multi-account support: PizzBurg, PizzBurg Cafe)
            try:
                cursor.execute("ALTER TABLE expense_drafts ADD COLUMN poster_account_id INTEGER")
            except Exception:
                pass  # Column already exists

            # Migration: add completion_status column for tracking expense completion
            # Values: 'pending' (not done), 'partial' (in Poster but not paid), 'completed' (fully done)
            try:
                cursor.execute("ALTER TABLE expense_drafts ADD COLUMN completion_status TEXT DEFAULT 'pending'")
            except Exception:
                pass  # Column already exists

            # Migration: add poster_transaction_id column for linking drafts to Poster transactions
            try:
                cursor.execute("ALTER TABLE expense_drafts ADD COLUMN poster_transaction_id TEXT")
            except Exception:
                pass  # Column already exists

            # Migration: add is_income column for income transactions (доходы, например продажа масла)
            try:
                cursor.execute("ALTER TABLE expense_drafts ADD COLUMN is_income INTEGER DEFAULT 0")
            except Exception:
                pass  # Column already exists

            # Migration: add poster_amount column for tracking Poster's current amount
            # Used to detect mismatches when user edits amount on website vs Poster
            try:
                cursor.execute("ALTER TABLE expense_drafts ADD COLUMN poster_amount REAL")
            except Exception:
                pass  # Column already exists

            # Table for shift reconciliation (сверка смены по источникам: cash/kaspi/halyk)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS shift_reconciliation (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_user_id INTEGER NOT NULL,
                    date TEXT NOT NULL,
                    source TEXT NOT NULL,
                    opening_balance REAL,
                    closing_balance REAL,
                    total_difference REAL,
                    notes TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT,
                    UNIQUE(telegram_user_id, date, source),
                    FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id) ON DELETE CASCADE
                )
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_shift_reconciliation_user_date
                ON shift_reconciliation(telegram_user_id, date)
            """)

            # Table for supply drafts (черновики поставок)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS supply_drafts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_user_id INTEGER NOT NULL,
                    supplier_name TEXT,
                    invoice_date TEXT,
                    total_sum REAL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    linked_expense_draft_id INTEGER,
                    ocr_text TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    processed_at TEXT,
                    FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id) ON DELETE CASCADE,
                    FOREIGN KEY (linked_expense_draft_id) REFERENCES expense_drafts(id) ON DELETE SET NULL
                )
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_supply_drafts_user_status
                ON supply_drafts(telegram_user_id, status)
            """)

            # Table for supply draft items (позиции в черновике поставки)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS supply_draft_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    supply_draft_id INTEGER NOT NULL,
                    item_name TEXT NOT NULL,
                    quantity REAL NOT NULL DEFAULT 1,
                    unit TEXT DEFAULT 'шт',
                    price_per_unit REAL NOT NULL DEFAULT 0,
                    total REAL NOT NULL DEFAULT 0,
                    poster_ingredient_id INTEGER,
                    poster_ingredient_name TEXT,
                    FOREIGN KEY (supply_draft_id) REFERENCES supply_drafts(id) ON DELETE CASCADE
                )
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_supply_draft_items_draft
                ON supply_draft_items(supply_draft_id)
            """)
        else:
            # PostgreSQL syntax
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    telegram_user_id BIGINT PRIMARY KEY,
                    poster_token TEXT NOT NULL,
                    poster_user_id TEXT NOT NULL,
                    poster_base_url TEXT NOT NULL,
                    subscription_status TEXT NOT NULL DEFAULT 'trial',
                    subscription_expires_at TIMESTAMP,
                    created_at TIMESTAMP NOT NULL,
                    updated_at TIMESTAMP NOT NULL
                )
            """)

            # Table for poster accounts (multi-account support)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS poster_accounts (
                    id SERIAL PRIMARY KEY,
                    telegram_user_id BIGINT NOT NULL,
                    account_name TEXT NOT NULL,
                    poster_token TEXT NOT NULL,
                    poster_user_id TEXT NOT NULL,
                    poster_base_url TEXT NOT NULL,
                    is_primary BOOLEAN DEFAULT false,
                    created_at TIMESTAMP NOT NULL,
                    updated_at TIMESTAMP NOT NULL,
                    UNIQUE(telegram_user_id, account_name),
                    FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id) ON DELETE CASCADE
                )
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_accounts_user
                ON poster_accounts(telegram_user_id)
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_settings (
                    telegram_user_id BIGINT PRIMARY KEY,
                    language TEXT DEFAULT 'ru',
                    timezone TEXT DEFAULT 'UTC+6',
                    notifications_enabled INTEGER DEFAULT 1,
                    FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id)
                )
            """)

            # Table for ingredient aliases (multi-tenant)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ingredient_aliases (
                    id SERIAL PRIMARY KEY,
                    telegram_user_id BIGINT NOT NULL,
                    alias_text TEXT NOT NULL,
                    poster_item_id INTEGER NOT NULL,
                    poster_item_name TEXT NOT NULL,
                    source TEXT DEFAULT 'user',
                    notes TEXT,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(telegram_user_id, alias_text),
                    FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id) ON DELETE CASCADE
                )
            """)

            # Index for fast alias lookups
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_aliases_user_alias
                ON ingredient_aliases(telegram_user_id, alias_text)
            """)

            # Table for supplier aliases (ИП Федорова → Кока-Кола)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS supplier_aliases (
                    id SERIAL PRIMARY KEY,
                    telegram_user_id BIGINT NOT NULL,
                    alias_text TEXT NOT NULL,
                    poster_supplier_id INTEGER NOT NULL,
                    poster_supplier_name TEXT NOT NULL,
                    notes TEXT,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(telegram_user_id, alias_text),
                    FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id) ON DELETE CASCADE
                )
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_supplier_aliases_user_alias
                ON supplier_aliases(telegram_user_id, alias_text)
            """)

            # Table for shipment templates (quick templates for recurring shipments)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS shipment_templates (
                    id SERIAL PRIMARY KEY,
                    telegram_user_id BIGINT NOT NULL,
                    template_name TEXT NOT NULL,
                    supplier_id INTEGER NOT NULL,
                    supplier_name TEXT NOT NULL,
                    account_id INTEGER NOT NULL,
                    account_name TEXT NOT NULL,
                    storage_id INTEGER DEFAULT 1,
                    items TEXT NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(telegram_user_id, template_name),
                    FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id) ON DELETE CASCADE
                )
            """)

            # Index for fast template lookups
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_templates_user_name
                ON shipment_templates(telegram_user_id, template_name)
            """)

            # Table for ingredient price history (for smart price monitoring)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ingredient_price_history (
                    id SERIAL PRIMARY KEY,
                    telegram_user_id BIGINT NOT NULL,
                    ingredient_id INTEGER NOT NULL,
                    ingredient_name TEXT,
                    supplier_id INTEGER,
                    supplier_name TEXT,
                    date DATE NOT NULL,
                    price DECIMAL(10, 2) NOT NULL,
                    quantity DECIMAL(10, 3),
                    unit TEXT,
                    supply_id INTEGER,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id) ON DELETE CASCADE
                )
            """)

            # Indexes for fast lookups
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_price_history_ingredient_date
                ON ingredient_price_history(telegram_user_id, ingredient_id, date)
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_price_history_supplier
                ON ingredient_price_history(telegram_user_id, supplier_id)
            """)

            # Table for employees (for salary tracking with names)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS employees (
                    id SERIAL PRIMARY KEY,
                    telegram_user_id BIGINT NOT NULL,
                    employee_name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    last_mentioned_date DATE,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(telegram_user_id, employee_name, role),
                    FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id) ON DELETE CASCADE
                )
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_employees_user_role
                ON employees(telegram_user_id, role)
            """)

            # Table for expense drafts (черновики расходов для веб-интерфейса)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS expense_drafts (
                    id SERIAL PRIMARY KEY,
                    telegram_user_id BIGINT NOT NULL,
                    amount DECIMAL(12,2) NOT NULL,
                    description TEXT NOT NULL,
                    expense_type TEXT NOT NULL DEFAULT 'transaction',
                    category TEXT,
                    source TEXT NOT NULL DEFAULT 'cash',
                    source_account TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    quantity DECIMAL(10,3),
                    unit TEXT,
                    price_per_unit DECIMAL(12,2),
                    account_id INTEGER,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    processed_at TIMESTAMP,
                    FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id) ON DELETE CASCADE
                )
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_expense_drafts_user_status
                ON expense_drafts(telegram_user_id, status)
            """)

            # Migration: add account_id column if not exists
            try:
                cursor.execute("ALTER TABLE expense_drafts ADD COLUMN IF NOT EXISTS account_id INTEGER")
            except Exception:
                pass  # Column already exists

            # Migration: add poster_account_id column if not exists (for multi-account support: PizzBurg, PizzBurg Cafe)
            try:
                cursor.execute("ALTER TABLE expense_drafts ADD COLUMN IF NOT EXISTS poster_account_id INTEGER")
            except Exception:
                pass  # Column already exists

            # Migration: add completion_status column for tracking expense completion
            try:
                cursor.execute("ALTER TABLE expense_drafts ADD COLUMN IF NOT EXISTS completion_status TEXT DEFAULT 'pending'")
            except Exception:
                pass  # Column already exists

            # Migration: add poster_transaction_id column for linking drafts to Poster transactions
            try:
                cursor.execute("ALTER TABLE expense_drafts ADD COLUMN IF NOT EXISTS poster_transaction_id TEXT")
            except Exception:
                pass  # Column already exists

            # Migration: add is_income column for income transactions (доходы, например продажа масла)
            try:
                cursor.execute("ALTER TABLE expense_drafts ADD COLUMN IF NOT EXISTS is_income INTEGER DEFAULT 0")
            except Exception:
                pass  # Column already exists

            # Migration: add poster_amount column for tracking Poster's current amount
            # Used to detect mismatches when user edits amount on website vs Poster
            try:
                cursor.execute("ALTER TABLE expense_drafts ADD COLUMN IF NOT EXISTS poster_amount REAL")
            except Exception:
                pass  # Column already exists

            # Table for shift reconciliation (сверка смены по источникам: cash/kaspi/halyk)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS shift_reconciliation (
                    id SERIAL PRIMARY KEY,
                    telegram_user_id BIGINT NOT NULL,
                    date DATE NOT NULL,
                    source TEXT NOT NULL,
                    opening_balance REAL,
                    closing_balance REAL,
                    total_difference REAL,
                    notes TEXT,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP,
                    UNIQUE(telegram_user_id, date, source),
                    FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id) ON DELETE CASCADE
                )
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_shift_reconciliation_user_date
                ON shift_reconciliation(telegram_user_id, date)
            """)

            # Table for supply drafts (черновики поставок)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS supply_drafts (
                    id SERIAL PRIMARY KEY,
                    telegram_user_id BIGINT NOT NULL,
                    supplier_name TEXT,
                    invoice_date DATE,
                    total_sum DECIMAL(12,2),
                    status TEXT NOT NULL DEFAULT 'pending',
                    linked_expense_draft_id INTEGER,
                    ocr_text TEXT,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    processed_at TIMESTAMP,
                    FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id) ON DELETE CASCADE,
                    FOREIGN KEY (linked_expense_draft_id) REFERENCES expense_drafts(id) ON DELETE SET NULL
                )
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_supply_drafts_user_status
                ON supply_drafts(telegram_user_id, status)
            """)

            # Table for supply draft items (позиции в черновике поставки)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS supply_draft_items (
                    id SERIAL PRIMARY KEY,
                    supply_draft_id INTEGER NOT NULL,
                    item_name TEXT NOT NULL,
                    quantity DECIMAL(10,3) NOT NULL DEFAULT 1,
                    unit TEXT DEFAULT 'шт',
                    price_per_unit DECIMAL(12,2) NOT NULL DEFAULT 0,
                    total DECIMAL(12,2) NOT NULL DEFAULT 0,
                    poster_ingredient_id INTEGER,
                    poster_ingredient_name TEXT,
                    FOREIGN KEY (supply_draft_id) REFERENCES supply_drafts(id) ON DELETE CASCADE
                )
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_supply_draft_items_draft
                ON supply_draft_items(supply_draft_id)
            """)

        conn.commit()
        conn.close()

        if DB_TYPE == "sqlite":
            logger.info(f"✅ SQLite database initialized: {self.db_path}")
        else:
            logger.info(f"✅ PostgreSQL database initialized")

        # Run migration to multi-account structure
        self._migrate_to_multi_account()

        # Run migration to add poster_account_id to supply_draft_items
        self._migrate_supply_items_add_account()

    def _migrate_to_multi_account(self):
        """
        Migrate existing users from single-account to multi-account structure.
        This runs once to move poster credentials from users table to poster_accounts table.
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # Check if migration is needed (poster_accounts is empty)
            if DB_TYPE == "sqlite":
                cursor.execute("SELECT COUNT(*) as count FROM poster_accounts")
                count = cursor.fetchone()[0]
            else:
                cursor.execute("SELECT COUNT(*) as count FROM poster_accounts")
                count = cursor.fetchone()[0]

            if count > 0:
                # Migration already done
                conn.close()
                logger.info("✅ Multi-account migration: already completed")
                return

            # Get all users with poster credentials
            cursor.execute("""
                SELECT telegram_user_id, poster_token, poster_user_id, poster_base_url, created_at, updated_at
                FROM users
                WHERE poster_token IS NOT NULL AND poster_token != ''
            """)
            users = cursor.fetchall()

            migrated_count = 0
            for user in users:
                telegram_user_id = user[0]
                poster_token = user[1]
                poster_user_id = user[2]
                poster_base_url = user[3]
                created_at = user[4]
                updated_at = user[5]

                # Insert into poster_accounts as primary account
                if DB_TYPE == "sqlite":
                    cursor.execute("""
                        INSERT INTO poster_accounts (
                            telegram_user_id, account_name, poster_token, poster_user_id,
                            poster_base_url, is_primary, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        telegram_user_id, "Pizzburg", poster_token, poster_user_id,
                        poster_base_url, 1, created_at, updated_at
                    ))
                else:
                    cursor.execute("""
                        INSERT INTO poster_accounts (
                            telegram_user_id, account_name, poster_token, poster_user_id,
                            poster_base_url, is_primary, created_at, updated_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        telegram_user_id, "Pizzburg", poster_token, poster_user_id,
                        poster_base_url, True, created_at, updated_at
                    ))

                migrated_count += 1

            conn.commit()
            conn.close()

            if migrated_count > 0:
                logger.info(f"✅ Multi-account migration: moved {migrated_count} users to poster_accounts")
            else:
                logger.info("✅ Multi-account migration: no users to migrate")

        except Exception as e:
            logger.error(f"❌ Multi-account migration failed: {e}")
            # Don't crash the app if migration fails

    def _migrate_supply_items_add_account(self):
        """Add poster_account_id column to supply_draft_items table"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            if DB_TYPE == "sqlite":
                cursor.execute("ALTER TABLE supply_draft_items ADD COLUMN poster_account_id INTEGER")
            else:
                cursor.execute("ALTER TABLE supply_draft_items ADD COLUMN IF NOT EXISTS poster_account_id INTEGER")

            conn.commit()
            conn.close()
            logger.info("✅ Supply items migration: added poster_account_id column")
        except Exception as e:
            # Column probably already exists
            logger.info(f"✅ Supply items migration: poster_account_id column already exists or error: {e}")

        # Also add account_id and source to supply_drafts
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            if DB_TYPE == "sqlite":
                try:
                    cursor.execute("ALTER TABLE supply_drafts ADD COLUMN account_id INTEGER")
                except Exception:
                    pass
                try:
                    cursor.execute("ALTER TABLE supply_drafts ADD COLUMN source TEXT DEFAULT 'cash'")
                except Exception:
                    pass
                try:
                    cursor.execute("ALTER TABLE supply_drafts ADD COLUMN supplier_id INTEGER")
                except Exception:
                    pass
            else:
                cursor.execute("ALTER TABLE supply_drafts ADD COLUMN IF NOT EXISTS account_id INTEGER")
                cursor.execute("ALTER TABLE supply_drafts ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'cash'")
                cursor.execute("ALTER TABLE supply_drafts ADD COLUMN IF NOT EXISTS supplier_id INTEGER")

            conn.commit()
            conn.close()
            logger.info("✅ Supply drafts migration: added account_id and source columns")
        except Exception as e:
            logger.info(f"✅ Supply drafts migration: columns already exist or error: {e}")

        # Add item_type column to supply_draft_items (ingredient vs product)
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            if DB_TYPE == "sqlite":
                cursor.execute("ALTER TABLE supply_draft_items ADD COLUMN item_type TEXT DEFAULT 'ingredient'")
            else:
                cursor.execute("ALTER TABLE supply_draft_items ADD COLUMN IF NOT EXISTS item_type TEXT DEFAULT 'ingredient'")

            conn.commit()
            conn.close()
            logger.info("✅ Supply items migration: added item_type column")
        except Exception as e:
            logger.info(f"✅ Supply items migration: item_type column already exists or error: {e}")

        # Add storage_id and storage_name columns to supply_draft_items
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            if DB_TYPE == "sqlite":
                try:
                    cursor.execute("ALTER TABLE supply_draft_items ADD COLUMN storage_id INTEGER DEFAULT 1")
                except:
                    pass
                try:
                    cursor.execute("ALTER TABLE supply_draft_items ADD COLUMN storage_name TEXT")
                except:
                    pass
            else:
                cursor.execute("ALTER TABLE supply_draft_items ADD COLUMN IF NOT EXISTS storage_id INTEGER DEFAULT 1")
                cursor.execute("ALTER TABLE supply_draft_items ADD COLUMN IF NOT EXISTS storage_name TEXT")

            conn.commit()
            conn.close()
            logger.info("✅ Supply items migration: added storage_id and storage_name columns")
        except Exception as e:
            logger.info(f"✅ Supply items migration: storage columns already exist or error: {e}")

        # Add poster_account_name column to supply_draft_items
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            if DB_TYPE == "sqlite":
                try:
                    cursor.execute("ALTER TABLE supply_draft_items ADD COLUMN poster_account_name TEXT")
                except:
                    pass
            else:
                cursor.execute("ALTER TABLE supply_draft_items ADD COLUMN IF NOT EXISTS poster_account_name TEXT")

            conn.commit()
            conn.close()
            logger.info("✅ Supply items migration: added poster_account_name column")
        except Exception as e:
            logger.info(f"✅ Supply items migration: poster_account_name column already exists or error: {e}")

    def get_user(self, telegram_user_id: int) -> Optional[Dict]:
        """Get user by Telegram ID"""
        conn = self._get_connection()

        if DB_TYPE == "sqlite":
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM users WHERE telegram_user_id = ?
            """, (telegram_user_id,))
            row = cursor.fetchone()
        else:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                SELECT * FROM users WHERE telegram_user_id = %s
            """, (telegram_user_id,))
            row = cursor.fetchone()

        conn.close()

        if row:
            return dict(row)
        return None

    def create_user(
        self,
        telegram_user_id: int,
        poster_token: str,
        poster_user_id: str,
        poster_base_url: str = None
    ) -> bool:
        """Create new user"""
        try:
            # Use config default if poster_base_url not provided
            if poster_base_url is None:
                from config import POSTER_BASE_URL
                poster_base_url = POSTER_BASE_URL

            conn = self._get_connection()
            cursor = conn.cursor()

            now = datetime.now()
            trial_expires = now + timedelta(days=14)

            if DB_TYPE == "sqlite":
                cursor.execute("""
                    INSERT INTO users (
                        telegram_user_id,
                        poster_token,
                        poster_user_id,
                        poster_base_url,
                        subscription_status,
                        subscription_expires_at,
                        created_at,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    telegram_user_id,
                    poster_token,
                    poster_user_id,
                    poster_base_url,
                    'trial',
                    trial_expires.isoformat(),
                    now.isoformat(),
                    now.isoformat()
                ))
            else:
                cursor.execute("""
                    INSERT INTO users (
                        telegram_user_id,
                        poster_token,
                        poster_user_id,
                        poster_base_url,
                        subscription_status,
                        subscription_expires_at,
                        created_at,
                        updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    telegram_user_id,
                    poster_token,
                    poster_user_id,
                    poster_base_url,
                    'trial',
                    trial_expires,
                    now,
                    now
                ))

            conn.commit()
            conn.close()

            logger.info(f"✅ User created: telegram_id={telegram_user_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to create user: {e}")
            return False

    def update_user(
        self,
        telegram_user_id: int,
        poster_token: Optional[str] = None,
        poster_user_id: Optional[str] = None,
        poster_base_url: Optional[str] = None,
        subscription_status: Optional[str] = None
    ) -> bool:
        """Update user info"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            updates = []
            params = []

            if poster_token:
                updates.append("poster_token = ?")
                params.append(poster_token)
            if poster_user_id:
                updates.append("poster_user_id = ?")
                params.append(poster_user_id)
            if poster_base_url:
                updates.append("poster_base_url = ?")
                params.append(poster_base_url)
            if subscription_status:
                updates.append("subscription_status = ?")
                params.append(subscription_status)

            if not updates:
                return False

            updates.append("updated_at = ?")

            if DB_TYPE == "sqlite":
                params.append(datetime.now().isoformat())
                params.append(telegram_user_id)
                query = f"UPDATE users SET {', '.join(updates)} WHERE telegram_user_id = ?"
                cursor.execute(query, params)
            else:
                # For PostgreSQL, replace ? with %s
                updates_pg = [u.replace("?", "%s") for u in updates]
                params.append(datetime.now())
                params.append(telegram_user_id)
                query = f"UPDATE users SET {', '.join(updates_pg)} WHERE telegram_user_id = %s"
                cursor.execute(query, params)

            conn.commit()
            conn.close()

            logger.info(f"✅ User updated: telegram_id={telegram_user_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to update user: {e}")
            return False

    def delete_user(self, telegram_user_id: int) -> bool:
        """Delete user (for testing or user request)"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            if DB_TYPE == "sqlite":
                cursor.execute("DELETE FROM users WHERE telegram_user_id = ?", (telegram_user_id,))
            else:
                cursor.execute("DELETE FROM users WHERE telegram_user_id = %s", (telegram_user_id,))

            conn.commit()
            conn.close()

            logger.info(f"✅ User deleted: telegram_id={telegram_user_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to delete user: {e}")
            return False

    def is_subscription_active(self, telegram_user_id: int) -> bool:
        """Check if user has active subscription"""
        user = self.get_user(telegram_user_id)
        if not user:
            return False

        # Check subscription status
        if user['subscription_status'] == 'expired':
            return False

        # Check expiration date
        if user['subscription_expires_at']:
            if DB_TYPE == "sqlite":
                expires_at = datetime.fromisoformat(user['subscription_expires_at'])
            else:
                expires_at = user['subscription_expires_at']

            if datetime.now() > expires_at:
                # Update status to expired
                self.update_user(telegram_user_id, subscription_status='expired')
                return False

        return True

    # === Poster Accounts Methods ===

    def get_accounts(self, telegram_user_id: int) -> list:
        """Get all Poster accounts for a user"""
        conn = self._get_connection()

        if DB_TYPE == "sqlite":
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, account_name, poster_token, poster_user_id, poster_base_url,
                       is_primary, created_at, updated_at
                FROM poster_accounts
                WHERE telegram_user_id = ?
                ORDER BY is_primary DESC, account_name
            """, (telegram_user_id,))
            rows = cursor.fetchall()
        else:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                SELECT id, account_name, poster_token, poster_user_id, poster_base_url,
                       is_primary, created_at, updated_at
                FROM poster_accounts
                WHERE telegram_user_id = %s
                ORDER BY is_primary DESC, account_name
            """, (telegram_user_id,))
            rows = cursor.fetchall()

        conn.close()
        return [dict(row) for row in rows]

    def get_primary_account(self, telegram_user_id: int) -> Optional[Dict]:
        """Get primary Poster account for a user"""
        conn = self._get_connection()

        if DB_TYPE == "sqlite":
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, account_name, poster_token, poster_user_id, poster_base_url,
                       is_primary, created_at, updated_at
                FROM poster_accounts
                WHERE telegram_user_id = ? AND is_primary = 1
                LIMIT 1
            """, (telegram_user_id,))
            row = cursor.fetchone()
        else:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                SELECT id, account_name, poster_token, poster_user_id, poster_base_url,
                       is_primary, created_at, updated_at
                FROM poster_accounts
                WHERE telegram_user_id = %s AND is_primary = true
                LIMIT 1
            """, (telegram_user_id,))
            row = cursor.fetchone()

        conn.close()

        if row:
            return dict(row)
        return None

    def get_account_by_name(self, telegram_user_id: int, account_name: str) -> Optional[Dict]:
        """Get Poster account by name"""
        conn = self._get_connection()

        if DB_TYPE == "sqlite":
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, account_name, poster_token, poster_user_id, poster_base_url,
                       is_primary, created_at, updated_at
                FROM poster_accounts
                WHERE telegram_user_id = ? AND account_name = ?
                LIMIT 1
            """, (telegram_user_id, account_name))
            row = cursor.fetchone()
        else:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                SELECT id, account_name, poster_token, poster_user_id, poster_base_url,
                       is_primary, created_at, updated_at
                FROM poster_accounts
                WHERE telegram_user_id = %s AND account_name = %s
                LIMIT 1
            """, (telegram_user_id, account_name))
            row = cursor.fetchone()

        conn.close()

        if row:
            return dict(row)
        return None

    def add_account(
        self,
        telegram_user_id: int,
        account_name: str,
        poster_token: str,
        poster_user_id: str,
        poster_base_url: str,
        is_primary: bool = False
    ) -> bool:
        """Add a new Poster account for a user"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            now = datetime.now()

            if DB_TYPE == "sqlite":
                cursor.execute("""
                    INSERT INTO poster_accounts (
                        telegram_user_id, account_name, poster_token, poster_user_id,
                        poster_base_url, is_primary, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    telegram_user_id, account_name, poster_token, poster_user_id,
                    poster_base_url, 1 if is_primary else 0, now.isoformat(), now.isoformat()
                ))
            else:
                cursor.execute("""
                    INSERT INTO poster_accounts (
                        telegram_user_id, account_name, poster_token, poster_user_id,
                        poster_base_url, is_primary, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    telegram_user_id, account_name, poster_token, poster_user_id,
                    poster_base_url, is_primary, now, now
                ))

            conn.commit()
            conn.close()

            logger.info(f"✅ Poster account added: {account_name} for telegram_id={telegram_user_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to add poster account: {e}")
            return False

    # === Ingredient Aliases Methods ===

    def get_ingredient_aliases(self, telegram_user_id: int) -> list:
        """Get all ingredient aliases for a user"""
        conn = self._get_connection()

        if DB_TYPE == "sqlite":
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, alias_text, poster_item_id, poster_item_name, source, notes
                FROM ingredient_aliases
                WHERE telegram_user_id = ?
                ORDER BY alias_text
            """, (telegram_user_id,))
            rows = cursor.fetchall()
        else:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                SELECT id, alias_text, poster_item_id, poster_item_name, source, notes
                FROM ingredient_aliases
                WHERE telegram_user_id = %s
                ORDER BY alias_text
            """, (telegram_user_id,))
            rows = cursor.fetchall()

        conn.close()

        return [dict(row) for row in rows]

    def add_ingredient_alias(
        self,
        telegram_user_id: int,
        alias_text: str,
        poster_item_id: int,
        poster_item_name: str,
        source: str = "user",
        notes: str = ""
    ) -> bool:
        """Add or update an ingredient alias"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            if DB_TYPE == "sqlite":
                # SQLite: INSERT OR REPLACE
                cursor.execute("""
                    INSERT OR REPLACE INTO ingredient_aliases (
                        telegram_user_id, alias_text, poster_item_id,
                        poster_item_name, source, notes, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
                """, (
                    telegram_user_id,
                    alias_text.strip().lower(),
                    poster_item_id,
                    poster_item_name,
                    source,
                    notes
                ))
            else:
                # PostgreSQL: ON CONFLICT UPDATE
                cursor.execute("""
                    INSERT INTO ingredient_aliases (
                        telegram_user_id, alias_text, poster_item_id,
                        poster_item_name, source, notes
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (telegram_user_id, alias_text)
                    DO UPDATE SET
                        poster_item_id = EXCLUDED.poster_item_id,
                        poster_item_name = EXCLUDED.poster_item_name,
                        source = EXCLUDED.source,
                        notes = EXCLUDED.notes
                """, (
                    telegram_user_id,
                    alias_text.strip().lower(),
                    poster_item_id,
                    poster_item_name,
                    source,
                    notes
                ))

            conn.commit()
            conn.close()

            logger.info(f"✅ Alias added: '{alias_text}' -> {poster_item_name} (ID={poster_item_id})")
            return True

        except Exception as e:
            logger.error(f"Failed to add alias: {e}")
            return False

    def delete_ingredient_alias(self, telegram_user_id: int, alias_text: str) -> bool:
        """Delete an ingredient alias"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            if DB_TYPE == "sqlite":
                cursor.execute("""
                    DELETE FROM ingredient_aliases
                    WHERE telegram_user_id = ? AND alias_text = ?
                """, (telegram_user_id, alias_text.strip().lower()))
            else:
                cursor.execute("""
                    DELETE FROM ingredient_aliases
                    WHERE telegram_user_id = %s AND alias_text = %s
                """, (telegram_user_id, alias_text.strip().lower()))

            conn.commit()
            conn.close()

            logger.info(f"✅ Alias deleted: '{alias_text}'")
            return True

        except Exception as e:
            logger.error(f"Failed to delete alias: {e}")
            return False

    def bulk_add_aliases(self, telegram_user_id: int, aliases: list) -> int:
        """
        Bulk add multiple aliases

        Args:
            telegram_user_id: User ID
            aliases: List of dicts with keys: alias_text, poster_item_id, poster_item_name, source, notes

        Returns:
            Number of aliases added
        """
        count = 0
        for alias in aliases:
            if self.add_ingredient_alias(
                telegram_user_id=telegram_user_id,
                alias_text=alias['alias_text'],
                poster_item_id=alias['poster_item_id'],
                poster_item_name=alias['poster_item_name'],
                source=alias.get('source', 'user'),
                notes=alias.get('notes', '')
            ):
                count += 1

        logger.info(f"✅ Bulk import: {count}/{len(aliases)} aliases added")
        return count

    def get_alias_by_id(self, alias_id: int, telegram_user_id: int) -> Optional[Dict]:
        """Get a single alias by ID"""
        conn = self._get_connection()

        if DB_TYPE == "sqlite":
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, alias_text, poster_item_id, poster_item_name, source, notes
                FROM ingredient_aliases
                WHERE id = ? AND telegram_user_id = ?
            """, (alias_id, telegram_user_id))
            row = cursor.fetchone()
        else:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                SELECT id, alias_text, poster_item_id, poster_item_name, source, notes
                FROM ingredient_aliases
                WHERE id = %s AND telegram_user_id = %s
            """, (alias_id, telegram_user_id))
            row = cursor.fetchone()

        conn.close()

        if row:
            return dict(row)
        return None

    def update_alias(
        self,
        alias_id: int,
        telegram_user_id: int,
        alias_text: str,
        poster_item_id: int,
        poster_item_name: str,
        source: str = "user",
        notes: str = ""
    ) -> bool:
        """Update an existing alias"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            if DB_TYPE == "sqlite":
                cursor.execute("""
                    UPDATE ingredient_aliases
                    SET alias_text = ?,
                        poster_item_id = ?,
                        poster_item_name = ?,
                        source = ?,
                        notes = ?
                    WHERE id = ? AND telegram_user_id = ?
                """, (
                    alias_text.strip().lower(),
                    poster_item_id,
                    poster_item_name,
                    source,
                    notes,
                    alias_id,
                    telegram_user_id
                ))
            else:
                cursor.execute("""
                    UPDATE ingredient_aliases
                    SET alias_text = %s,
                        poster_item_id = %s,
                        poster_item_name = %s,
                        source = %s,
                        notes = %s
                    WHERE id = %s AND telegram_user_id = %s
                """, (
                    alias_text.strip().lower(),
                    poster_item_id,
                    poster_item_name,
                    source,
                    notes,
                    alias_id,
                    telegram_user_id
                ))

            conn.commit()
            conn.close()

            logger.info(f"✅ Alias updated: ID={alias_id}, '{alias_text}' -> {poster_item_name}")
            return True

        except Exception as e:
            logger.error(f"Failed to update alias: {e}")
            return False

    def delete_alias_by_id(self, alias_id: int, telegram_user_id: int) -> bool:
        """Delete an alias by ID"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            if DB_TYPE == "sqlite":
                cursor.execute("""
                    DELETE FROM ingredient_aliases
                    WHERE id = ? AND telegram_user_id = ?
                """, (alias_id, telegram_user_id))
            else:
                cursor.execute("""
                    DELETE FROM ingredient_aliases
                    WHERE id = %s AND telegram_user_id = %s
                """, (alias_id, telegram_user_id))

            conn.commit()
            conn.close()

            logger.info(f"✅ Alias deleted: ID={alias_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to delete alias: {e}")
            return False

    def clean_orphaned_ingredient_aliases(self, telegram_user_id: int, valid_ingredient_ids: list) -> int:
        """
        Delete aliases that reference ingredient IDs that no longer exist

        Args:
            telegram_user_id: User ID
            valid_ingredient_ids: List of ingredient IDs that currently exist in Poster

        Returns:
            Number of deleted aliases
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # Fetch all aliases for this user
            if DB_TYPE == "sqlite":
                cursor.execute("""
                    SELECT id, alias_text, poster_item_id
                    FROM ingredient_aliases
                    WHERE telegram_user_id = ?
                """, (telegram_user_id,))
            else:
                cursor.execute("""
                    SELECT id, alias_text, poster_item_id
                    FROM ingredient_aliases
                    WHERE telegram_user_id = %s
                """, (telegram_user_id,))

            all_aliases = cursor.fetchall()

            # Find orphaned aliases
            orphaned_ids = []
            for alias in all_aliases:
                if DB_TYPE == "sqlite":
                    alias_id = alias['id']
                    poster_item_id = alias['poster_item_id']
                    alias_text = alias['alias_text']
                else:
                    alias_id = alias[0]
                    poster_item_id = alias[2]
                    alias_text = alias[1]

                if poster_item_id not in valid_ingredient_ids:
                    orphaned_ids.append(alias_id)
                    logger.info(f"  Orphaned alias: '{alias_text}' -> ingredient_id {poster_item_id} (deleted)")

            # Delete orphaned aliases
            if orphaned_ids:
                if DB_TYPE == "sqlite":
                    placeholders = ','.join('?' * len(orphaned_ids))
                    cursor.execute(f"""
                        DELETE FROM ingredient_aliases
                        WHERE id IN ({placeholders}) AND telegram_user_id = ?
                    """, orphaned_ids + [telegram_user_id])
                else:
                    placeholders = ','.join(['%s'] * len(orphaned_ids))
                    cursor.execute(f"""
                        DELETE FROM ingredient_aliases
                        WHERE id IN ({placeholders}) AND telegram_user_id = %s
                    """, orphaned_ids + [telegram_user_id])

            conn.commit()
            conn.close()

            if orphaned_ids:
                logger.info(f"✅ Cleaned {len(orphaned_ids)} orphaned ingredient aliases for user {telegram_user_id}")

            return len(orphaned_ids)

        except Exception as e:
            logger.error(f"Failed to clean orphaned aliases: {e}")
            return 0

    # === Supplier Aliases Methods ===

    def get_supplier_aliases(self, telegram_user_id: int) -> list:
        """Get all supplier aliases for a user"""
        conn = self._get_connection()

        if DB_TYPE == "sqlite":
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, alias_text, poster_supplier_id, poster_supplier_name, notes, created_at
                FROM supplier_aliases
                WHERE telegram_user_id = ?
                ORDER BY alias_text
            """, (telegram_user_id,))
            rows = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description]
            rows = [dict(zip(columns, row)) for row in rows]
        else:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                SELECT id, alias_text, poster_supplier_id, poster_supplier_name, notes, created_at
                FROM supplier_aliases
                WHERE telegram_user_id = %s
                ORDER BY alias_text
            """, (telegram_user_id,))
            rows = cursor.fetchall()

        conn.close()
        return [dict(row) for row in rows]

    def add_supplier_alias(
        self,
        telegram_user_id: int,
        alias_text: str,
        poster_supplier_id: int,
        poster_supplier_name: str,
        notes: str = ""
    ) -> bool:
        """Add or update a supplier alias"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            if DB_TYPE == "sqlite":
                cursor.execute("""
                    INSERT OR REPLACE INTO supplier_aliases (
                        telegram_user_id, alias_text, poster_supplier_id,
                        poster_supplier_name, notes, created_at
                    ) VALUES (?, ?, ?, ?, ?, datetime('now'))
                """, (
                    telegram_user_id,
                    alias_text.strip().lower(),
                    poster_supplier_id,
                    poster_supplier_name,
                    notes
                ))
            else:
                cursor.execute("""
                    INSERT INTO supplier_aliases (
                        telegram_user_id, alias_text, poster_supplier_id,
                        poster_supplier_name, notes
                    ) VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (telegram_user_id, alias_text)
                    DO UPDATE SET
                        poster_supplier_id = EXCLUDED.poster_supplier_id,
                        poster_supplier_name = EXCLUDED.poster_supplier_name,
                        notes = EXCLUDED.notes
                """, (
                    telegram_user_id,
                    alias_text.strip().lower(),
                    poster_supplier_id,
                    poster_supplier_name,
                    notes
                ))

            conn.commit()
            conn.close()

            logger.info(f"✅ Supplier alias added: '{alias_text}' -> {poster_supplier_name}")
            return True

        except Exception as e:
            logger.error(f"Failed to add supplier alias: {e}")
            return False

    def get_supplier_by_alias(self, telegram_user_id: int, alias_text: str) -> Optional[Dict]:
        """Find supplier by alias text (for Kaspi parsing)"""
        conn = self._get_connection()

        # Normalize alias
        alias_normalized = alias_text.strip().lower()

        if DB_TYPE == "sqlite":
            cursor = conn.cursor()
            # Exact match first
            cursor.execute("""
                SELECT poster_supplier_id, poster_supplier_name
                FROM supplier_aliases
                WHERE telegram_user_id = ? AND alias_text = ?
            """, (telegram_user_id, alias_normalized))
            row = cursor.fetchone()

            if not row:
                # Partial match (alias contains in text or text contains alias)
                cursor.execute("""
                    SELECT poster_supplier_id, poster_supplier_name, alias_text
                    FROM supplier_aliases
                    WHERE telegram_user_id = ?
                """, (telegram_user_id,))
                all_aliases = cursor.fetchall()

                for alias_row in all_aliases:
                    stored_alias = alias_row[2]
                    if stored_alias in alias_normalized or alias_normalized in stored_alias:
                        row = alias_row[:2]
                        break
        else:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                SELECT poster_supplier_id, poster_supplier_name
                FROM supplier_aliases
                WHERE telegram_user_id = %s AND alias_text = %s
            """, (telegram_user_id, alias_normalized))
            row = cursor.fetchone()

            if not row:
                cursor.execute("""
                    SELECT poster_supplier_id, poster_supplier_name, alias_text
                    FROM supplier_aliases
                    WHERE telegram_user_id = %s
                """, (telegram_user_id,))
                all_aliases = cursor.fetchall()

                for alias_row in all_aliases:
                    stored_alias = alias_row['alias_text']
                    if stored_alias in alias_normalized or alias_normalized in stored_alias:
                        row = alias_row
                        break

        conn.close()

        if row:
            if DB_TYPE == "sqlite":
                return {'poster_supplier_id': row[0], 'poster_supplier_name': row[1]}
            else:
                return dict(row)
        return None

    def delete_supplier_alias(self, telegram_user_id: int, alias_id: int) -> bool:
        """Delete a supplier alias by ID"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            if DB_TYPE == "sqlite":
                cursor.execute("""
                    DELETE FROM supplier_aliases
                    WHERE id = ? AND telegram_user_id = ?
                """, (alias_id, telegram_user_id))
            else:
                cursor.execute("""
                    DELETE FROM supplier_aliases
                    WHERE id = %s AND telegram_user_id = %s
                """, (alias_id, telegram_user_id))

            conn.commit()
            conn.close()

            logger.info(f"✅ Supplier alias deleted: ID={alias_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to delete supplier alias: {e}")
            return False

    # === Price History Methods ===

    def add_price_history(
        self,
        telegram_user_id: int,
        ingredient_id: int,
        ingredient_name: str,
        supplier_id: int,
        supplier_name: str,
        date: str,
        price: float,
        quantity: float,
        unit: str,
        supply_id: int = None
    ) -> bool:
        """
        Add ingredient price record to history

        Args:
            telegram_user_id: User ID
            ingredient_id: Poster ingredient ID
            ingredient_name: Ingredient name
            supplier_id: Poster supplier ID
            supplier_name: Supplier name
            date: Date in format "YYYY-MM-DD"
            price: Price per unit
            quantity: Quantity purchased
            unit: Unit of measurement (кг, л, шт)
            supply_id: Poster supply ID

        Returns:
            True if successful
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            if DB_TYPE == "sqlite":
                cursor.execute("""
                    INSERT INTO ingredient_price_history (
                        telegram_user_id, ingredient_id, ingredient_name,
                        supplier_id, supplier_name, date, price,
                        quantity, unit, supply_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    telegram_user_id, ingredient_id, ingredient_name,
                    supplier_id, supplier_name, date, price,
                    quantity, unit, supply_id
                ))
            else:
                cursor.execute("""
                    INSERT INTO ingredient_price_history (
                        telegram_user_id, ingredient_id, ingredient_name,
                        supplier_id, supplier_name, date, price,
                        quantity, unit, supply_id
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    telegram_user_id, ingredient_id, ingredient_name,
                    supplier_id, supplier_name, date, price,
                    quantity, unit, supply_id
                ))

            conn.commit()
            conn.close()

            logger.debug(f"✅ Price history added: {ingredient_name} - {price}₸")
            return True

        except Exception as e:
            logger.error(f"Failed to add price history: {e}")
            return False

    def get_price_history(
        self,
        telegram_user_id: int,
        ingredient_id: int = None,
        supplier_id: int = None,
        date_from: str = None,
        date_to: str = None
    ) -> list:
        """
        Get price history with optional filters

        Args:
            telegram_user_id: User ID
            ingredient_id: Optional ingredient ID filter
            supplier_id: Optional supplier ID filter
            date_from: Optional start date "YYYY-MM-DD"
            date_to: Optional end date "YYYY-MM-DD"

        Returns:
            List of price history records
        """
        conn = self._get_connection()

        query = """
            SELECT id, ingredient_id, ingredient_name, supplier_id, supplier_name,
                   date, price, quantity, unit, supply_id, created_at
            FROM ingredient_price_history
            WHERE telegram_user_id = {}
        """.format('?' if DB_TYPE == 'sqlite' else '%s')

        params = [telegram_user_id]

        if ingredient_id:
            query += f" AND ingredient_id = {'?' if DB_TYPE == 'sqlite' else '%s'}"
            params.append(ingredient_id)

        if supplier_id:
            query += f" AND supplier_id = {'?' if DB_TYPE == 'sqlite' else '%s'}"
            params.append(supplier_id)

        if date_from:
            query += f" AND date >= {'?' if DB_TYPE == 'sqlite' else '%s'}"
            params.append(date_from)

        if date_to:
            query += f" AND date <= {'?' if DB_TYPE == 'sqlite' else '%s'}"
            params.append(date_to)

        query += " ORDER BY date DESC"

        if DB_TYPE == "sqlite":
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows = cursor.fetchall()
        else:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute(query, params)
            rows = cursor.fetchall()

        conn.close()

        return [dict(row) for row in rows]

    def bulk_add_price_history(self, telegram_user_id: int, records: list) -> int:
        """
        Bulk add multiple price history records

        Args:
            telegram_user_id: User ID
            records: List of dicts with keys: ingredient_id, ingredient_name,
                    supplier_id, supplier_name, date, price, quantity, unit, supply_id

        Returns:
            Number of records added
        """
        count = 0
        for record in records:
            if self.add_price_history(
                telegram_user_id=telegram_user_id,
                ingredient_id=record['ingredient_id'],
                ingredient_name=record['ingredient_name'],
                supplier_id=record['supplier_id'],
                supplier_name=record['supplier_name'],
                date=record['date'],
                price=record['price'],
                quantity=record['quantity'],
                unit=record['unit'],
                supply_id=record.get('supply_id')
            ):
                count += 1

        logger.info(f"✅ Bulk import: {count}/{len(records)} price history records added")
        return count

    # === Shipment Templates Methods ===

    def get_shipment_templates(self, telegram_user_id: int) -> list:
        """Get all shipment templates for a user"""
        conn = self._get_connection()

        if DB_TYPE == "sqlite":
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, template_name, supplier_id, supplier_name,
                       account_id, account_name, storage_id, items
                FROM shipment_templates
                WHERE telegram_user_id = ?
                ORDER BY template_name
            """, (telegram_user_id,))
            rows = cursor.fetchall()
        else:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                SELECT id, template_name, supplier_id, supplier_name,
                       account_id, account_name, storage_id, items
                FROM shipment_templates
                WHERE telegram_user_id = %s
                ORDER BY template_name
            """, (telegram_user_id,))
            rows = cursor.fetchall()

        conn.close()

        import json
        templates = []
        for row in rows:
            template = dict(row)
            # Parse items JSON
            template['items'] = json.loads(template['items'])
            templates.append(template)

        return templates

    def get_shipment_template(self, telegram_user_id: int, template_name: str) -> Optional[Dict]:
        """Get a single shipment template by name"""
        conn = self._get_connection()

        if DB_TYPE == "sqlite":
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, template_name, supplier_id, supplier_name,
                       account_id, account_name, storage_id, items
                FROM shipment_templates
                WHERE telegram_user_id = ? AND template_name = ?
            """, (telegram_user_id, template_name.strip().lower()))
            row = cursor.fetchone()
        else:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                SELECT id, template_name, supplier_id, supplier_name,
                       account_id, account_name, storage_id, items
                FROM shipment_templates
                WHERE telegram_user_id = %s AND template_name = %s
            """, (telegram_user_id, template_name.strip().lower()))
            row = cursor.fetchone()

        conn.close()

        if row:
            import json
            template = dict(row)
            # Parse items JSON
            template['items'] = json.loads(template['items'])
            return template
        return None

    def create_shipment_template(
        self,
        telegram_user_id: int,
        template_name: str,
        supplier_id: int,
        supplier_name: str,
        account_id: int,
        account_name: str,
        items: list,
        storage_id: int = 1
    ) -> bool:
        """Create a new shipment template"""
        try:
            import json
            conn = self._get_connection()
            cursor = conn.cursor()

            items_json = json.dumps(items, ensure_ascii=False)

            if DB_TYPE == "sqlite":
                cursor.execute("""
                    INSERT INTO shipment_templates (
                        telegram_user_id, template_name, supplier_id, supplier_name,
                        account_id, account_name, storage_id, items
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    telegram_user_id,
                    template_name.strip().lower(),
                    supplier_id,
                    supplier_name,
                    account_id,
                    account_name,
                    storage_id,
                    items_json
                ))
            else:
                cursor.execute("""
                    INSERT INTO shipment_templates (
                        telegram_user_id, template_name, supplier_id, supplier_name,
                        account_id, account_name, storage_id, items
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    telegram_user_id,
                    template_name.strip().lower(),
                    supplier_id,
                    supplier_name,
                    account_id,
                    account_name,
                    storage_id,
                    items_json
                ))

            conn.commit()
            conn.close()

            logger.info(f"✅ Shipment template created: '{template_name}' for user {telegram_user_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to create shipment template: {e}")
            return False

    def update_shipment_template(
        self,
        telegram_user_id: int,
        template_name: str,
        supplier_id: int = None,
        supplier_name: str = None,
        account_id: int = None,
        account_name: str = None,
        items: list = None,
        storage_id: int = None
    ) -> bool:
        """Update an existing shipment template"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            updates = []
            params = []

            if supplier_id is not None:
                updates.append("supplier_id = ?")
                params.append(supplier_id)
            if supplier_name is not None:
                updates.append("supplier_name = ?")
                params.append(supplier_name)
            if account_id is not None:
                updates.append("account_id = ?")
                params.append(account_id)
            if account_name is not None:
                updates.append("account_name = ?")
                params.append(account_name)
            if storage_id is not None:
                updates.append("storage_id = ?")
                params.append(storage_id)
            if items is not None:
                import json
                updates.append("items = ?")
                params.append(json.dumps(items, ensure_ascii=False))

            if not updates:
                return False

            if DB_TYPE == "sqlite":
                updates.append("updated_at = datetime('now')")
                params.extend([telegram_user_id, template_name.strip().lower()])
                query = f"UPDATE shipment_templates SET {', '.join(updates)} WHERE telegram_user_id = ? AND template_name = ?"
                cursor.execute(query, params)
            else:
                # For PostgreSQL, replace ? with %s
                updates_pg = [u.replace("?", "%s") for u in updates]
                updates_pg.append("updated_at = CURRENT_TIMESTAMP")
                params.extend([telegram_user_id, template_name.strip().lower()])
                query = f"UPDATE shipment_templates SET {', '.join(updates_pg)} WHERE telegram_user_id = %s AND template_name = %s"
                cursor.execute(query, params)

            conn.commit()
            conn.close()

            logger.info(f"✅ Shipment template updated: '{template_name}'")
            return True

        except Exception as e:
            logger.error(f"Failed to update shipment template: {e}")
            return False

    def delete_shipment_template(self, telegram_user_id: int, template_name: str) -> bool:
        """Delete a shipment template"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            if DB_TYPE == "sqlite":
                cursor.execute("""
                    DELETE FROM shipment_templates
                    WHERE telegram_user_id = ? AND template_name = ?
                """, (telegram_user_id, template_name.strip().lower()))
            else:
                cursor.execute("""
                    DELETE FROM shipment_templates
                    WHERE telegram_user_id = %s AND template_name = %s
                """, (telegram_user_id, template_name.strip().lower()))

            conn.commit()
            conn.close()

            logger.info(f"✅ Shipment template deleted: '{template_name}'")
            return True

        except Exception as e:
            logger.error(f"Failed to delete shipment template: {e}")
            return False

    # === Employee Methods ===

    def add_employee(
        self,
        telegram_user_id: int,
        employee_name: str,
        role: str,
        date: str = None
    ) -> bool:
        """
        Add or update an employee

        Args:
            telegram_user_id: User ID
            employee_name: Name of the employee
            role: Employee role ('cashier', 'doner_maker', 'assistant')
            date: Date mentioned in format "YYYY-MM-DD". If None, uses current date

        Returns:
            True if successful
        """
        try:
            if date is None:
                date = datetime.now().strftime("%Y-%m-%d")

            conn = self._get_connection()
            cursor = conn.cursor()

            if DB_TYPE == "sqlite":
                cursor.execute("""
                    INSERT INTO employees (telegram_user_id, employee_name, role, last_mentioned_date)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(telegram_user_id, employee_name, role)
                    DO UPDATE SET last_mentioned_date = ?
                """, (telegram_user_id, employee_name.strip(), role, date, date))
            else:
                cursor.execute("""
                    INSERT INTO employees (telegram_user_id, employee_name, role, last_mentioned_date)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (telegram_user_id, employee_name, role)
                    DO UPDATE SET last_mentioned_date = EXCLUDED.last_mentioned_date
                """, (telegram_user_id, employee_name.strip(), role, date))

            conn.commit()
            conn.close()

            logger.debug(f"✅ Employee added/updated: {employee_name} ({role})")
            return True

        except Exception as e:
            logger.error(f"Failed to add employee: {e}")
            return False

    def get_employees(self, telegram_user_id: int, role: str = None) -> list:
        """
        Get all employees for a user, optionally filtered by role

        Args:
            telegram_user_id: User ID
            role: Optional role filter ('cashier', 'doner_maker', 'assistant')

        Returns:
            List of employee dictionaries
        """
        conn = self._get_connection()

        if role:
            if DB_TYPE == "sqlite":
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, employee_name, role, last_mentioned_date, created_at
                    FROM employees
                    WHERE telegram_user_id = ? AND role = ?
                    ORDER BY last_mentioned_date DESC, employee_name
                """, (telegram_user_id, role))
                rows = cursor.fetchall()
            else:
                cursor = conn.cursor(cursor_factory=RealDictCursor)
                cursor.execute("""
                    SELECT id, employee_name, role, last_mentioned_date, created_at
                    FROM employees
                    WHERE telegram_user_id = %s AND role = %s
                    ORDER BY last_mentioned_date DESC, employee_name
                """, (telegram_user_id, role))
                rows = cursor.fetchall()
        else:
            if DB_TYPE == "sqlite":
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, employee_name, role, last_mentioned_date, created_at
                    FROM employees
                    WHERE telegram_user_id = ?
                    ORDER BY last_mentioned_date DESC, employee_name
                """, (telegram_user_id,))
                rows = cursor.fetchall()
            else:
                cursor = conn.cursor(cursor_factory=RealDictCursor)
                cursor.execute("""
                    SELECT id, employee_name, role, last_mentioned_date, created_at
                    FROM employees
                    WHERE telegram_user_id = %s
                    ORDER BY last_mentioned_date DESC, employee_name
                """, (telegram_user_id,))
                rows = cursor.fetchall()

        conn.close()
        return [dict(row) for row in rows]

    # ==================== Expense Drafts Methods ====================

    def save_expense_drafts(self, telegram_user_id: int, items: list, source: str = "cash", source_account: str = None) -> int:
        """
        Сохранить черновики расходов в БД

        Args:
            telegram_user_id: ID пользователя Telegram
            items: Список ExpenseItem или dict с полями amount, description, expense_type, category
            source: Источник (cash, kaspi)
            source_account: Название счёта

        Returns:
            Количество сохранённых записей
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            count = 0
            for item in items:
                # Поддержка как dict, так и объектов с атрибутами
                if hasattr(item, 'amount'):
                    amount = item.amount
                    description = item.description
                    expense_type = item.expense_type.value if hasattr(item.expense_type, 'value') else str(item.expense_type)
                    category = item.category
                    quantity = getattr(item, 'quantity', None)
                    unit = getattr(item, 'unit', None)
                    price_per_unit = getattr(item, 'price_per_unit', None)
                else:
                    amount = item.get('amount')
                    description = item.get('description')
                    expense_type = item.get('expense_type', 'transaction')
                    category = item.get('category')
                    quantity = item.get('quantity')
                    unit = item.get('unit')
                    price_per_unit = item.get('price_per_unit')

                if DB_TYPE == "sqlite":
                    cursor.execute("""
                        INSERT INTO expense_drafts
                        (telegram_user_id, amount, description, expense_type, category, source, source_account, quantity, unit, price_per_unit)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (telegram_user_id, amount, description, expense_type, category, source, source_account, quantity, unit, price_per_unit))
                else:
                    cursor.execute("""
                        INSERT INTO expense_drafts
                        (telegram_user_id, amount, description, expense_type, category, source, source_account, quantity, unit, price_per_unit)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (telegram_user_id, amount, description, expense_type, category, source, source_account, quantity, unit, price_per_unit))
                count += 1

            conn.commit()
            conn.close()

            logger.info(f"✅ Saved {count} expense drafts for user {telegram_user_id}")
            return count

        except Exception as e:
            logger.error(f"Failed to save expense drafts: {e}")
            return 0

    def get_expense_drafts(self, telegram_user_id: int, status: str = "pending") -> list:
        """
        Получить черновики расходов пользователя

        Args:
            telegram_user_id: ID пользователя
            status: Фильтр по статусу (pending, processed, all)

        Returns:
            Список черновиков
        """
        conn = self._get_connection()

        if DB_TYPE == "sqlite":
            cursor = conn.cursor()
            if status == "all":
                cursor.execute("""
                    SELECT * FROM expense_drafts
                    WHERE telegram_user_id = ?
                    ORDER BY created_at DESC
                """, (telegram_user_id,))
            else:
                cursor.execute("""
                    SELECT * FROM expense_drafts
                    WHERE telegram_user_id = ? AND status = ?
                    ORDER BY created_at DESC
                """, (telegram_user_id, status))
            rows = cursor.fetchall()
            # Convert to dict
            columns = [desc[0] for desc in cursor.description]
            rows = [dict(zip(columns, row)) for row in rows]
        else:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            if status == "all":
                cursor.execute("""
                    SELECT * FROM expense_drafts
                    WHERE telegram_user_id = %s
                    ORDER BY created_at DESC
                """, (telegram_user_id,))
            else:
                cursor.execute("""
                    SELECT * FROM expense_drafts
                    WHERE telegram_user_id = %s AND status = %s
                    ORDER BY created_at DESC
                """, (telegram_user_id, status))
            rows = cursor.fetchall()

        conn.close()
        return [dict(row) for row in rows]

    def get_expense_draft(self, draft_id: int) -> Optional[Dict]:
        """
        Получить один черновик расхода по ID

        Args:
            draft_id: ID черновика

        Returns:
            Черновик или None
        """
        conn = self._get_connection()

        if DB_TYPE == "sqlite":
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM expense_drafts WHERE id = ?", (draft_id,))
            row = cursor.fetchone()
            if row:
                columns = [desc[0] for desc in cursor.description]
                result = dict(zip(columns, row))
            else:
                result = None
        else:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("SELECT * FROM expense_drafts WHERE id = %s", (draft_id,))
            row = cursor.fetchone()
            result = dict(row) if row else None

        conn.close()
        return result

    def update_expense_draft(self, draft_id: int, **kwargs) -> bool:
        """
        Обновить черновик расхода

        Args:
            draft_id: ID черновика
            **kwargs: Поля для обновления (expense_type, category, amount, description, etc.)

        Returns:
            True если успешно
        """
        if not kwargs:
            return False

        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # Build SET clause
            if DB_TYPE == "sqlite":
                set_clause = ", ".join([f"{k} = ?" for k in kwargs.keys()])
                query = f"UPDATE expense_drafts SET {set_clause} WHERE id = ?"
                cursor.execute(query, list(kwargs.values()) + [draft_id])
            else:
                set_clause = ", ".join([f"{k} = %s" for k in kwargs.keys()])
                query = f"UPDATE expense_drafts SET {set_clause} WHERE id = %s"
                cursor.execute(query, list(kwargs.values()) + [draft_id])

            conn.commit()
            conn.close()
            return True

        except Exception as e:
            logger.error(f"Failed to update expense draft: {e}")
            return False

    def delete_expense_draft(self, draft_id: int) -> bool:
        """Удалить черновик"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            if DB_TYPE == "sqlite":
                cursor.execute("DELETE FROM expense_drafts WHERE id = ?", (draft_id,))
            else:
                cursor.execute("DELETE FROM expense_drafts WHERE id = %s", (draft_id,))

            conn.commit()
            conn.close()
            return True

        except Exception as e:
            logger.error(f"Failed to delete expense draft: {e}")
            return False

    def create_expense_draft(
        self,
        telegram_user_id: int,
        amount: float = 0,
        description: str = "",
        expense_type: str = "transaction",
        category: str = None,
        source: str = "cash",
        account_id: int = None,
        poster_account_id: int = None,
        poster_transaction_id: str = None,
        is_income: bool = False,
        completion_status: str = "pending",
        poster_amount: float = None
    ) -> Optional[int]:
        """
        Создать один черновик расхода (для ручного ввода или синхронизации из Poster)

        Args:
            is_income: True если это доход (например, продажа масла), False для расхода
            completion_status: 'pending' (не в Poster), 'completed' (в Poster)
            poster_amount: Текущая сумма в Poster (для отслеживания изменений)

        Returns:
            ID созданного черновика или None при ошибке
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            is_income_int = 1 if is_income else 0

            if DB_TYPE == "sqlite":
                cursor.execute("""
                    INSERT INTO expense_drafts
                    (telegram_user_id, amount, description, expense_type, category, source, account_id, poster_account_id, poster_transaction_id, is_income, completion_status, poster_amount)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (telegram_user_id, amount, description, expense_type, category, source, account_id, poster_account_id, poster_transaction_id, is_income_int, completion_status, poster_amount))
                draft_id = cursor.lastrowid
            else:
                cursor.execute("""
                    INSERT INTO expense_drafts
                    (telegram_user_id, amount, description, expense_type, category, source, account_id, poster_account_id, poster_transaction_id, is_income, completion_status, poster_amount)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (telegram_user_id, amount, description, expense_type, category, source, account_id, poster_account_id, poster_transaction_id, is_income_int, completion_status, poster_amount))
                draft_id = cursor.fetchone()[0]

            conn.commit()
            conn.close()
            logger.info(f"✅ Created expense draft #{draft_id} for user {telegram_user_id} (income={is_income})")
            return draft_id

        except Exception as e:
            logger.error(f"Failed to create expense draft: {e}")
            return None

    def get_expense_draft_by_poster_transaction_id(self, poster_transaction_id: str) -> Optional[dict]:
        """Check if a draft with given poster_transaction_id exists"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            if DB_TYPE == "sqlite":
                cursor.execute(
                    "SELECT id FROM expense_drafts WHERE poster_transaction_id = ?",
                    (poster_transaction_id,)
                )
            else:
                cursor.execute(
                    "SELECT id FROM expense_drafts WHERE poster_transaction_id = %s",
                    (poster_transaction_id,)
                )

            row = cursor.fetchone()
            conn.close()

            if row:
                return {"id": row[0]}
            return None

        except Exception as e:
            logger.error(f"Failed to get expense draft by poster_transaction_id: {e}")
            return None

    def delete_expense_drafts_bulk(self, draft_ids: list) -> int:
        """Удалить несколько черновиков"""
        if not draft_ids:
            return 0

        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            if DB_TYPE == "sqlite":
                placeholders = ",".join(["?" for _ in draft_ids])
                cursor.execute(f"DELETE FROM expense_drafts WHERE id IN ({placeholders})", draft_ids)
            else:
                placeholders = ",".join(["%s" for _ in draft_ids])
                cursor.execute(f"DELETE FROM expense_drafts WHERE id IN ({placeholders})", draft_ids)

            deleted = cursor.rowcount
            conn.commit()
            conn.close()
            return deleted

        except Exception as e:
            logger.error(f"Failed to delete expense drafts: {e}")
            return 0

    def mark_drafts_processed(self, draft_ids: list) -> int:
        """Пометить черновики как обработанные"""
        if not draft_ids:
            return 0

        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            if DB_TYPE == "sqlite":
                placeholders = ",".join(["?" for _ in draft_ids])
                cursor.execute(f"""
                    UPDATE expense_drafts
                    SET status = 'processed', processed_at = CURRENT_TIMESTAMP
                    WHERE id IN ({placeholders})
                """, draft_ids)
            else:
                placeholders = ",".join(["%s" for _ in draft_ids])
                cursor.execute(f"""
                    UPDATE expense_drafts
                    SET status = 'processed', processed_at = CURRENT_TIMESTAMP
                    WHERE id IN ({placeholders})
                """, draft_ids)

            updated = cursor.rowcount
            conn.commit()
            conn.close()
            return updated

        except Exception as e:
            logger.error(f"Failed to mark drafts processed: {e}")
            return 0

    def mark_drafts_in_poster(self, draft_ids: list) -> int:
        """
        Пометить черновики как созданные в Poster (completion_status='completed')
        но оставить на странице (status='pending')
        """
        if not draft_ids:
            return 0

        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            if DB_TYPE == "sqlite":
                placeholders = ",".join(["?" for _ in draft_ids])
                cursor.execute(f"""
                    UPDATE expense_drafts
                    SET completion_status = 'completed'
                    WHERE id IN ({placeholders})
                """, draft_ids)
            else:
                placeholders = ",".join(["%s" for _ in draft_ids])
                cursor.execute(f"""
                    UPDATE expense_drafts
                    SET completion_status = 'completed'
                    WHERE id IN ({placeholders})
                """, draft_ids)

            updated = cursor.rowcount
            conn.commit()
            conn.close()
            logger.info(f"✅ Marked {updated} drafts as in Poster (staying visible)")
            return updated

        except Exception as e:
            logger.error(f"Failed to mark drafts in poster: {e}")
            return 0

    # ==================== Shift Reconciliation Methods ====================

    def get_shift_reconciliation(self, telegram_user_id: int, date: str) -> list:
        """Get shift reconciliation data for a specific date (all sources)"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            placeholder = "?" if DB_TYPE == "sqlite" else "%s"
            cursor.execute(f"""
                SELECT * FROM shift_reconciliation
                WHERE telegram_user_id = {placeholder} AND date = {placeholder}
                ORDER BY source
            """, (telegram_user_id, date))

            columns = [desc[0] for desc in cursor.description]
            rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
            conn.close()
            return rows

        except Exception as e:
            logger.error(f"Failed to get shift reconciliation: {e}")
            return []

    def save_shift_reconciliation(self, telegram_user_id: int, date: str, source: str,
                                   opening_balance=None, closing_balance=None,
                                   total_difference=None, notes=None) -> bool:
        """Save or update shift reconciliation for a specific date and source (upsert)"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            if DB_TYPE == "sqlite":
                cursor.execute("""
                    INSERT INTO shift_reconciliation
                        (telegram_user_id, date, source, opening_balance, closing_balance, total_difference, notes, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(telegram_user_id, date, source)
                    DO UPDATE SET
                        opening_balance = excluded.opening_balance,
                        closing_balance = excluded.closing_balance,
                        total_difference = excluded.total_difference,
                        notes = excluded.notes,
                        updated_at = CURRENT_TIMESTAMP
                """, (telegram_user_id, date, source, opening_balance, closing_balance, total_difference, notes))
            else:
                cursor.execute("""
                    INSERT INTO shift_reconciliation
                        (telegram_user_id, date, source, opening_balance, closing_balance, total_difference, notes, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT(telegram_user_id, date, source)
                    DO UPDATE SET
                        opening_balance = EXCLUDED.opening_balance,
                        closing_balance = EXCLUDED.closing_balance,
                        total_difference = EXCLUDED.total_difference,
                        notes = EXCLUDED.notes,
                        updated_at = CURRENT_TIMESTAMP
                """, (telegram_user_id, date, source, opening_balance, closing_balance, total_difference, notes))

            conn.commit()
            conn.close()
            return True

        except Exception as e:
            logger.error(f"Failed to save shift reconciliation: {e}")
            return False

    # ==================== Supply Drafts Methods ====================

    def save_supply_draft(
        self,
        telegram_user_id: int,
        supplier_name: str,
        invoice_date: str,
        items: list,
        total_sum: float = None,
        linked_expense_draft_id: int = None,
        ocr_text: str = None,
        account_id: int = None,
        source: str = 'cash'
    ) -> int:
        """
        Сохранить черновик поставки с позициями

        Args:
            telegram_user_id: ID пользователя Telegram
            supplier_name: Название поставщика
            invoice_date: Дата накладной (YYYY-MM-DD)
            items: Список позиций [{'name': str, 'quantity': float, 'unit': str, 'price': float, 'total': float}]
            total_sum: Общая сумма
            linked_expense_draft_id: ID связанного черновика расхода
            ocr_text: Распознанный OCR текст
            account_id: ID счёта списания
            source: Источник (cash, kaspi)

        Returns:
            ID созданного черновика поставки или 0 при ошибке
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # Insert supply draft
            if DB_TYPE == "sqlite":
                cursor.execute("""
                    INSERT INTO supply_drafts
                    (telegram_user_id, supplier_name, invoice_date, total_sum, linked_expense_draft_id, ocr_text, account_id, source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (telegram_user_id, supplier_name, invoice_date, total_sum, linked_expense_draft_id, ocr_text, account_id, source))
                supply_draft_id = cursor.lastrowid
            else:
                cursor.execute("""
                    INSERT INTO supply_drafts
                    (telegram_user_id, supplier_name, invoice_date, total_sum, linked_expense_draft_id, ocr_text, account_id, source)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (telegram_user_id, supplier_name, invoice_date, total_sum, linked_expense_draft_id, ocr_text, account_id, source))
                supply_draft_id = cursor.fetchone()[0]

            # Insert supply draft items
            for item in items:
                item_name = item.get('name', '')
                quantity = float(item.get('quantity') or 1)
                unit = item.get('unit', 'шт')
                price_per_unit = float(item.get('price') or 0)
                total = float(item.get('total') or 0) or (quantity * price_per_unit)

                if DB_TYPE == "sqlite":
                    cursor.execute("""
                        INSERT INTO supply_draft_items
                        (supply_draft_id, item_name, quantity, unit, price_per_unit, total)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (supply_draft_id, item_name, quantity, unit, price_per_unit, total))
                else:
                    cursor.execute("""
                        INSERT INTO supply_draft_items
                        (supply_draft_id, item_name, quantity, unit, price_per_unit, total)
                        VALUES (%s, %s, %s, %s, %s, %s)
                    """, (supply_draft_id, item_name, quantity, unit, price_per_unit, total))

            conn.commit()
            conn.close()

            logger.info(f"✅ Saved supply draft #{supply_draft_id} with {len(items)} items for user {telegram_user_id}")
            return supply_draft_id

        except Exception as e:
            logger.error(f"Failed to save supply draft: {e}")
            return 0

    def create_empty_supply_draft(
        self,
        telegram_user_id: int,
        supplier_name: str = "",
        invoice_date: str = None,
        total_sum: float = 0,
        linked_expense_draft_id: int = None,
        account_id: int = None,
        source: str = 'cash'
    ) -> Optional[int]:
        """
        Создать пустой черновик поставки (без товаров) - для ручного ввода

        Returns:
            ID созданного черновика или None при ошибке
        """
        from datetime import datetime
        if not invoice_date:
            invoice_date = datetime.now().strftime("%Y-%m-%d")

        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            if DB_TYPE == "sqlite":
                cursor.execute("""
                    INSERT INTO supply_drafts
                    (telegram_user_id, supplier_name, invoice_date, total_sum, linked_expense_draft_id, account_id, source)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (telegram_user_id, supplier_name, invoice_date, total_sum, linked_expense_draft_id, account_id, source))
                supply_draft_id = cursor.lastrowid
            else:
                cursor.execute("""
                    INSERT INTO supply_drafts
                    (telegram_user_id, supplier_name, invoice_date, total_sum, linked_expense_draft_id, account_id, source)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (telegram_user_id, supplier_name, invoice_date, total_sum, linked_expense_draft_id, account_id, source))
                supply_draft_id = cursor.fetchone()[0]

            conn.commit()
            conn.close()
            logger.info(f"✅ Created empty supply draft #{supply_draft_id} for user {telegram_user_id}")
            return supply_draft_id

        except Exception as e:
            logger.error(f"Failed to create empty supply draft: {e}")
            return None

    def update_supply_draft(self, supply_draft_id: int, **kwargs) -> bool:
        """
        Обновить черновик поставки

        Args:
            supply_draft_id: ID черновика
            **kwargs: Поля для обновления (supplier_name, supplier_id, invoice_date, total_sum, account_id, source)
        """
        if not kwargs:
            return False

        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            if DB_TYPE == "sqlite":
                set_clause = ", ".join([f"{k} = ?" for k in kwargs.keys()])
                query = f"UPDATE supply_drafts SET {set_clause} WHERE id = ?"
                cursor.execute(query, list(kwargs.values()) + [supply_draft_id])
            else:
                set_clause = ", ".join([f"{k} = %s" for k in kwargs.keys()])
                query = f"UPDATE supply_drafts SET {set_clause} WHERE id = %s"
                cursor.execute(query, list(kwargs.values()) + [supply_draft_id])

            conn.commit()
            conn.close()
            return True

        except Exception as e:
            logger.error(f"Failed to update supply draft: {e}")
            return False

    def add_supply_draft_item(
        self,
        supply_draft_id: int,
        item_name: str = "",
        quantity: float = 0,
        unit: str = "шт",
        price_per_unit: float = 0,
        poster_ingredient_id: int = None,
        poster_ingredient_name: str = None,
        poster_account_id: int = None,
        poster_account_name: str = None,
        item_type: str = 'ingredient',  # 'ingredient' or 'product'
        storage_id: int = None,
        storage_name: str = None
    ) -> Optional[int]:
        """
        Добавить позицию в черновик поставки

        Returns:
            ID созданной позиции или None при ошибке
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            total = quantity * price_per_unit

            if DB_TYPE == "sqlite":
                cursor.execute("""
                    INSERT INTO supply_draft_items
                    (supply_draft_id, item_name, quantity, unit, price_per_unit, total,
                     poster_ingredient_id, poster_ingredient_name, poster_account_id, poster_account_name,
                     item_type, storage_id, storage_name)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (supply_draft_id, item_name, quantity, unit, price_per_unit, total,
                      poster_ingredient_id, poster_ingredient_name, poster_account_id, poster_account_name,
                      item_type, storage_id, storage_name))
                item_id = cursor.lastrowid
            else:
                cursor.execute("""
                    INSERT INTO supply_draft_items
                    (supply_draft_id, item_name, quantity, unit, price_per_unit, total,
                     poster_ingredient_id, poster_ingredient_name, poster_account_id, poster_account_name,
                     item_type, storage_id, storage_name)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (supply_draft_id, item_name, quantity, unit, price_per_unit, total,
                      poster_ingredient_id, poster_ingredient_name, poster_account_id, poster_account_name,
                      item_type, storage_id, storage_name))
                item_id = cursor.fetchone()[0]

            conn.commit()
            conn.close()
            return item_id

        except Exception as e:
            logger.error(f"Failed to add supply draft item: {e}")
            return None

    def delete_supply_draft_item(self, item_id: int) -> bool:
        """Удалить позицию из черновика поставки"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            if DB_TYPE == "sqlite":
                cursor.execute("DELETE FROM supply_draft_items WHERE id = ?", (item_id,))
            else:
                cursor.execute("DELETE FROM supply_draft_items WHERE id = %s", (item_id,))

            conn.commit()
            conn.close()
            return True

        except Exception as e:
            logger.error(f"Failed to delete supply draft item: {e}")
            return False

    def get_supply_drafts(self, telegram_user_id: int, status: str = "pending") -> list:
        """
        Получить черновики поставок пользователя

        Args:
            telegram_user_id: ID пользователя
            status: Фильтр по статусу (pending, processed, all)

        Returns:
            Список черновиков поставок
        """
        conn = self._get_connection()

        if DB_TYPE == "sqlite":
            cursor = conn.cursor()
            if status == "all":
                cursor.execute("""
                    SELECT * FROM supply_drafts
                    WHERE telegram_user_id = ?
                    ORDER BY created_at DESC
                """, (telegram_user_id,))
            else:
                cursor.execute("""
                    SELECT * FROM supply_drafts
                    WHERE telegram_user_id = ? AND status = ?
                    ORDER BY created_at DESC
                """, (telegram_user_id, status))
            rows = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description]
            rows = [dict(zip(columns, row)) for row in rows]
        else:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            if status == "all":
                cursor.execute("""
                    SELECT * FROM supply_drafts
                    WHERE telegram_user_id = %s
                    ORDER BY created_at DESC
                """, (telegram_user_id,))
            else:
                cursor.execute("""
                    SELECT * FROM supply_drafts
                    WHERE telegram_user_id = %s AND status = %s
                    ORDER BY created_at DESC
                """, (telegram_user_id, status))
            rows = cursor.fetchall()

        conn.close()
        return [dict(row) for row in rows]

    def get_supply_draft_with_items(self, supply_draft_id: int) -> Optional[Dict]:
        """
        Получить черновик поставки со всеми позициями

        Args:
            supply_draft_id: ID черновика поставки

        Returns:
            Черновик поставки с items или None
        """
        conn = self._get_connection()

        if DB_TYPE == "sqlite":
            cursor = conn.cursor()

            # Get supply draft
            cursor.execute("SELECT * FROM supply_drafts WHERE id = ?", (supply_draft_id,))
            draft_row = cursor.fetchone()
            if not draft_row:
                conn.close()
                return None

            columns = [desc[0] for desc in cursor.description]
            draft = dict(zip(columns, draft_row))

            # Get items
            cursor.execute("""
                SELECT * FROM supply_draft_items
                WHERE supply_draft_id = ?
                ORDER BY id
            """, (supply_draft_id,))
            item_rows = cursor.fetchall()
            item_columns = [desc[0] for desc in cursor.description]
            draft['items'] = [dict(zip(item_columns, row)) for row in item_rows]
        else:
            cursor = conn.cursor(cursor_factory=RealDictCursor)

            cursor.execute("SELECT * FROM supply_drafts WHERE id = %s", (supply_draft_id,))
            draft_row = cursor.fetchone()
            if not draft_row:
                conn.close()
                return None

            draft = dict(draft_row)

            cursor.execute("""
                SELECT * FROM supply_draft_items
                WHERE supply_draft_id = %s
                ORDER BY id
            """, (supply_draft_id,))
            draft['items'] = [dict(row) for row in cursor.fetchall()]

        conn.close()
        return draft

    def update_supply_draft_item(self, item_id: int, **kwargs) -> bool:
        """
        Обновить позицию в черновике поставки

        Args:
            item_id: ID позиции
            **kwargs: Поля для обновления (poster_ingredient_id, poster_ingredient_name, quantity, etc.)

        Returns:
            True если успешно
        """
        if not kwargs:
            return False

        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            if DB_TYPE == "sqlite":
                set_clause = ", ".join([f"{k} = ?" for k in kwargs.keys()])
                query = f"UPDATE supply_draft_items SET {set_clause} WHERE id = ?"
                cursor.execute(query, list(kwargs.values()) + [item_id])
            else:
                set_clause = ", ".join([f"{k} = %s" for k in kwargs.keys()])
                query = f"UPDATE supply_draft_items SET {set_clause} WHERE id = %s"
                cursor.execute(query, list(kwargs.values()) + [item_id])

            conn.commit()
            conn.close()
            return True

        except Exception as e:
            logger.error(f"Failed to update supply draft item: {e}")
            return False

    def delete_supply_draft_item(self, item_id: int) -> bool:
        """Удалить отдельную позицию из черновика поставки"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            if DB_TYPE == "sqlite":
                cursor.execute("DELETE FROM supply_draft_items WHERE id = ?", (item_id,))
            else:
                cursor.execute("DELETE FROM supply_draft_items WHERE id = %s", (item_id,))

            conn.commit()
            conn.close()
            return True

        except Exception as e:
            logger.error(f"Failed to delete supply draft item: {e}")
            return False

    def delete_supply_draft(self, supply_draft_id: int) -> bool:
        """Удалить черновик поставки (вместе с позициями благодаря CASCADE)"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            if DB_TYPE == "sqlite":
                cursor.execute("DELETE FROM supply_drafts WHERE id = ?", (supply_draft_id,))
            else:
                cursor.execute("DELETE FROM supply_drafts WHERE id = %s", (supply_draft_id,))

            conn.commit()
            conn.close()
            return True

        except Exception as e:
            logger.error(f"Failed to delete supply draft: {e}")
            return False

    def mark_supply_draft_processed(self, supply_draft_id: int) -> bool:
        """Пометить черновик поставки как обработанный"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            if DB_TYPE == "sqlite":
                cursor.execute("""
                    UPDATE supply_drafts
                    SET status = 'processed', processed_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (supply_draft_id,))
            else:
                cursor.execute("""
                    UPDATE supply_drafts
                    SET status = 'processed', processed_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (supply_draft_id,))

            conn.commit()
            conn.close()
            return True

        except Exception as e:
            logger.error(f"Failed to mark supply draft processed: {e}")
            return False

    def get_pending_supply_items(self, telegram_user_id: int) -> list:
        """
        Получить все pending позиции из expense_drafts с типом 'supply'
        Используется для связывания накладных с расходами

        Returns:
            Список pending расходов с expense_type='supply'
        """
        conn = self._get_connection()

        if DB_TYPE == "sqlite":
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM expense_drafts
                WHERE telegram_user_id = ? AND status = 'pending' AND expense_type = 'supply'
                ORDER BY created_at DESC
            """, (telegram_user_id,))
            rows = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description]
            rows = [dict(zip(columns, row)) for row in rows]
        else:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                SELECT * FROM expense_drafts
                WHERE telegram_user_id = %s AND status = 'pending' AND expense_type = 'supply'
                ORDER BY created_at DESC
            """, (telegram_user_id,))
            rows = cursor.fetchall()

        conn.close()
        return [dict(row) for row in rows]


# Singleton instance
_db: Optional[UserDatabase] = None


def get_database() -> UserDatabase:
    """Get singleton database instance"""
    global _db
    if _db is None:
        _db = UserDatabase()
    return _db
