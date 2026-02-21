"""Database management for multi-tenant bot - supports both SQLite and PostgreSQL"""
import os
import logging
from contextlib import contextmanager
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


class _ManagedConnection:
    """Connection wrapper that ensures cleanup even if conn.close() is forgotten.

    - Safe double-close (no error if close() called twice)
    - __del__ catches leaked connections when garbage collected
    - In CPython, reference counting ensures immediate cleanup when function returns
    - Supports context manager protocol (with statement)
    """

    def __init__(self, conn):
        self._conn = conn
        self._closed = False

    def cursor(self, *args, **kwargs):
        return self._conn.cursor(*args, **kwargs)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        if not self._closed:
            self._closed = True
            self._conn.close()

    def __del__(self):
        self.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    @property
    def row_factory(self):
        return self._conn.row_factory

    @row_factory.setter
    def row_factory(self, value):
        self._conn.row_factory = value


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
        """Get managed database connection. Auto-closes when garbage collected."""
        if DB_TYPE == "sqlite":
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            return _ManagedConnection(conn)
        else:
            # PostgreSQL
            return _ManagedConnection(psycopg2.connect(self.db_url))

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

            # Index for fast lookup during sync (poster_transaction_id used in O(n) scan)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_expense_drafts_poster_txn
                ON expense_drafts(poster_transaction_id)
            """)

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

            # Table for shift closings (история закрытий смены)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS shift_closings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_user_id INTEGER NOT NULL,
                    date TEXT NOT NULL,
                    wolt REAL DEFAULT 0,
                    halyk REAL DEFAULT 0,
                    kaspi REAL DEFAULT 0,
                    kaspi_cafe REAL DEFAULT 0,
                    cash_bills REAL DEFAULT 0,
                    cash_coins REAL DEFAULT 0,
                    shift_start REAL DEFAULT 0,
                    deposits REAL DEFAULT 0,
                    expenses REAL DEFAULT 0,
                    cash_to_leave REAL DEFAULT 15000,
                    poster_trade REAL DEFAULT 0,
                    poster_bonus REAL DEFAULT 0,
                    poster_card REAL DEFAULT 0,
                    poster_cash REAL DEFAULT 0,
                    transactions_count INTEGER DEFAULT 0,
                    fact_cashless REAL DEFAULT 0,
                    fact_total REAL DEFAULT 0,
                    fact_adjusted REAL DEFAULT 0,
                    poster_total REAL DEFAULT 0,
                    day_result REAL DEFAULT 0,
                    shift_left REAL DEFAULT 0,
                    collection REAL DEFAULT 0,
                    cashless_diff REAL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT,
                    UNIQUE(telegram_user_id, date),
                    FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id) ON DELETE CASCADE
                )
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_shift_closings_user_date
                ON shift_closings(telegram_user_id, date)
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

            # Index for fast lookup during sync (poster_transaction_id used in O(n) scan)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_expense_drafts_poster_txn
                ON expense_drafts(poster_transaction_id)
            """)

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

            # Table for shift closings (история закрытий смены)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS shift_closings (
                    id SERIAL PRIMARY KEY,
                    telegram_user_id BIGINT NOT NULL,
                    date DATE NOT NULL,
                    wolt REAL DEFAULT 0,
                    halyk REAL DEFAULT 0,
                    kaspi REAL DEFAULT 0,
                    kaspi_cafe REAL DEFAULT 0,
                    cash_bills REAL DEFAULT 0,
                    cash_coins REAL DEFAULT 0,
                    shift_start REAL DEFAULT 0,
                    deposits REAL DEFAULT 0,
                    expenses REAL DEFAULT 0,
                    cash_to_leave REAL DEFAULT 15000,
                    poster_trade REAL DEFAULT 0,
                    poster_bonus REAL DEFAULT 0,
                    poster_card REAL DEFAULT 0,
                    poster_cash REAL DEFAULT 0,
                    transactions_count INTEGER DEFAULT 0,
                    fact_cashless REAL DEFAULT 0,
                    fact_total REAL DEFAULT 0,
                    fact_adjusted REAL DEFAULT 0,
                    poster_total REAL DEFAULT 0,
                    day_result REAL DEFAULT 0,
                    shift_left REAL DEFAULT 0,
                    collection REAL DEFAULT 0,
                    cashless_diff REAL DEFAULT 0,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP,
                    UNIQUE(telegram_user_id, date),
                    FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id) ON DELETE CASCADE
                )
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_shift_closings_user_date
                ON shift_closings(telegram_user_id, date)
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

        # Run migration for cafe access tokens and shift_closings.poster_account_id
        self._migrate_cafe_access()

        # Run migration for cashier access tokens and cashier_shift_data
        self._migrate_cashier_access()

        # Run migration for web_users (auth system)
        self._migrate_web_users()

        # Run migration to fix shift_closings UNIQUE constraint (cafe + main same date)
        self._migrate_shift_closings_fix_unique()

        # Run migration to add salaries columns to shift_closings (cafe salaries)
        self._migrate_cafe_salaries()

    def _migrate_shift_closings_fix_unique(self):
        """Fix UNIQUE constraint on shift_closings to include poster_account_id.

        Old constraint UNIQUE(telegram_user_id, date) prevents having both
        a cafe and main shift closing for the same date. Replace with partial
        unique indexes that properly handle poster_account_id.
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            if DB_TYPE == "sqlite":
                # SQLite: can't ALTER DROP constraint, but we can create partial indexes.
                # The old inline UNIQUE stays but we change code to not use ON CONFLICT on it.
                try:
                    cursor.execute("""
                        CREATE UNIQUE INDEX IF NOT EXISTS shift_closings_user_date_main_idx
                        ON shift_closings (telegram_user_id, date)
                        WHERE poster_account_id IS NULL
                    """)
                    cursor.execute("""
                        CREATE UNIQUE INDEX IF NOT EXISTS shift_closings_user_date_cafe_idx
                        ON shift_closings (telegram_user_id, date, poster_account_id)
                        WHERE poster_account_id IS NOT NULL
                    """)
                    logger.info("✅ shift_closings: created partial unique indexes (SQLite)")
                except Exception:
                    pass  # Indexes already exist
            else:
                # PostgreSQL: drop old constraint and create partial unique indexes
                try:
                    cursor.execute("""
                        ALTER TABLE shift_closings
                        DROP CONSTRAINT IF EXISTS shift_closings_telegram_user_id_date_key
                    """)
                    logger.info("✅ shift_closings: dropped old UNIQUE(telegram_user_id, date)")
                except Exception:
                    pass  # Constraint already dropped

                try:
                    cursor.execute("""
                        CREATE UNIQUE INDEX IF NOT EXISTS shift_closings_user_date_main_idx
                        ON shift_closings (telegram_user_id, date)
                        WHERE poster_account_id IS NULL
                    """)
                    cursor.execute("""
                        CREATE UNIQUE INDEX IF NOT EXISTS shift_closings_user_date_cafe_idx
                        ON shift_closings (telegram_user_id, date, poster_account_id)
                        WHERE poster_account_id IS NOT NULL
                    """)
                    logger.info("✅ shift_closings: created partial unique indexes (PostgreSQL)")
                except Exception:
                    pass  # Indexes already exist

            conn.commit()
            conn.close()

        except Exception as e:
            logger.error(f"shift_closings unique fix migration error: {e}")

    def _migrate_cafe_salaries(self):
        """Add salaries_created and salaries_data columns to shift_closings for cafe salary tracking"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            try:
                if DB_TYPE == "sqlite":
                    cursor.execute("ALTER TABLE shift_closings ADD COLUMN salaries_created INTEGER DEFAULT 0")
                else:
                    cursor.execute("ALTER TABLE shift_closings ADD COLUMN salaries_created BOOLEAN DEFAULT FALSE")
                logger.info("✅ Cafe salaries migration: added salaries_created to shift_closings")
            except Exception:
                pass  # Column already exists

            try:
                cursor.execute("ALTER TABLE shift_closings ADD COLUMN salaries_data TEXT DEFAULT NULL")
                logger.info("✅ Cafe salaries migration: added salaries_data to shift_closings")
            except Exception:
                pass  # Column already exists

            conn.commit()
            conn.close()
            logger.info("✅ Cafe salaries migration: completed")

        except Exception as e:
            logger.error(f"Cafe salaries migration error: {e}")

    def _migrate_cafe_access(self):
        """Create cafe_access_tokens table and add poster_account_id to shift_closings + kaspi_pizzburg column"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # 1. Create cafe_access_tokens table
            if DB_TYPE == "sqlite":
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS cafe_access_tokens (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        token TEXT UNIQUE NOT NULL,
                        telegram_user_id INTEGER NOT NULL,
                        poster_account_id INTEGER NOT NULL,
                        label TEXT,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id) ON DELETE CASCADE
                    )
                """)
            else:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS cafe_access_tokens (
                        id SERIAL PRIMARY KEY,
                        token TEXT UNIQUE NOT NULL,
                        telegram_user_id BIGINT NOT NULL,
                        poster_account_id INTEGER NOT NULL,
                        label TEXT,
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id) ON DELETE CASCADE
                    )
                """)

            # 2. Add poster_account_id to shift_closings (nullable, NULL = primary)
            try:
                if DB_TYPE == "sqlite":
                    cursor.execute("ALTER TABLE shift_closings ADD COLUMN poster_account_id INTEGER DEFAULT NULL")
                else:
                    cursor.execute("ALTER TABLE shift_closings ADD COLUMN poster_account_id INTEGER DEFAULT NULL")
                logger.info("✅ Cafe migration: added poster_account_id to shift_closings")
            except Exception:
                pass  # Column already exists

            # 3. Add kaspi_pizzburg column to shift_closings (for Cafe: deliveries via Pizzburg couriers)
            try:
                if DB_TYPE == "sqlite":
                    cursor.execute("ALTER TABLE shift_closings ADD COLUMN kaspi_pizzburg REAL DEFAULT 0")
                else:
                    cursor.execute("ALTER TABLE shift_closings ADD COLUMN kaspi_pizzburg REAL DEFAULT 0")
                logger.info("✅ Cafe migration: added kaspi_pizzburg to shift_closings")
            except Exception:
                pass  # Column already exists

            conn.commit()
            conn.close()
            logger.info("✅ Cafe migration: completed")

        except Exception as e:
            logger.error(f"Cafe migration error: {e}")

    def _migrate_cashier_access(self):
        """Create cashier_access_tokens and cashier_shift_data tables, add transfers_created to shift_closings"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # 1. Create cashier_access_tokens table
            if DB_TYPE == "sqlite":
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS cashier_access_tokens (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        token TEXT UNIQUE NOT NULL,
                        telegram_user_id INTEGER NOT NULL,
                        poster_account_id INTEGER NOT NULL,
                        label TEXT,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id) ON DELETE CASCADE
                    )
                """)
            else:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS cashier_access_tokens (
                        id SERIAL PRIMARY KEY,
                        token TEXT UNIQUE NOT NULL,
                        telegram_user_id BIGINT NOT NULL,
                        poster_account_id INTEGER NOT NULL,
                        label TEXT,
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id) ON DELETE CASCADE
                    )
                """)

            # 2. Create cashier_shift_data table
            if DB_TYPE == "sqlite":
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS cashier_shift_data (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        telegram_user_id INTEGER NOT NULL,
                        date TEXT NOT NULL,
                        cashier_count INTEGER,
                        cashier_names TEXT,
                        assistant_start_time TEXT,
                        doner_name TEXT,
                        assistant_name TEXT,
                        salaries_data TEXT,
                        salaries_created INTEGER DEFAULT 0,
                        wolt REAL DEFAULT 0,
                        halyk REAL DEFAULT 0,
                        cash_bills REAL DEFAULT 0,
                        cash_coins REAL DEFAULT 0,
                        expenses REAL DEFAULT 0,
                        shift_data_submitted INTEGER DEFAULT 0,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(telegram_user_id, date)
                    )
                """)
            else:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS cashier_shift_data (
                        id SERIAL PRIMARY KEY,
                        telegram_user_id BIGINT NOT NULL,
                        date DATE NOT NULL,
                        cashier_count INTEGER,
                        cashier_names TEXT,
                        assistant_start_time TEXT,
                        doner_name TEXT,
                        assistant_name TEXT,
                        salaries_data TEXT,
                        salaries_created BOOLEAN DEFAULT FALSE,
                        wolt REAL DEFAULT 0,
                        halyk REAL DEFAULT 0,
                        cash_bills REAL DEFAULT 0,
                        cash_coins REAL DEFAULT 0,
                        expenses REAL DEFAULT 0,
                        shift_data_submitted BOOLEAN DEFAULT FALSE,
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(telegram_user_id, date)
                    )
                """)

            # 3. Add transfers_created to shift_closings
            try:
                if DB_TYPE == "sqlite":
                    cursor.execute("ALTER TABLE shift_closings ADD COLUMN transfers_created INTEGER DEFAULT 0")
                else:
                    cursor.execute("ALTER TABLE shift_closings ADD COLUMN transfers_created BOOLEAN DEFAULT FALSE")
                logger.info("✅ Cashier migration: added transfers_created to shift_closings")
            except Exception:
                pass  # Column already exists

            conn.commit()
            conn.close()
            logger.info("✅ Cashier migration: completed")

        except Exception as e:
            logger.error(f"Cashier migration error: {e}")

    def _migrate_web_users(self):
        """Create web_users table for session-based authentication with roles"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            if DB_TYPE == "sqlite":
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS web_users (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        telegram_user_id INTEGER NOT NULL,
                        username TEXT UNIQUE NOT NULL,
                        password_hash TEXT NOT NULL,
                        role TEXT NOT NULL CHECK(role IN ('owner', 'admin', 'cashier')),
                        label TEXT,
                        poster_account_id INTEGER,
                        is_active INTEGER DEFAULT 1,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        last_login TEXT,
                        FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id) ON DELETE CASCADE
                    )
                """)
            else:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS web_users (
                        id SERIAL PRIMARY KEY,
                        telegram_user_id BIGINT NOT NULL,
                        username TEXT UNIQUE NOT NULL,
                        password_hash TEXT NOT NULL,
                        role TEXT NOT NULL CHECK(role IN ('owner', 'admin', 'cashier')),
                        label TEXT,
                        poster_account_id INTEGER,
                        is_active BOOLEAN DEFAULT TRUE,
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        last_login TIMESTAMP,
                        FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id) ON DELETE CASCADE
                    )
                """)

            conn.commit()
            conn.close()
            logger.info("✅ Web users migration: completed")

        except Exception as e:
            logger.error(f"Web users migration error: {e}")

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

    def get_all_user_ids_with_accounts(self) -> list:
        """Get all distinct telegram_user_ids that have poster accounts"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT telegram_user_id FROM poster_accounts")
            rows = cursor.fetchall()
            conn.close()
            return [row[0] for row in rows]
        except Exception as e:
            logger.error(f"Failed to get user IDs with accounts: {e}")
            return []

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

    def update_expense_draft(self, draft_id: int, telegram_user_id: int = None, **kwargs) -> bool:
        """
        Обновить черновик расхода

        Args:
            draft_id: ID черновика
            telegram_user_id: ID владельца (если передан — проверяет принадлежность)
            **kwargs: Поля для обновления (expense_type, category, amount, description, etc.)

        Returns:
            True если успешно
        """
        if not kwargs:
            return False

        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # Build SET clause with optional ownership check
            if DB_TYPE == "sqlite":
                set_clause = ", ".join([f"{k} = ?" for k in kwargs.keys()])
                if telegram_user_id is not None:
                    query = f"UPDATE expense_drafts SET {set_clause} WHERE id = ? AND telegram_user_id = ?"
                    cursor.execute(query, list(kwargs.values()) + [draft_id, telegram_user_id])
                else:
                    query = f"UPDATE expense_drafts SET {set_clause} WHERE id = ?"
                    cursor.execute(query, list(kwargs.values()) + [draft_id])
            else:
                set_clause = ", ".join([f"{k} = %s" for k in kwargs.keys()])
                if telegram_user_id is not None:
                    query = f"UPDATE expense_drafts SET {set_clause} WHERE id = %s AND telegram_user_id = %s"
                    cursor.execute(query, list(kwargs.values()) + [draft_id, telegram_user_id])
                else:
                    query = f"UPDATE expense_drafts SET {set_clause} WHERE id = %s"
                    cursor.execute(query, list(kwargs.values()) + [draft_id])

            conn.commit()
            conn.close()
            return True

        except Exception as e:
            logger.error(f"Failed to update expense draft: {e}")
            return False

    def delete_expense_draft(self, draft_id: int, telegram_user_id: int = None) -> bool:
        """Удалить черновик (если telegram_user_id передан — проверяет принадлежность)"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            if DB_TYPE == "sqlite":
                if telegram_user_id is not None:
                    cursor.execute("DELETE FROM expense_drafts WHERE id = ? AND telegram_user_id = ?", (draft_id, telegram_user_id))
                else:
                    cursor.execute("DELETE FROM expense_drafts WHERE id = ?", (draft_id,))
            else:
                if telegram_user_id is not None:
                    cursor.execute("DELETE FROM expense_drafts WHERE id = %s AND telegram_user_id = %s", (draft_id, telegram_user_id))
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

    def delete_expense_drafts_bulk(self, draft_ids: list, telegram_user_id: int = None) -> int:
        """Удалить несколько черновиков (если telegram_user_id передан — только свои)"""
        if not draft_ids:
            return 0

        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            if DB_TYPE == "sqlite":
                placeholders = ",".join(["?" for _ in draft_ids])
                if telegram_user_id is not None:
                    cursor.execute(f"DELETE FROM expense_drafts WHERE id IN ({placeholders}) AND telegram_user_id = ?", draft_ids + [telegram_user_id])
                else:
                    cursor.execute(f"DELETE FROM expense_drafts WHERE id IN ({placeholders})", draft_ids)
            else:
                placeholders = ",".join(["%s" for _ in draft_ids])
                if telegram_user_id is not None:
                    cursor.execute(f"DELETE FROM expense_drafts WHERE id IN ({placeholders}) AND telegram_user_id = %s", draft_ids + [telegram_user_id])
                else:
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

    # ==================== Shift Closings Methods ====================

    def save_shift_closing(self, telegram_user_id: int, date: str, data: dict, poster_account_id: int = None) -> bool:
        """Save or update shift closing data for a specific date (upsert)"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            fields = [
                'wolt', 'halyk', 'kaspi', 'kaspi_cafe', 'kaspi_pizzburg',
                'cash_bills', 'cash_coins',
                'shift_start', 'deposits', 'expenses', 'cash_to_leave',
                'poster_trade', 'poster_bonus', 'poster_card', 'poster_cash',
                'transactions_count',
                'fact_cashless', 'fact_total', 'fact_adjusted', 'poster_total',
                'day_result', 'shift_left', 'collection', 'cashless_diff'
            ]

            values = [data.get(f, 0) for f in fields]

            if poster_account_id is not None:
                # Cafe shift closing: unique by (user, account, date)
                if DB_TYPE == "sqlite":
                    # Check if exists
                    cursor.execute("""
                        SELECT id FROM shift_closings
                        WHERE telegram_user_id = ? AND date = ? AND poster_account_id = ?
                    """, (telegram_user_id, date, poster_account_id))
                    existing = cursor.fetchone()

                    if existing:
                        update_parts = ', '.join([f'{f} = ?' for f in fields])
                        cursor.execute(f"""
                            UPDATE shift_closings SET {update_parts}, updated_at = CURRENT_TIMESTAMP
                            WHERE telegram_user_id = ? AND date = ? AND poster_account_id = ?
                        """, values + [telegram_user_id, date, poster_account_id])
                    else:
                        all_fields = ['telegram_user_id', 'date', 'poster_account_id'] + fields
                        placeholders = ', '.join(['?'] * len(all_fields))
                        cursor.execute(f"""
                            INSERT INTO shift_closings ({', '.join(all_fields)}, updated_at)
                            VALUES ({placeholders}, CURRENT_TIMESTAMP)
                        """, [telegram_user_id, date, poster_account_id] + values)
                else:
                    cursor.execute("""
                        SELECT id FROM shift_closings
                        WHERE telegram_user_id = %s AND date = %s AND poster_account_id = %s
                    """, (telegram_user_id, date, poster_account_id))
                    existing = cursor.fetchone()

                    if existing:
                        update_parts = ', '.join([f'{f} = %s' for f in fields])
                        cursor.execute(f"""
                            UPDATE shift_closings SET {update_parts}, updated_at = CURRENT_TIMESTAMP
                            WHERE telegram_user_id = %s AND date = %s AND poster_account_id = %s
                        """, values + [telegram_user_id, date, poster_account_id])
                    else:
                        all_fields = ['telegram_user_id', 'date', 'poster_account_id'] + fields
                        placeholders = ', '.join(['%s'] * len(all_fields))
                        cursor.execute(f"""
                            INSERT INTO shift_closings ({', '.join(all_fields)}, updated_at)
                            VALUES ({placeholders}, CURRENT_TIMESTAMP)
                        """, [telegram_user_id, date, poster_account_id] + values)
            else:
                # Primary shift closing: unique by (user, date) where poster_account_id IS NULL
                # Use SELECT+INSERT/UPDATE to avoid conflict with cafe rows for the same date
                if DB_TYPE == "sqlite":
                    cursor.execute("""
                        SELECT id FROM shift_closings
                        WHERE telegram_user_id = ? AND date = ? AND poster_account_id IS NULL
                    """, (telegram_user_id, date))
                    existing = cursor.fetchone()

                    if existing:
                        update_parts = ', '.join([f'{f} = ?' for f in fields])
                        cursor.execute(f"""
                            UPDATE shift_closings SET {update_parts}, updated_at = CURRENT_TIMESTAMP
                            WHERE telegram_user_id = ? AND date = ? AND poster_account_id IS NULL
                        """, values + [telegram_user_id, date])
                    else:
                        all_fields = ['telegram_user_id', 'date'] + fields
                        placeholders = ', '.join(['?'] * len(all_fields))
                        cursor.execute(f"""
                            INSERT INTO shift_closings ({', '.join(all_fields)}, updated_at)
                            VALUES ({placeholders}, CURRENT_TIMESTAMP)
                        """, [telegram_user_id, date] + values)
                else:
                    cursor.execute("""
                        SELECT id FROM shift_closings
                        WHERE telegram_user_id = %s AND date = %s AND poster_account_id IS NULL
                    """, (telegram_user_id, date))
                    existing = cursor.fetchone()

                    if existing:
                        update_parts = ', '.join([f'{f} = %s' for f in fields])
                        cursor.execute(f"""
                            UPDATE shift_closings SET {update_parts}, updated_at = CURRENT_TIMESTAMP
                            WHERE telegram_user_id = %s AND date = %s AND poster_account_id IS NULL
                        """, values + [telegram_user_id, date])
                    else:
                        all_fields = ['telegram_user_id', 'date'] + fields
                        placeholders = ', '.join(['%s'] * len(all_fields))
                        cursor.execute(f"""
                            INSERT INTO shift_closings ({', '.join(all_fields)}, updated_at)
                            VALUES ({placeholders}, CURRENT_TIMESTAMP)
                        """, [telegram_user_id, date] + values)

            conn.commit()
            conn.close()
            return True

        except Exception as e:
            logger.error(f"Failed to save shift closing: {e}")
            import traceback
            traceback.print_exc()
            return False

    def get_shift_closing(self, telegram_user_id: int, date: str, poster_account_id: int = None) -> Optional[Dict]:
        """Get shift closing data for a specific date"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            placeholder = "?" if DB_TYPE == "sqlite" else "%s"
            if poster_account_id is not None:
                cursor.execute(f"""
                    SELECT * FROM shift_closings
                    WHERE telegram_user_id = {placeholder} AND date = {placeholder}
                    AND poster_account_id = {placeholder}
                """, (telegram_user_id, date, poster_account_id))
            else:
                cursor.execute(f"""
                    SELECT * FROM shift_closings
                    WHERE telegram_user_id = {placeholder} AND date = {placeholder}
                    AND poster_account_id IS NULL
                """, (telegram_user_id, date))

            columns = [desc[0] for desc in cursor.description]
            row = cursor.fetchone()
            conn.close()

            if row:
                return dict(zip(columns, row))
            return None

        except Exception as e:
            logger.error(f"Failed to get shift closing: {e}")
            return None

    def get_shift_closing_dates(self, telegram_user_id: int, limit: int = 30, poster_account_id: int = None) -> list:
        """Get list of dates that have shift closing data"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            placeholder = "?" if DB_TYPE == "sqlite" else "%s"
            limit_placeholder = "?" if DB_TYPE == "sqlite" else "%s"
            if poster_account_id is not None:
                cursor.execute(f"""
                    SELECT date FROM shift_closings
                    WHERE telegram_user_id = {placeholder} AND poster_account_id = {placeholder}
                    ORDER BY date DESC
                    LIMIT {limit_placeholder}
                """, (telegram_user_id, poster_account_id, limit))
            else:
                cursor.execute(f"""
                    SELECT date FROM shift_closings
                    WHERE telegram_user_id = {placeholder} AND poster_account_id IS NULL
                    ORDER BY date DESC
                    LIMIT {limit_placeholder}
                """, (telegram_user_id, limit))

            rows = cursor.fetchall()
            conn.close()
            return [str(row[0]) for row in rows]

        except Exception as e:
            logger.error(f"Failed to get shift closing dates: {e}")
            return []

    def set_transfers_created(self, telegram_user_id: int, date: str, poster_account_id: int = None) -> bool:
        """Mark transfers as created for a shift closing"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            placeholder = "?" if DB_TYPE == "sqlite" else "%s"
            true_val = 1 if DB_TYPE == "sqlite" else True

            if poster_account_id is not None:
                cursor.execute(f"""
                    UPDATE shift_closings SET transfers_created = {placeholder}
                    WHERE telegram_user_id = {placeholder} AND date = {placeholder}
                    AND poster_account_id = {placeholder}
                """, (true_val, telegram_user_id, date, poster_account_id))
            else:
                cursor.execute(f"""
                    UPDATE shift_closings SET transfers_created = {placeholder}
                    WHERE telegram_user_id = {placeholder} AND date = {placeholder}
                    AND poster_account_id IS NULL
                """, (true_val, telegram_user_id, date))

            conn.commit()
            affected = cursor.rowcount
            conn.close()
            return affected > 0

        except Exception as e:
            logger.error(f"Failed to set transfers_created: {e}")
            return False

    def set_cafe_salaries(self, telegram_user_id: int, date: str, poster_account_id: int, salaries_data: str) -> bool:
        """Mark cafe salaries as created and save salary data JSON"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            placeholder = "?" if DB_TYPE == "sqlite" else "%s"
            true_val = 1 if DB_TYPE == "sqlite" else True

            # Check if row exists
            cursor.execute(f"""
                SELECT id FROM shift_closings
                WHERE telegram_user_id = {placeholder} AND date = {placeholder}
                AND poster_account_id = {placeholder}
            """, (telegram_user_id, date, poster_account_id))
            existing = cursor.fetchone()

            if existing:
                cursor.execute(f"""
                    UPDATE shift_closings
                    SET salaries_created = {placeholder}, salaries_data = {placeholder},
                        updated_at = CURRENT_TIMESTAMP
                    WHERE telegram_user_id = {placeholder} AND date = {placeholder}
                    AND poster_account_id = {placeholder}
                """, (true_val, salaries_data, telegram_user_id, date, poster_account_id))
            else:
                # Create a minimal row
                cursor.execute(f"""
                    INSERT INTO shift_closings (telegram_user_id, date, poster_account_id,
                        salaries_created, salaries_data, updated_at)
                    VALUES ({placeholder}, {placeholder}, {placeholder},
                        {placeholder}, {placeholder}, CURRENT_TIMESTAMP)
                """, (telegram_user_id, date, poster_account_id, true_val, salaries_data))

            conn.commit()
            conn.close()
            return True

        except Exception as e:
            logger.error(f"Failed to set cafe salaries: {e}")
            import traceback
            traceback.print_exc()
            return False

    # ==================== Cafe Access Token Methods ====================

    def create_cafe_token(self, telegram_user_id: int, poster_account_id: int, label: str = None) -> str:
        """Create a new cafe access token, returns the token string"""
        import secrets
        token = secrets.token_urlsafe(24)

        conn = self._get_connection()
        cursor = conn.cursor()

        if DB_TYPE == "sqlite":
            cursor.execute("""
                INSERT INTO cafe_access_tokens (token, telegram_user_id, poster_account_id, label)
                VALUES (?, ?, ?, ?)
            """, (token, telegram_user_id, poster_account_id, label))
        else:
            cursor.execute("""
                INSERT INTO cafe_access_tokens (token, telegram_user_id, poster_account_id, label)
                VALUES (%s, %s, %s, %s)
            """, (token, telegram_user_id, poster_account_id, label))

        conn.commit()
        conn.close()
        return token

    def get_cafe_token(self, token: str) -> Optional[Dict]:
        """Resolve a cafe access token to user_id and account info"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            placeholder = "?" if DB_TYPE == "sqlite" else "%s"
            cursor.execute(f"""
                SELECT t.telegram_user_id, t.poster_account_id, t.label,
                       a.account_name, a.poster_token, a.poster_user_id, a.poster_base_url
                FROM cafe_access_tokens t
                JOIN poster_accounts a ON a.id = t.poster_account_id
                WHERE t.token = {placeholder}
            """, (token,))

            columns = [desc[0] for desc in cursor.description]
            row = cursor.fetchone()
            conn.close()

            if row:
                return dict(zip(columns, row))
            return None

        except Exception as e:
            logger.error(f"Failed to get cafe token: {e}")
            return None

    def list_cafe_tokens(self, telegram_user_id: int) -> list:
        """List all cafe access tokens for a user"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            placeholder = "?" if DB_TYPE == "sqlite" else "%s"
            cursor.execute(f"""
                SELECT t.id, t.token, t.label, t.created_at, a.account_name
                FROM cafe_access_tokens t
                JOIN poster_accounts a ON a.id = t.poster_account_id
                WHERE t.telegram_user_id = {placeholder}
                ORDER BY t.created_at DESC
            """, (telegram_user_id,))

            columns = [desc[0] for desc in cursor.description]
            rows = cursor.fetchall()
            conn.close()
            return [dict(zip(columns, row)) for row in rows]

        except Exception as e:
            logger.error(f"Failed to list cafe tokens: {e}")
            return []

    def delete_cafe_token(self, token_id: int, telegram_user_id: int) -> bool:
        """Delete a cafe access token"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            placeholder = "?" if DB_TYPE == "sqlite" else "%s"
            cursor.execute(f"""
                DELETE FROM cafe_access_tokens
                WHERE id = {placeholder} AND telegram_user_id = {placeholder}
            """, (token_id, telegram_user_id))

            conn.commit()
            affected = cursor.rowcount
            conn.close()
            return affected > 0

        except Exception as e:
            logger.error(f"Failed to delete cafe token: {e}")
            return False

    # ==================== Cashier Access Token Methods ====================

    def create_cashier_token(self, telegram_user_id: int, poster_account_id: int, label: str = None) -> str:
        """Create a new cashier access token, returns the token string"""
        import secrets
        token = secrets.token_urlsafe(24)

        conn = self._get_connection()
        cursor = conn.cursor()

        if DB_TYPE == "sqlite":
            cursor.execute("""
                INSERT INTO cashier_access_tokens (token, telegram_user_id, poster_account_id, label)
                VALUES (?, ?, ?, ?)
            """, (token, telegram_user_id, poster_account_id, label))
        else:
            cursor.execute("""
                INSERT INTO cashier_access_tokens (token, telegram_user_id, poster_account_id, label)
                VALUES (%s, %s, %s, %s)
            """, (token, telegram_user_id, poster_account_id, label))

        conn.commit()
        conn.close()
        return token

    def get_cashier_token(self, token: str) -> Optional[Dict]:
        """Resolve a cashier access token to user_id and account info"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            placeholder = "?" if DB_TYPE == "sqlite" else "%s"
            cursor.execute(f"""
                SELECT t.telegram_user_id, t.poster_account_id, t.label,
                       a.account_name, a.poster_token, a.poster_user_id, a.poster_base_url
                FROM cashier_access_tokens t
                JOIN poster_accounts a ON a.id = t.poster_account_id
                WHERE t.token = {placeholder}
            """, (token,))

            columns = [desc[0] for desc in cursor.description]
            row = cursor.fetchone()
            conn.close()

            if row:
                return dict(zip(columns, row))
            return None

        except Exception as e:
            logger.error(f"Failed to get cashier token: {e}")
            return None

    def list_cashier_tokens(self, telegram_user_id: int) -> list:
        """List all cashier access tokens for a user"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            placeholder = "?" if DB_TYPE == "sqlite" else "%s"
            cursor.execute(f"""
                SELECT t.id, t.token, t.label, t.created_at, a.account_name
                FROM cashier_access_tokens t
                JOIN poster_accounts a ON a.id = t.poster_account_id
                WHERE t.telegram_user_id = {placeholder}
                ORDER BY t.created_at DESC
            """, (telegram_user_id,))

            columns = [desc[0] for desc in cursor.description]
            rows = cursor.fetchall()
            conn.close()
            return [dict(zip(columns, row)) for row in rows]

        except Exception as e:
            logger.error(f"Failed to list cashier tokens: {e}")
            return []

    def delete_cashier_token(self, token_id: int, telegram_user_id: int) -> bool:
        """Delete a cashier access token"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            placeholder = "?" if DB_TYPE == "sqlite" else "%s"
            cursor.execute(f"""
                DELETE FROM cashier_access_tokens
                WHERE id = {placeholder} AND telegram_user_id = {placeholder}
            """, (token_id, telegram_user_id))

            conn.commit()
            affected = cursor.rowcount
            conn.close()
            return affected > 0

        except Exception as e:
            logger.error(f"Failed to delete cashier token: {e}")
            return False

    # ==================== Cashier Shift Data Methods ====================

    def save_cashier_shift_data(self, telegram_user_id: int, date: str, data: dict) -> bool:
        """Save or update cashier shift data for a specific date (upsert)"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            fields = [
                'cashier_count', 'cashier_names', 'assistant_start_time',
                'doner_name', 'assistant_name', 'salaries_data', 'salaries_created',
                'wolt', 'halyk', 'cash_bills', 'cash_coins', 'expenses',
                'shift_data_submitted'
            ]

            # Fields that should be text (NULL default) vs numeric (0 default)
            text_fields = ('cashier_names', 'assistant_start_time', 'doner_name', 'assistant_name', 'salaries_data')
            # Boolean fields need special handling for PostgreSQL (BOOLEAN type vs SQLite INTEGER)
            bool_fields = ('salaries_created', 'shift_data_submitted')

            values = []
            for f in fields:
                val = data.get(f)
                if f in bool_fields:
                    # Convert to proper bool for PostgreSQL, int for SQLite
                    if DB_TYPE == "sqlite":
                        values.append(1 if val else 0)
                    else:
                        values.append(bool(val))
                elif f in text_fields:
                    values.append(val)  # None if not provided
                else:
                    values.append(val if val is not None else 0)

            if DB_TYPE == "sqlite":
                placeholders = ', '.join(['?'] * (len(fields) + 2))
                fields_str = ', '.join(['telegram_user_id', 'date'] + fields + ['updated_at'])
                update_parts = ', '.join([f'{f} = excluded.{f}' for f in fields])
                cursor.execute(f"""
                    INSERT INTO cashier_shift_data ({fields_str})
                    VALUES ({placeholders}, CURRENT_TIMESTAMP)
                    ON CONFLICT(telegram_user_id, date)
                    DO UPDATE SET {update_parts}, updated_at = CURRENT_TIMESTAMP
                """, [telegram_user_id, date] + values)
            else:
                placeholders = ', '.join(['%s'] * (len(fields) + 2))
                fields_str = ', '.join(['telegram_user_id', 'date'] + fields + ['updated_at'])
                update_parts = ', '.join([f'{f} = EXCLUDED.{f}' for f in fields])
                cursor.execute(f"""
                    INSERT INTO cashier_shift_data ({fields_str})
                    VALUES ({placeholders}, CURRENT_TIMESTAMP)
                    ON CONFLICT(telegram_user_id, date)
                    DO UPDATE SET {update_parts}, updated_at = CURRENT_TIMESTAMP
                """, [telegram_user_id, date] + values)

            conn.commit()
            conn.close()
            return True

        except Exception as e:
            logger.error(f"Failed to save cashier shift data: {e}")
            import traceback
            traceback.print_exc()
            return False

    def get_cashier_shift_data(self, telegram_user_id: int, date: str) -> Optional[Dict]:
        """Get cashier shift data for a specific date"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            placeholder = "?" if DB_TYPE == "sqlite" else "%s"
            cursor.execute(f"""
                SELECT * FROM cashier_shift_data
                WHERE telegram_user_id = {placeholder} AND date = {placeholder}
            """, (telegram_user_id, date))

            columns = [desc[0] for desc in cursor.description]
            row = cursor.fetchone()
            conn.close()

            if row:
                return dict(zip(columns, row))
            return None

        except Exception as e:
            logger.error(f"Failed to get cashier shift data: {e}")
            return None

    def get_cashier_last_employees(self, telegram_user_id: int) -> Optional[Dict]:
        """Get most recent cashier shift data (for auto-filling names)"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            placeholder = "?" if DB_TYPE == "sqlite" else "%s"
            cursor.execute(f"""
                SELECT cashier_count, cashier_names, assistant_start_time,
                       doner_name, assistant_name
                FROM cashier_shift_data
                WHERE telegram_user_id = {placeholder}
                  AND cashier_names IS NOT NULL
                ORDER BY date DESC
                LIMIT 1
            """, (telegram_user_id,))

            columns = [desc[0] for desc in cursor.description]
            row = cursor.fetchone()
            conn.close()

            if row:
                return dict(zip(columns, row))
            return None

        except Exception as e:
            logger.error(f"Failed to get cashier last employees: {e}")
            return None

    # ==================== Web Users (Auth) Methods ====================

    def create_web_user(self, telegram_user_id: int, username: str, password: str, role: str,
                        label: str = None, poster_account_id: int = None) -> Optional[int]:
        """Create a new web user with bcrypt-hashed password. Returns user id."""
        import bcrypt
        password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            if DB_TYPE == "sqlite":
                cursor.execute("""
                    INSERT INTO web_users (telegram_user_id, username, password_hash, role, label, poster_account_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (telegram_user_id, username, password_hash, role, label, poster_account_id))
                user_id = cursor.lastrowid
            else:
                cursor.execute("""
                    INSERT INTO web_users (telegram_user_id, username, password_hash, role, label, poster_account_id)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (telegram_user_id, username, password_hash, role, label, poster_account_id))
                user_id = cursor.fetchone()[0]

            conn.commit()
            conn.close()
            return user_id

        except Exception as e:
            logger.error(f"Failed to create web user: {e}")
            return None

    def verify_web_user(self, username: str, password: str) -> Optional[Dict]:
        """Verify username/password and return user dict if valid, None otherwise."""
        import bcrypt
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            placeholder = "?" if DB_TYPE == "sqlite" else "%s"
            cursor.execute(f"""
                SELECT id, telegram_user_id, username, password_hash, role, label,
                       poster_account_id, is_active
                FROM web_users
                WHERE username = {placeholder}
            """, (username,))

            columns = [desc[0] for desc in cursor.description]
            row = cursor.fetchone()

            if not row:
                conn.close()
                return None

            user = dict(zip(columns, row))

            # Check active
            is_active = user.get('is_active')
            if is_active == 0 or is_active is False:
                conn.close()
                return None

            # Verify password
            if not bcrypt.checkpw(password.encode('utf-8'), user['password_hash'].encode('utf-8')):
                conn.close()
                return None

            # Update last_login
            cursor.execute(f"""
                UPDATE web_users SET last_login = CURRENT_TIMESTAMP
                WHERE id = {placeholder}
            """, (user['id'],))
            conn.commit()
            conn.close()

            del user['password_hash']
            return user

        except Exception as e:
            logger.error(f"Failed to verify web user: {e}")
            return None

    def get_web_user_by_id(self, user_id: int) -> Optional[Dict]:
        """Get web user by id (for session validation)."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            placeholder = "?" if DB_TYPE == "sqlite" else "%s"
            cursor.execute(f"""
                SELECT id, telegram_user_id, username, role, label,
                       poster_account_id, is_active
                FROM web_users
                WHERE id = {placeholder}
            """, (user_id,))

            columns = [desc[0] for desc in cursor.description]
            row = cursor.fetchone()
            conn.close()

            if row:
                return dict(zip(columns, row))
            return None

        except Exception as e:
            logger.error(f"Failed to get web user: {e}")
            return None

    def list_web_users(self, telegram_user_id: int) -> list:
        """List all web users for a given telegram owner."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            placeholder = "?" if DB_TYPE == "sqlite" else "%s"
            cursor.execute(f"""
                SELECT id, username, role, label, poster_account_id, is_active, created_at, last_login
                FROM web_users
                WHERE telegram_user_id = {placeholder}
                ORDER BY role, username
            """, (telegram_user_id,))

            columns = [desc[0] for desc in cursor.description]
            rows = cursor.fetchall()
            conn.close()
            return [dict(zip(columns, row)) for row in rows]

        except Exception as e:
            logger.error(f"Failed to list web users: {e}")
            return []

    def delete_web_user(self, user_id: int, telegram_user_id: int) -> bool:
        """Delete a web user by id. Only the telegram owner can delete."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            placeholder = "?" if DB_TYPE == "sqlite" else "%s"
            cursor.execute(f"""
                DELETE FROM web_users
                WHERE id = {placeholder} AND telegram_user_id = {placeholder}
            """, (user_id, telegram_user_id))

            conn.commit()
            affected = cursor.rowcount
            conn.close()
            return affected > 0

        except Exception as e:
            logger.error(f"Failed to delete web user: {e}")
            return False

    def reset_web_user_password(self, user_id: int, telegram_user_id: int, new_password: str) -> bool:
        """Reset password for a web user. Only the telegram owner can reset."""
        import bcrypt
        password_hash = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            placeholder = "?" if DB_TYPE == "sqlite" else "%s"
            cursor.execute(f"""
                UPDATE web_users SET password_hash = {placeholder}
                WHERE id = {placeholder} AND telegram_user_id = {placeholder}
            """, (password_hash, user_id, telegram_user_id))

            conn.commit()
            affected = cursor.rowcount
            conn.close()
            return affected > 0

        except Exception as e:
            logger.error(f"Failed to reset web user password: {e}")
            return False

    def get_web_user_poster_info(self, web_user_id: int) -> Optional[Dict]:
        """Get poster account info for a web user (for session-based cafe/cashier routes)."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            placeholder = "?" if DB_TYPE == "sqlite" else "%s"
            cursor.execute(f"""
                SELECT u.id, u.telegram_user_id, u.role, u.poster_account_id, u.label,
                       a.account_name, a.poster_token, a.poster_user_id, a.poster_base_url
                FROM web_users u
                LEFT JOIN poster_accounts a ON a.id = u.poster_account_id
                WHERE u.id = {placeholder}
            """, (web_user_id,))

            columns = [desc[0] for desc in cursor.description]
            row = cursor.fetchone()
            conn.close()

            if row:
                return dict(zip(columns, row))
            return None

        except Exception as e:
            logger.error(f"Failed to get web user poster info: {e}")
            return None

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

    def update_supply_draft(self, supply_draft_id: int, telegram_user_id: int = None, **kwargs) -> bool:
        """
        Обновить черновик поставки

        Args:
            supply_draft_id: ID черновика
            telegram_user_id: ID владельца (если передан — проверяет принадлежность)
            **kwargs: Поля для обновления (supplier_name, supplier_id, invoice_date, total_sum, account_id, source)
        """
        if not kwargs:
            return False

        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            if DB_TYPE == "sqlite":
                set_clause = ", ".join([f"{k} = ?" for k in kwargs.keys()])
                if telegram_user_id is not None:
                    query = f"UPDATE supply_drafts SET {set_clause} WHERE id = ? AND telegram_user_id = ?"
                    cursor.execute(query, list(kwargs.values()) + [supply_draft_id, telegram_user_id])
                else:
                    query = f"UPDATE supply_drafts SET {set_clause} WHERE id = ?"
                    cursor.execute(query, list(kwargs.values()) + [supply_draft_id])
            else:
                set_clause = ", ".join([f"{k} = %s" for k in kwargs.keys()])
                if telegram_user_id is not None:
                    query = f"UPDATE supply_drafts SET {set_clause} WHERE id = %s AND telegram_user_id = %s"
                    cursor.execute(query, list(kwargs.values()) + [supply_draft_id, telegram_user_id])
                else:
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

    def delete_supply_draft_item(self, item_id: int, telegram_user_id: int = None) -> bool:
        """Удалить позицию из черновика поставки. Если telegram_user_id передан — проверяет через supply_drafts."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            if DB_TYPE == "sqlite":
                if telegram_user_id is not None:
                    cursor.execute("DELETE FROM supply_draft_items WHERE id = ? AND supply_draft_id IN (SELECT id FROM supply_drafts WHERE telegram_user_id = ?)", (item_id, telegram_user_id))
                else:
                    cursor.execute("DELETE FROM supply_draft_items WHERE id = ?", (item_id,))
            else:
                if telegram_user_id is not None:
                    cursor.execute("DELETE FROM supply_draft_items WHERE id = %s AND supply_draft_id IN (SELECT id FROM supply_drafts WHERE telegram_user_id = %s)", (item_id, telegram_user_id))
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

    def update_supply_draft_item(self, item_id: int, telegram_user_id: int = None, **kwargs) -> bool:
        """
        Обновить позицию в черновике поставки

        Args:
            item_id: ID позиции
            telegram_user_id: ID владельца (если передан — проверяет через supply_drafts)
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
                if telegram_user_id is not None:
                    query = f"UPDATE supply_draft_items SET {set_clause} WHERE id = ? AND supply_draft_id IN (SELECT id FROM supply_drafts WHERE telegram_user_id = ?)"
                    cursor.execute(query, list(kwargs.values()) + [item_id, telegram_user_id])
                else:
                    query = f"UPDATE supply_draft_items SET {set_clause} WHERE id = ?"
                    cursor.execute(query, list(kwargs.values()) + [item_id])
            else:
                set_clause = ", ".join([f"{k} = %s" for k in kwargs.keys()])
                if telegram_user_id is not None:
                    query = f"UPDATE supply_draft_items SET {set_clause} WHERE id = %s AND supply_draft_id IN (SELECT id FROM supply_drafts WHERE telegram_user_id = %s)"
                    cursor.execute(query, list(kwargs.values()) + [item_id, telegram_user_id])
                else:
                    query = f"UPDATE supply_draft_items SET {set_clause} WHERE id = %s"
                    cursor.execute(query, list(kwargs.values()) + [item_id])

            conn.commit()
            conn.close()
            return True

        except Exception as e:
            logger.error(f"Failed to update supply draft item: {e}")
            return False

    def delete_supply_draft(self, supply_draft_id: int, telegram_user_id: int = None) -> bool:
        """Удалить черновик поставки (вместе с позициями благодаря CASCADE). Если telegram_user_id передан — проверяет принадлежность."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            if DB_TYPE == "sqlite":
                if telegram_user_id is not None:
                    cursor.execute("DELETE FROM supply_drafts WHERE id = ? AND telegram_user_id = ?", (supply_draft_id, telegram_user_id))
                else:
                    cursor.execute("DELETE FROM supply_drafts WHERE id = ?", (supply_draft_id,))
            else:
                if telegram_user_id is not None:
                    cursor.execute("DELETE FROM supply_drafts WHERE id = %s AND telegram_user_id = %s", (supply_draft_id, telegram_user_id))
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
