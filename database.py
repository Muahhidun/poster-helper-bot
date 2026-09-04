"""Database management for multi-tenant bot - supports both SQLite and PostgreSQL"""
import os
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Optional, Dict
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# Detect database type based on environment
DATABASE_URL = os.getenv("DATABASE_URL")  # Railway provides this for PostgreSQL

if DATABASE_URL:
    # PostgreSQL on Railway
    import psycopg2
    from psycopg2.extras import RealDictCursor
    from psycopg2 import pool
    DB_TYPE = "postgresql"
    logger.info("Using PostgreSQL database")
    
    try:
        DB_POOL = pool.ThreadedConnectionPool(1, 20, dsn=DATABASE_URL)
        logger.info("Initialized PostgreSQL connection pool (min=1, max=20)")
    except Exception as e:
        logger.error(f"Failed to initialize connection pool: {e}")
        DB_POOL = None
else:
    # SQLite locally
    import sqlite3
    from config import DATABASE_PATH
    DB_TYPE = "sqlite"
    DB_POOL = None
    logger.info(f"Using SQLite database at {DATABASE_PATH}")


def _hash_web_password(password: str) -> str:
    """Hash password with bcrypt if available, otherwise fallback to werkzeug.security."""
    try:
        import bcrypt
        return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    except ImportError:
        from werkzeug.security import generate_password_hash
        return generate_password_hash(password)


def _check_web_password(password: str, password_hash: str) -> bool:
    """Verify password using bcrypt or werkzeug.security."""
    try:
        if password_hash.startswith('pbkdf2:') or password_hash.startswith('scrypt:'):
            from werkzeug.security import check_password_hash
            return check_password_hash(password_hash, password)
        import bcrypt
        return bcrypt.checkpw(password.encode('utf-8'), password_hash.encode('utf-8'))
    except (ImportError, Exception):
        from werkzeug.security import check_password_hash
        return check_password_hash(password_hash, password)


class _ManagedConnection:
    """Connection wrapper that ensures cleanup even if conn.close() is forgotten.

    - Safe double-close (no error if close() called twice)
    - __del__ catches leaked connections when garbage collected
    - In CPython, reference counting ensures immediate cleanup when function returns
    - Supports context manager protocol (with statement)
    """

    def __init__(self, conn, pool=None):
        self._conn = conn
        self._closed = False
        self._pool = pool

    def cursor(self, *args, **kwargs):
        return self._conn.cursor(*args, **kwargs)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        if not self._closed:
            self._closed = True
            if self._pool:
                self._pool.putconn(self._conn)
            else:
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
    DB_TYPE = DB_TYPE

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
            if DB_POOL:
                try:
                    conn = DB_POOL.getconn()
                    return _ManagedConnection(conn, pool=DB_POOL)
                except Exception as e:
                    logger.error(f"Pool error: {e}. Falling back to standard connection.")
                    return _ManagedConnection(psycopg2.connect(self.db_url))
            else:
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

        # Run migration for account balance snapshots (15 days history)
        self._migrate_account_snapshots()

        # Reliable 02:00 capital snapshots. Kept separate from the legacy table
        # because legacy rows were produced by an incorrect transaction formula.
        self._migrate_capital_balance_snapshots()

        # Verified daily business analytics built directly from both Poster accounts.
        self._migrate_business_analytics()

        # Run migration to fix shift_closings UNIQUE constraint (cafe + main same date)
        self._migrate_shift_closings_fix_unique()

        # Run migration to add salaries columns to shift_closings (cafe salaries)
        self._migrate_cafe_salaries()

        # Run migration to create daily_transactions_log table
        self._migrate_daily_transactions_log()

        # Run migration to create daily_transactions_config table
        self._migrate_daily_transactions_config()

        # Run migration to add packaging rules and habits
        self._migrate_packaging_and_habits()

        # Run migration to add account_name to packaging rules and habits
        self._migrate_packaging_and_habits_account()

        # Run migration for assistant chat history
        self._migrate_assistant_chat()

        # Durable, sequential processing for bursts of WhatsApp invoices.
        self._migrate_whatsapp_queue()

        # Run migration for assistant memory
        self._migrate_assistant_memory()
        self._migrate_assistant_memory_versions()

        # Run migration to add wedrink_sales column to shift_closings
        self._migrate_shift_closings_wedrink()

        # Run migration for purchase sheet tables
        self._migrate_purchase_sheet()

        # Explicit cross-account supplier identity table. Supplier IDs belong
        # to one Poster account and cannot be reused in another account.
        self._migrate_supplier_account_mappings()

        # Manual draft corrections are one-off fixes, not reusable business
        # rules. Remove rows created by the retired auto-learning behavior.
        self._remove_auto_learned_corrections()

        # Clean invalid or corrupted aliases
        self._clean_invalid_aliases()

    def _remove_auto_learned_corrections(self):
        """Delete aliases, price habits, and coefficients learned from edits."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            statements = [
                """DELETE FROM ingredient_aliases
                   WHERE COALESCE(notes, '') LIKE 'Авто-сохранено при ручной привязке%'""",
                """DELETE FROM ingredient_aliases
                   WHERE COALESCE(notes, '') IN (
                       'Auto-learned from user selection',
                       'Auto-learned from user correction'
                   )""",
                """DELETE FROM supplier_aliases
                   WHERE COALESCE(notes, '') LIKE 'Авто-обучено при редактировании черновика%'""",
                """DELETE FROM ingredient_packaging_rules
                   WHERE COALESCE(notes, '') LIKE 'Авто-изучено:%'""",
                """DELETE FROM ingredient_habits
                   WHERE COALESCE(notes, '') LIKE 'Изучено из ручного ввода цены%'""",
            ]
            deleted = 0
            for statement in statements:
                cursor.execute(statement)
                deleted += max(cursor.rowcount or 0, 0)
            conn.commit()
            conn.close()
            if deleted:
                logger.info("Removed %s legacy auto-learned correction rows", deleted)
        except Exception as e:
            logger.error(f"Failed to remove auto-learned corrections: {e}")

    def _migrate_supplier_account_mappings(self):
        """Create canonical supplier-to-account ID mappings."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            if DB_TYPE == "sqlite":
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS supplier_account_mappings (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        telegram_user_id INTEGER NOT NULL,
                        canonical_name TEXT NOT NULL,
                        poster_account_id INTEGER NOT NULL,
                        poster_account_name TEXT NOT NULL,
                        poster_supplier_id INTEGER NOT NULL,
                        poster_supplier_name TEXT NOT NULL,
                        confidence REAL NOT NULL DEFAULT 100,
                        source TEXT NOT NULL DEFAULT 'manual',
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(telegram_user_id, canonical_name, poster_account_id),
                        UNIQUE(telegram_user_id, poster_account_id, poster_supplier_id)
                    )
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_supplier_account_mapping_lookup
                    ON supplier_account_mappings(telegram_user_id, poster_account_id, canonical_name)
                """)
            else:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS supplier_account_mappings (
                        id SERIAL PRIMARY KEY,
                        telegram_user_id BIGINT NOT NULL,
                        canonical_name TEXT NOT NULL,
                        poster_account_id INTEGER NOT NULL,
                        poster_account_name TEXT NOT NULL,
                        poster_supplier_id INTEGER NOT NULL,
                        poster_supplier_name TEXT NOT NULL,
                        confidence DOUBLE PRECISION NOT NULL DEFAULT 100,
                        source TEXT NOT NULL DEFAULT 'manual',
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(telegram_user_id, canonical_name, poster_account_id),
                        UNIQUE(telegram_user_id, poster_account_id, poster_supplier_id)
                    )
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_supplier_account_mapping_lookup
                    ON supplier_account_mappings(telegram_user_id, poster_account_id, canonical_name)
                """)
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Failed to migrate supplier account mappings: {e}")

    def _clean_invalid_aliases(self):
        """Clean up mistakenly saved or corrupt aliases"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            if DB_TYPE == "sqlite":
                cursor.execute("""
                    DELETE FROM ingredient_aliases
                    WHERE (alias_text LIKE '%фьюс%' OR alias_text LIKE '%fuse%')
                      AND (poster_item_name LIKE '%палочк%' OR poster_item_name LIKE '%перреро%')
                """)
            else:
                cursor.execute("""
                    DELETE FROM ingredient_aliases
                    WHERE (alias_text ILIKE '%фьюс%' OR alias_text ILIKE '%fuse%')
                      AND (poster_item_name ILIKE '%палочк%' OR poster_item_name ILIKE '%перреро%')
                """)
            conn.commit()
            conn.close()
            logger.info("✅ Cleaned invalid aliases from database")
        except Exception as e:
            logger.warning(f"Error during alias cleanup: {e}")

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

            for col_sql in [
                ("salaries_created", "ALTER TABLE shift_closings ADD COLUMN salaries_created INTEGER DEFAULT 0"
                 if DB_TYPE == "sqlite" else
                 "ALTER TABLE shift_closings ADD COLUMN salaries_created BOOLEAN DEFAULT FALSE"),
                ("salaries_data", "ALTER TABLE shift_closings ADD COLUMN salaries_data TEXT DEFAULT NULL"),
            ]:
                try:
                    if DB_TYPE != "sqlite":
                        cursor.execute("SAVEPOINT migration_sp")
                    cursor.execute(col_sql[1])
                    if DB_TYPE != "sqlite":
                        cursor.execute("RELEASE SAVEPOINT migration_sp")
                    logger.info(f"✅ Cafe salaries migration: added {col_sql[0]} to shift_closings")
                except Exception:
                    if DB_TYPE != "sqlite":
                        cursor.execute("ROLLBACK TO SAVEPOINT migration_sp")

            conn.commit()
            conn.close()
            logger.info("✅ Cafe salaries migration: completed")

        except Exception as e:
            logger.error(f"Cafe salaries migration error: {e}")

    def _migrate_daily_transactions_log(self):
        """Create daily_transactions_log table to track when daily transactions were created per date"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            if DB_TYPE == "sqlite":
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS daily_transactions_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        telegram_user_id INTEGER NOT NULL,
                        date TEXT NOT NULL,
                        count INTEGER DEFAULT 0,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(telegram_user_id, date)
                    )
                """)
            else:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS daily_transactions_log (
                        id SERIAL PRIMARY KEY,
                        telegram_user_id BIGINT NOT NULL,
                        date DATE NOT NULL,
                        count INTEGER DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(telegram_user_id, date)
                    )
                """)

            conn.commit()
            conn.close()
            logger.info("✅ daily_transactions_log table: ready")

        except Exception as e:
            logger.error(f"daily_transactions_log migration error: {e}")

    def _migrate_daily_transactions_config(self):
        """Create daily_transactions_config table for user-editable daily transaction rules"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            if DB_TYPE == "sqlite":
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS daily_transactions_config (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        telegram_user_id INTEGER NOT NULL,
                        account_name TEXT NOT NULL DEFAULT 'Pizzburg',
                        transaction_type INTEGER NOT NULL DEFAULT 0,
                        category_id INTEGER NOT NULL DEFAULT 0,
                        category_name TEXT DEFAULT '',
                        account_from_id INTEGER NOT NULL,
                        account_from_name TEXT DEFAULT '',
                        account_to_id INTEGER,
                        account_to_name TEXT DEFAULT '',
                        amount INTEGER NOT NULL DEFAULT 1,
                        comment TEXT DEFAULT '',
                        is_enabled INTEGER NOT NULL DEFAULT 1,
                        sort_order INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                """)
            else:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS daily_transactions_config (
                        id SERIAL PRIMARY KEY,
                        telegram_user_id BIGINT NOT NULL,
                        account_name TEXT NOT NULL DEFAULT 'Pizzburg',
                        transaction_type INTEGER NOT NULL DEFAULT 0,
                        category_id INTEGER NOT NULL DEFAULT 0,
                        category_name TEXT DEFAULT '',
                        account_from_id INTEGER NOT NULL,
                        account_from_name TEXT DEFAULT '',
                        account_to_id INTEGER,
                        account_to_name TEXT DEFAULT '',
                        amount INTEGER NOT NULL DEFAULT 1,
                        comment TEXT DEFAULT '',
                        is_enabled INTEGER NOT NULL DEFAULT 1,
                        sort_order INTEGER NOT NULL DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)

            conn.commit()
            conn.close()
            logger.info("✅ daily_transactions_config table: ready")

        except Exception as e:
            logger.error(f"daily_transactions_config migration error: {e}")

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
            # 3. Add kaspi_pizzburg column to shift_closings (for Cafe: deliveries via Pizzburg couriers)
            for col_name, col_sql in [
                ("poster_account_id", "ALTER TABLE shift_closings ADD COLUMN poster_account_id INTEGER DEFAULT NULL"),
                ("kaspi_pizzburg", "ALTER TABLE shift_closings ADD COLUMN kaspi_pizzburg REAL DEFAULT 0"),
            ]:
                try:
                    if DB_TYPE != "sqlite":
                        cursor.execute("SAVEPOINT migration_sp")
                    cursor.execute(col_sql)
                    if DB_TYPE != "sqlite":
                        cursor.execute("RELEASE SAVEPOINT migration_sp")
                    logger.info(f"✅ Cafe migration: added {col_name} to shift_closings")
                except Exception:
                    if DB_TYPE != "sqlite":
                        cursor.execute("ROLLBACK TO SAVEPOINT migration_sp")

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
                if DB_TYPE != "sqlite":
                    cursor.execute("SAVEPOINT migration_sp")
                if DB_TYPE == "sqlite":
                    cursor.execute("ALTER TABLE shift_closings ADD COLUMN transfers_created INTEGER DEFAULT 0")
                else:
                    cursor.execute("ALTER TABLE shift_closings ADD COLUMN transfers_created BOOLEAN DEFAULT FALSE")
                if DB_TYPE != "sqlite":
                    cursor.execute("RELEASE SAVEPOINT migration_sp")
                logger.info("✅ Cashier migration: added transfers_created to shift_closings")
            except Exception:
                if DB_TYPE != "sqlite":
                    cursor.execute("ROLLBACK TO SAVEPOINT migration_sp")

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

    def _migrate_account_snapshots(self):
        """Create account_balance_snapshots table for 15-day account history and analytics"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            if DB_TYPE == "sqlite":
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS account_balance_snapshots (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        telegram_user_id INTEGER NOT NULL,
                        date TEXT NOT NULL,
                        account_key TEXT NOT NULL,
                        account_name TEXT,
                        balance REAL NOT NULL DEFAULT 0,
                        net_change REAL DEFAULT 0,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(telegram_user_id, date, account_key),
                        FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id) ON DELETE CASCADE
                    )
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_acc_snapshots_user_date
                    ON account_balance_snapshots(telegram_user_id, date)
                """)
            else:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS account_balance_snapshots (
                        id SERIAL PRIMARY KEY,
                        telegram_user_id BIGINT NOT NULL,
                        date DATE NOT NULL,
                        account_key TEXT NOT NULL,
                        account_name TEXT,
                        balance DECIMAL(12,2) NOT NULL DEFAULT 0,
                        net_change DECIMAL(12,2) DEFAULT 0,
                        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(telegram_user_id, date, account_key),
                        FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id) ON DELETE CASCADE
                    )
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_acc_snapshots_user_date
                    ON account_balance_snapshots(telegram_user_id, date)
                """)

            conn.commit()
            conn.close()
            logger.info("✅ Account balance snapshots migration: completed")

        except Exception as e:
            logger.error(f"Account balance snapshots migration error: {e}")

    def _migrate_capital_balance_snapshots(self):
        """Create the verified, completed-day capital snapshot table."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            if DB_TYPE == "sqlite":
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS capital_balance_snapshots (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        telegram_user_id INTEGER NOT NULL,
                        date TEXT NOT NULL,
                        account_key TEXT NOT NULL,
                        account_name TEXT,
                        balance REAL NOT NULL,
                        net_change REAL NOT NULL DEFAULT 0,
                        cutoff_at TEXT NOT NULL,
                        metadata_json TEXT,
                        captured_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(telegram_user_id, date, account_key),
                        FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id) ON DELETE CASCADE
                    )
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_capital_snapshots_user_date
                    ON capital_balance_snapshots(telegram_user_id, date)
                """)
            else:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS capital_balance_snapshots (
                        id SERIAL PRIMARY KEY,
                        telegram_user_id BIGINT NOT NULL,
                        date DATE NOT NULL,
                        account_key TEXT NOT NULL,
                        account_name TEXT,
                        balance DECIMAL(14,2) NOT NULL,
                        net_change DECIMAL(14,2) NOT NULL DEFAULT 0,
                        cutoff_at TIMESTAMPTZ NOT NULL,
                        metadata_json TEXT,
                        captured_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(telegram_user_id, date, account_key),
                        FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id) ON DELETE CASCADE
                    )
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_capital_snapshots_user_date
                    ON capital_balance_snapshots(telegram_user_id, date)
                """)

            conn.commit()
            conn.close()
            logger.info("✅ Capital balance snapshots migration: completed")
        except Exception as e:
            logger.error(f"Capital balance snapshots migration error: {e}")

    def _migrate_business_analytics(self):
        """Create reproducible daily metrics and generated analyst reports."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            if DB_TYPE == "sqlite":
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS business_daily_metrics (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        telegram_user_id INTEGER NOT NULL,
                        metric_date TEXT NOT NULL,
                        store_id TEXT NOT NULL,
                        store_name TEXT NOT NULL,
                        revenue REAL NOT NULL DEFAULT 0,
                        checks INTEGER NOT NULL DEFAULT 0,
                        average_check REAL NOT NULL DEFAULT 0,
                        expenses REAL NOT NULL DEFAULT 0,
                        supplies REAL NOT NULL DEFAULT 0,
                        non_supply_expenses REAL NOT NULL DEFAULT 0,
                        profit_withdrawals REAL NOT NULL DEFAULT 0,
                        capital_balance REAL,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(telegram_user_id, metric_date, store_id),
                        FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id) ON DELETE CASCADE
                    )
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS business_analytics_reports (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        telegram_user_id INTEGER NOT NULL,
                        report_date TEXT NOT NULL,
                        status TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        ai_commentary_json TEXT,
                        source_status_json TEXT,
                        generated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        telegram_sent_at TEXT,
                        UNIQUE(telegram_user_id, report_date),
                        FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id) ON DELETE CASCADE
                    )
                """)
            else:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS business_daily_metrics (
                        id SERIAL PRIMARY KEY,
                        telegram_user_id BIGINT NOT NULL,
                        metric_date DATE NOT NULL,
                        store_id TEXT NOT NULL,
                        store_name TEXT NOT NULL,
                        revenue DECIMAL(16,2) NOT NULL DEFAULT 0,
                        checks INTEGER NOT NULL DEFAULT 0,
                        average_check DECIMAL(16,2) NOT NULL DEFAULT 0,
                        expenses DECIMAL(16,2) NOT NULL DEFAULT 0,
                        supplies DECIMAL(16,2) NOT NULL DEFAULT 0,
                        non_supply_expenses DECIMAL(16,2) NOT NULL DEFAULT 0,
                        profit_withdrawals DECIMAL(16,2) NOT NULL DEFAULT 0,
                        capital_balance DECIMAL(16,2),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(telegram_user_id, metric_date, store_id),
                        FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id) ON DELETE CASCADE
                    )
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS business_analytics_reports (
                        id SERIAL PRIMARY KEY,
                        telegram_user_id BIGINT NOT NULL,
                        report_date DATE NOT NULL,
                        status TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        ai_commentary_json TEXT,
                        source_status_json TEXT,
                        generated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        telegram_sent_at TIMESTAMPTZ,
                        UNIQUE(telegram_user_id, report_date),
                        FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id) ON DELETE CASCADE
                    )
                """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_business_metrics_user_date
                ON business_daily_metrics(telegram_user_id, metric_date)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_business_reports_user_date
                ON business_analytics_reports(telegram_user_id, report_date)
            """)
            conn.commit()
            conn.close()
            logger.info("✅ Business analytics migrations completed")
        except Exception as e:
            logger.error(f"Business analytics migration error: {e}")

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

    # === Ingredient Packaging Rules & Habits Methods ===

    def _migrate_packaging_and_habits(self):
        """Add columns to supply_draft_items and create packaging_rules and ingredient_habits tables"""
        # 1. Add columns to supply_draft_items
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            if DB_TYPE == "sqlite":
                try:
                    cursor.execute("ALTER TABLE supply_draft_items ADD COLUMN parsed_quantity REAL")
                except:
                    pass
                try:
                    cursor.execute("ALTER TABLE supply_draft_items ADD COLUMN parsed_unit TEXT")
                except:
                    pass
                try:
                    cursor.execute("ALTER TABLE supply_draft_items ADD COLUMN parsed_price_per_unit REAL")
                except:
                    pass
            else:
                cursor.execute("ALTER TABLE supply_draft_items ADD COLUMN IF NOT EXISTS parsed_quantity DECIMAL(10,3)")
                cursor.execute("ALTER TABLE supply_draft_items ADD COLUMN IF NOT EXISTS parsed_unit TEXT")
                cursor.execute("ALTER TABLE supply_draft_items ADD COLUMN IF NOT EXISTS parsed_price_per_unit DECIMAL(12,2)")
            conn.commit()
            conn.close()
            logger.info("✅ supply_draft_items migration: added parsed_quantity, parsed_unit, parsed_price_per_unit")
        except Exception as e:
            logger.error(f"❌ Failed to migrate supply_draft_items: {e}")

        # 2. Create ingredient_packaging_rules table
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            if DB_TYPE == "sqlite":
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS ingredient_packaging_rules (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        telegram_user_id INTEGER NOT NULL,
                        account_name TEXT NOT NULL DEFAULT '',
                        poster_ingredient_id INTEGER NOT NULL,
                        original_unit TEXT NOT NULL,
                        coefficient REAL NOT NULL,
                        target_unit TEXT NOT NULL DEFAULT 'кг',
                        notes TEXT,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(telegram_user_id, account_name, poster_ingredient_id, original_unit),
                        FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id) ON DELETE CASCADE
                    )
                """)
            else:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS ingredient_packaging_rules (
                        id SERIAL PRIMARY KEY,
                        telegram_user_id BIGINT NOT NULL,
                        account_name VARCHAR(255) NOT NULL DEFAULT '',
                        poster_ingredient_id INTEGER NOT NULL,
                        original_unit TEXT NOT NULL,
                        coefficient REAL NOT NULL,
                        target_unit TEXT NOT NULL DEFAULT 'кг',
                        notes TEXT,
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(telegram_user_id, account_name, poster_ingredient_id, original_unit),
                        FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id) ON DELETE CASCADE
                    )
                """)
            conn.commit()
            conn.close()
            logger.info("✅ Created ingredient_packaging_rules table")
        except Exception as e:
            logger.error(f"❌ Failed to create ingredient_packaging_rules table: {e}")

        # 3. Create ingredient_habits table
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            if DB_TYPE == "sqlite":
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS ingredient_habits (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        telegram_user_id INTEGER NOT NULL,
                        account_name TEXT NOT NULL DEFAULT '',
                        poster_ingredient_id INTEGER NOT NULL,
                        default_price REAL,
                        default_quantity REAL,
                        notes TEXT,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(telegram_user_id, account_name, poster_ingredient_id),
                        FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id) ON DELETE CASCADE
                    )
                """)
            else:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS ingredient_habits (
                        id SERIAL PRIMARY KEY,
                        telegram_user_id BIGINT NOT NULL,
                        account_name VARCHAR(255) NOT NULL DEFAULT '',
                        poster_ingredient_id INTEGER NOT NULL,
                        default_price DECIMAL(12,2),
                        default_quantity DECIMAL(10,3),
                        notes TEXT,
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(telegram_user_id, account_name, poster_ingredient_id),
                        FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id) ON DELETE CASCADE
                    )
                """)
            conn.commit()
            conn.close()
            logger.info("✅ Created ingredient_habits table")
        except Exception as e:
            logger.error(f"❌ Failed to create ingredient_habits table: {e}")

    def _migrate_packaging_and_habits_account(self):
        """Add account_name column and update UNIQUE constraints on ingredient_packaging_rules and ingredient_habits tables"""
        # 1. Update ingredient_packaging_rules
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            # Check if column exists
            has_col = False
            if DB_TYPE == "sqlite":
                cursor.execute("PRAGMA table_info(ingredient_packaging_rules)")
                cols = [r[1] for r in cursor.fetchall()]
                has_col = "account_name" in cols
            else:
                cursor.execute("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name = 'ingredient_packaging_rules' AND column_name = 'account_name'
                """)
                has_col = bool(cursor.fetchone())
                
            if not has_col:
                logger.info("⏳ Migrating ingredient_packaging_rules: adding account_name and updating UNIQUE constraint...")
                if DB_TYPE == "sqlite":
                    cursor.execute("ALTER TABLE ingredient_packaging_rules RENAME TO old_ingredient_packaging_rules")
                    cursor.execute("""
                        CREATE TABLE ingredient_packaging_rules (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            telegram_user_id INTEGER NOT NULL,
                            account_name TEXT NOT NULL DEFAULT '',
                            poster_ingredient_id INTEGER NOT NULL,
                            original_unit TEXT NOT NULL,
                            coefficient REAL NOT NULL,
                            target_unit TEXT NOT NULL DEFAULT 'кг',
                            notes TEXT,
                            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            UNIQUE(telegram_user_id, account_name, poster_ingredient_id, original_unit),
                            FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id) ON DELETE CASCADE
                        )
                    """)
                    cursor.execute("""
                        INSERT INTO ingredient_packaging_rules (
                            id, telegram_user_id, poster_ingredient_id, original_unit,
                            coefficient, target_unit, notes, created_at
                        )
                        SELECT id, telegram_user_id, poster_ingredient_id, original_unit,
                               coefficient, target_unit, notes, created_at
                        FROM old_ingredient_packaging_rules
                    """)
                    cursor.execute("DROP TABLE old_ingredient_packaging_rules")
                else:
                    # Find constraint names
                    cursor.execute("""
                        SELECT conname 
                        FROM pg_constraint 
                        WHERE conrelid = 'ingredient_packaging_rules'::regclass AND contype = 'u'
                    """)
                    rows = cursor.fetchall()
                    for row in rows:
                        conname = row[0]
                        cursor.execute(f"ALTER TABLE ingredient_packaging_rules DROP CONSTRAINT {conname}")
                        
                    cursor.execute("ALTER TABLE ingredient_packaging_rules ADD COLUMN account_name VARCHAR(255) NOT NULL DEFAULT ''")
                    cursor.execute("""
                        ALTER TABLE ingredient_packaging_rules 
                        ADD CONSTRAINT ingredient_packaging_rules_user_acc_ing_unit_key 
                        UNIQUE(telegram_user_id, account_name, poster_ingredient_id, original_unit)
                    """)
                conn.commit()
                logger.info("✅ Migrated ingredient_packaging_rules successfully")
            conn.close()
        except Exception as e:
            logger.error(f"❌ Failed to migrate ingredient_packaging_rules: {e}")

        # 2. Update ingredient_habits
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            # Check if column exists
            has_col = False
            if DB_TYPE == "sqlite":
                cursor.execute("PRAGMA table_info(ingredient_habits)")
                cols = [r[1] for r in cursor.fetchall()]
                has_col = "account_name" in cols
            else:
                cursor.execute("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name = 'ingredient_habits' AND column_name = 'account_name'
                """)
                has_col = bool(cursor.fetchone())
                
            if not has_col:
                logger.info("⏳ Migrating ingredient_habits: adding account_name and updating UNIQUE constraint...")
                if DB_TYPE == "sqlite":
                    cursor.execute("ALTER TABLE ingredient_habits RENAME TO old_ingredient_habits")
                    cursor.execute("""
                        CREATE TABLE ingredient_habits (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            telegram_user_id INTEGER NOT NULL,
                            account_name TEXT NOT NULL DEFAULT '',
                            poster_ingredient_id INTEGER NOT NULL,
                            default_price REAL,
                            default_quantity REAL,
                            notes TEXT,
                            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            UNIQUE(telegram_user_id, account_name, poster_ingredient_id),
                            FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id) ON DELETE CASCADE
                        )
                    """)
                    cursor.execute("""
                        INSERT INTO ingredient_habits (
                            id, telegram_user_id, poster_ingredient_id, default_price,
                            default_quantity, notes, created_at
                        )
                        SELECT id, telegram_user_id, poster_ingredient_id, default_price,
                               default_quantity, notes, created_at
                        FROM old_ingredient_habits
                    """)
                    cursor.execute("DROP TABLE old_ingredient_habits")
                else:
                    # Find constraint names
                    cursor.execute("""
                        SELECT conname 
                        FROM pg_constraint 
                        WHERE conrelid = 'ingredient_habits'::regclass AND contype = 'u'
                    """)
                    rows = cursor.fetchall()
                    for row in rows:
                        conname = row[0]
                        cursor.execute(f"ALTER TABLE ingredient_habits DROP CONSTRAINT {conname}")
                        
                    cursor.execute("ALTER TABLE ingredient_habits ADD COLUMN account_name VARCHAR(255) NOT NULL DEFAULT ''")
                    cursor.execute("""
                        ALTER TABLE ingredient_habits 
                        ADD CONSTRAINT ingredient_habits_user_acc_ing_key 
                        UNIQUE(telegram_user_id, account_name, poster_ingredient_id)
                    """)
                conn.commit()
                logger.info("✅ Migrated ingredient_habits successfully")
            conn.close()
        except Exception as e:
            logger.error(f"❌ Failed to migrate ingredient_habits: {e}")

    def get_packaging_rules(self, telegram_user_id: int) -> list:
        """Get all ingredient packaging rules for a user"""
        try:
            conn = self._get_connection()
            if DB_TYPE == "sqlite":
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, account_name, poster_ingredient_id, original_unit, coefficient, target_unit, notes, created_at
                    FROM ingredient_packaging_rules
                    WHERE telegram_user_id = ?
                """, (telegram_user_id,))
                rows = cursor.fetchall()
                columns = [desc[0] for desc in cursor.description]
                res = [dict(zip(columns, row)) for row in rows]
            else:
                cursor = conn.cursor(cursor_factory=RealDictCursor)
                cursor.execute("""
                    SELECT id, account_name, poster_ingredient_id, original_unit, coefficient, target_unit, notes, created_at
                    FROM ingredient_packaging_rules
                    WHERE telegram_user_id = %s
                """, (telegram_user_id,))
                res = [dict(row) for row in cursor.fetchall()]
            conn.close()
            return res
        except Exception as e:
            logger.error(f"Failed to get packaging rules: {e}")
            return []

    def add_packaging_rule(
        self,
        telegram_user_id: int,
        poster_ingredient_id: int,
        original_unit: str,
        coefficient: float,
        target_unit: str = 'кг',
        notes: str = '',
        account_name: str = ''
    ) -> bool:
        """Add or update an ingredient packaging rule"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            original_unit = original_unit.strip().lower()
            account_name = account_name.strip()

            if DB_TYPE == "sqlite":
                cursor.execute("""
                    INSERT OR REPLACE INTO ingredient_packaging_rules (
                        telegram_user_id, account_name, poster_ingredient_id, original_unit,
                        coefficient, target_unit, notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (telegram_user_id, account_name, poster_ingredient_id, original_unit, coefficient, target_unit, notes))
            else:
                cursor.execute("""
                    INSERT INTO ingredient_packaging_rules (
                        telegram_user_id, account_name, poster_ingredient_id, original_unit,
                        coefficient, target_unit, notes
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (telegram_user_id, account_name, poster_ingredient_id, original_unit)
                    DO UPDATE SET
                        coefficient = EXCLUDED.coefficient,
                        target_unit = EXCLUDED.target_unit,
                        notes = EXCLUDED.notes
                """, (telegram_user_id, account_name, poster_ingredient_id, original_unit, coefficient, target_unit, notes))
            conn.commit()
            conn.close()
            logger.info(f"✅ Packaging rule saved: User {telegram_user_id}, Account '{account_name}', Ingredient {poster_ingredient_id}, '{original_unit}' -> coefficient {coefficient}")
            return True
        except Exception as e:
            logger.error(f"Failed to add packaging rule: {e}")
            return False

    def delete_packaging_rule_by_id(self, rule_id: int, telegram_user_id: int) -> bool:
        """Delete an ingredient packaging rule by ID"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            if DB_TYPE == "sqlite":
                cursor.execute("DELETE FROM ingredient_packaging_rules WHERE id = ? AND telegram_user_id = ?", (rule_id, telegram_user_id))
            else:
                cursor.execute("DELETE FROM ingredient_packaging_rules WHERE id = %s AND telegram_user_id = %s", (rule_id, telegram_user_id))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Failed to delete packaging rule: {e}")
            return False

    def delete_packaging_rule(self, telegram_user_id: int, poster_ingredient_id: int, original_unit: str) -> bool:
        """Delete an ingredient packaging rule"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            original_unit = original_unit.strip().lower()

            if DB_TYPE == "sqlite":
                cursor.execute("""
                    DELETE FROM ingredient_packaging_rules
                    WHERE telegram_user_id = ? AND poster_ingredient_id = ? AND original_unit = ?
                """, (telegram_user_id, poster_ingredient_id, original_unit))
            else:
                cursor.execute("""
                    DELETE FROM ingredient_packaging_rules
                    WHERE telegram_user_id = %s AND poster_ingredient_id = %s AND original_unit = %s
                """, (telegram_user_id, poster_ingredient_id, original_unit))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Failed to delete packaging rule: {e}")
            return False

    # === Purchase Sheet Methods ===

    def get_purchase_suppliers(self, telegram_user_id: int) -> list:
        """Get all purchase suppliers for a user"""
        try:
            conn = self._get_connection()
            if DB_TYPE == "sqlite":
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, name, schedule, created_at, updated_at
                    FROM purchase_suppliers
                    WHERE telegram_user_id = ?
                    ORDER BY id
                """, (telegram_user_id,))
                rows = cursor.fetchall()
                columns = [desc[0] for desc in cursor.description]
                res = [dict(zip(columns, row)) for row in rows]
            else:
                cursor = conn.cursor(cursor_factory=RealDictCursor)
                cursor.execute("""
                    SELECT id, name, schedule, created_at, updated_at
                    FROM purchase_suppliers
                    WHERE telegram_user_id = %s
                    ORDER BY id
                """, (telegram_user_id,))
                res = [dict(row) for row in cursor.fetchall()]
            conn.close()
            
            # Parse schedule JSON
            import json
            for supplier in res:
                try:
                    supplier['schedule'] = json.loads(supplier['schedule'])
                except Exception:
                    supplier['schedule'] = {}
            return res
        except Exception as e:
            logger.error(f"Failed to get purchase suppliers: {e}")
            return []

    def add_purchase_supplier(self, telegram_user_id: int, name: str, schedule: dict) -> int:
        """Add a new purchase supplier, returns supplier ID or -1 on failure"""
        try:
            import json
            from datetime import datetime
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            schedule_str = json.dumps(schedule)

            conn = self._get_connection()
            cursor = conn.cursor()
            supplier_id = -1

            if DB_TYPE == "sqlite":
                cursor.execute("""
                    INSERT OR REPLACE INTO purchase_suppliers (
                        telegram_user_id, name, schedule, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                """, (telegram_user_id, name, schedule_str, now_str, now_str))
                supplier_id = cursor.lastrowid
            else:
                cursor.execute("""
                    INSERT INTO purchase_suppliers (
                        telegram_user_id, name, schedule, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (telegram_user_id, name)
                    DO UPDATE SET
                        schedule = EXCLUDED.schedule,
                        updated_at = EXCLUDED.updated_at
                    RETURNING id
                """, (telegram_user_id, name, schedule_str, datetime.now(), datetime.now()))
                row = cursor.fetchone()
                if row:
                    supplier_id = row[0]
            conn.commit()
            conn.close()
            return supplier_id
        except Exception as e:
            logger.error(f"Failed to add purchase supplier: {e}")
            return -1

    def delete_purchase_supplier(self, telegram_user_id: int, supplier_id: int) -> bool:
        """Delete a purchase supplier and all associated ingredients"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            if DB_TYPE == "sqlite":
                # Explicitly delete ingredients first to avoid orphans when FK constraint is disabled
                cursor.execute("DELETE FROM purchase_ingredients WHERE supplier_id = ? AND telegram_user_id = ?", (supplier_id, telegram_user_id))
                cursor.execute("DELETE FROM purchase_suppliers WHERE id = ? AND telegram_user_id = ?", (supplier_id, telegram_user_id))
            else:
                cursor.execute("DELETE FROM purchase_ingredients WHERE supplier_id = %s AND telegram_user_id = %s", (supplier_id, telegram_user_id))
                cursor.execute("DELETE FROM purchase_suppliers WHERE id = %s AND telegram_user_id = %s", (supplier_id, telegram_user_id))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Failed to delete purchase supplier: {e}")
            return False

    def update_purchase_ingredient_poster_id(self, telegram_user_id: int, ing_row_id: int, poster_ingredient_id: int) -> bool:
        """Update the Poster ingredient ID for a purchase ingredient"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            if DB_TYPE == "sqlite":
                cursor.execute(
                    "UPDATE purchase_ingredients SET poster_ingredient_id = ? WHERE id = ? AND telegram_user_id = ?",
                    (poster_ingredient_id, ing_row_id, telegram_user_id)
                )
            else:
                cursor.execute(
                    "UPDATE purchase_ingredients SET poster_ingredient_id = %s WHERE id = %s AND telegram_user_id = %s",
                    (poster_ingredient_id, ing_row_id, telegram_user_id)
                )
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Failed to update purchase ingredient poster ID: {e}")
            return False

    def update_purchase_ingredient_name(self, telegram_user_id: int, ing_row_id: int, name: str) -> bool:
        """Update the display name for a purchase ingredient"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            if DB_TYPE == "sqlite":
                cursor.execute(
                    "UPDATE purchase_ingredients SET name = ? WHERE id = ? AND telegram_user_id = ?",
                    (name, ing_row_id, telegram_user_id)
                )
            else:
                cursor.execute(
                    "UPDATE purchase_ingredients SET name = %s WHERE id = %s AND telegram_user_id = %s",
                    (name, ing_row_id, telegram_user_id)
                )
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Failed to update purchase ingredient name: {e}")
            return False

    def get_purchase_ingredients(self, telegram_user_id: int, supplier_id: Optional[int] = None) -> list:
        """Get all purchase ingredients for a user, optionally filtered by supplier"""
        try:
            conn = self._get_connection()
            if DB_TYPE == "sqlite":
                cursor = conn.cursor()
                if supplier_id is not None:
                    cursor.execute("""
                        SELECT id, supplier_id, name, poster_ingredient_id, default_target_stock, sort_order, created_at, updated_at
                        FROM purchase_ingredients
                        WHERE telegram_user_id = ? AND supplier_id = ?
                        ORDER BY sort_order, id
                    """, (telegram_user_id, supplier_id))
                else:
                    cursor.execute("""
                        SELECT id, supplier_id, name, poster_ingredient_id, default_target_stock, sort_order, created_at, updated_at
                        FROM purchase_ingredients
                        WHERE telegram_user_id = ?
                        ORDER BY supplier_id, sort_order, id
                    """, (telegram_user_id,))
                rows = cursor.fetchall()
                columns = [desc[0] for desc in cursor.description]
                res = [dict(zip(columns, row)) for row in rows]
            else:
                cursor = conn.cursor(cursor_factory=RealDictCursor)
                if supplier_id is not None:
                    cursor.execute("""
                        SELECT id, supplier_id, name, poster_ingredient_id, default_target_stock, sort_order, created_at, updated_at
                        FROM purchase_ingredients
                        WHERE telegram_user_id = %s AND supplier_id = %s
                        ORDER BY sort_order, id
                    """, (telegram_user_id, supplier_id))
                else:
                    cursor.execute("""
                        SELECT id, supplier_id, name, poster_ingredient_id, default_target_stock, sort_order, created_at, updated_at
                        FROM purchase_ingredients
                        WHERE telegram_user_id = %s
                        ORDER BY supplier_id, sort_order, id
                    """, (telegram_user_id,))
                res = [dict(row) for row in cursor.fetchall()]
            conn.close()
            return res
        except Exception as e:
            logger.error(f"Failed to get purchase ingredients: {e}")
            return []

    def add_purchase_ingredient(
        self,
        telegram_user_id: int,
        supplier_id: int,
        name: str,
        poster_ingredient_id: Optional[int] = None,
        default_target_stock: Optional[float] = None,
        sort_order: int = 0
    ) -> bool:
        """Add or update an ingredient in the purchase sheet template"""
        try:
            from datetime import datetime
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            conn = self._get_connection()
            cursor = conn.cursor()

            # Check if it already exists for this supplier
            if DB_TYPE == "sqlite":
                cursor.execute("""
                    SELECT id FROM purchase_ingredients 
                    WHERE telegram_user_id = ? AND supplier_id = ? AND name = ?
                """, (telegram_user_id, supplier_id, name))
                existing = cursor.fetchone()
                if existing:
                    cursor.execute("""
                        UPDATE purchase_ingredients
                        SET poster_ingredient_id = ?, default_target_stock = ?, sort_order = ?, updated_at = ?
                        WHERE id = ?
                    """, (poster_ingredient_id, default_target_stock, sort_order, now_str, existing[0]))
                else:
                    cursor.execute("""
                        INSERT INTO purchase_ingredients (
                            telegram_user_id, supplier_id, name, poster_ingredient_id,
                            default_target_stock, sort_order, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (telegram_user_id, supplier_id, name, poster_ingredient_id, default_target_stock, sort_order, now_str, now_str))
            else:
                cursor.execute("""
                    SELECT id FROM purchase_ingredients 
                    WHERE telegram_user_id = %s AND supplier_id = %s AND name = %s
                """, (telegram_user_id, supplier_id, name))
                existing = cursor.fetchone()
                if existing:
                    cursor.execute("""
                        UPDATE purchase_ingredients
                        SET poster_ingredient_id = %s, default_target_stock = %s, sort_order = %s, updated_at = %s
                        WHERE id = %s
                    """, (poster_ingredient_id, default_target_stock, sort_order, datetime.now(), existing[0]))
                else:
                    cursor.execute("""
                        INSERT INTO purchase_ingredients (
                            telegram_user_id, supplier_id, name, poster_ingredient_id,
                            default_target_stock, sort_order, created_at, updated_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """, (telegram_user_id, supplier_id, name, poster_ingredient_id, default_target_stock, sort_order, datetime.now(), datetime.now()))
            
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Failed to add purchase ingredient: {e}")
            return False

    def delete_purchase_ingredient(self, telegram_user_id: int, ingredient_id: int) -> bool:
        """Delete an ingredient from the purchase sheet template"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            if DB_TYPE == "sqlite":
                cursor.execute("DELETE FROM purchase_ingredients WHERE id = ? AND telegram_user_id = ?", (ingredient_id, telegram_user_id))
            else:
                cursor.execute("DELETE FROM purchase_ingredients WHERE id = %s AND telegram_user_id = %s", (ingredient_id, telegram_user_id))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Failed to delete purchase ingredient: {e}")
            return False

    def save_purchase_history(self, telegram_user_id: int, date: str, supplier_name: str, items: list) -> bool:
        """Save purchase sheet record to archive history"""
        try:
            import json
            from datetime import datetime
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            items_str = json.dumps(items)

            conn = self._get_connection()
            cursor = conn.cursor()

            if DB_TYPE == "sqlite":
                cursor.execute("""
                    INSERT INTO purchase_history (
                        telegram_user_id, date, supplier_name, items_json, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                """, (telegram_user_id, date, supplier_name, items_str, now_str))
            else:
                cursor.execute("""
                    INSERT INTO purchase_history (
                        telegram_user_id, date, supplier_name, items_json, created_at
                    ) VALUES (%s, %s, %s, %s, %s)
                """, (telegram_user_id, date, supplier_name, items_str, datetime.now()))
            
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Failed to save purchase history: {e}")
            return False

    def get_purchase_history(self, telegram_user_id: int, date: Optional[str] = None) -> list:
        """Get archived purchase history"""
        try:
            conn = self._get_connection()
            if DB_TYPE == "sqlite":
                cursor = conn.cursor()
                if date:
                    cursor.execute("""
                        SELECT id, date, supplier_name, items_json, created_at
                        FROM purchase_history
                        WHERE telegram_user_id = ? AND date = ?
                        ORDER BY id DESC
                    """, (telegram_user_id, date))
                else:
                    cursor.execute("""
                        SELECT id, date, supplier_name, items_json, created_at
                        FROM purchase_history
                        WHERE telegram_user_id = ?
                        ORDER BY id DESC
                        LIMIT 100
                    """, (telegram_user_id,))
                rows = cursor.fetchall()
                columns = [desc[0] for desc in cursor.description]
                res = [dict(zip(columns, row)) for row in rows]
            else:
                cursor = conn.cursor(cursor_factory=RealDictCursor)
                if date:
                    cursor.execute("""
                        SELECT id, date, supplier_name, items_json, created_at
                        FROM purchase_history
                        WHERE telegram_user_id = %s AND date = %s
                        ORDER BY id DESC
                    """, (telegram_user_id, date))
                else:
                    cursor.execute("""
                        SELECT id, date, supplier_name, items_json, created_at
                        FROM purchase_history
                        WHERE telegram_user_id = %s
                        ORDER BY id DESC
                        LIMIT 100
                    """, (telegram_user_id,))
                res = [dict(row) for row in cursor.fetchall()]
            conn.close()

            # Parse items JSON
            import json
            for record in res:
                try:
                    record['items'] = json.loads(record['items_json'])
                except Exception:
                    record['items'] = []
            return res
        except Exception as e:
            logger.error(f"Failed to get purchase history: {e}")
            return []

    def get_ingredient_habits(self, telegram_user_id: int) -> list:
        """Get all ingredient habits (typical prices) for a user"""
        try:
            conn = self._get_connection()
            if DB_TYPE == "sqlite":
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, account_name, poster_ingredient_id, default_price, default_quantity, notes, created_at
                    FROM ingredient_habits
                    WHERE telegram_user_id = ?
                """, (telegram_user_id,))
                rows = cursor.fetchall()
                columns = [desc[0] for desc in cursor.description]
                res = [dict(zip(columns, row)) for row in rows]
            else:
                cursor = conn.cursor(cursor_factory=RealDictCursor)
                cursor.execute("""
                    SELECT id, account_name, poster_ingredient_id, default_price, default_quantity, notes, created_at
                    FROM ingredient_habits
                    WHERE telegram_user_id = %s
                """, (telegram_user_id,))
                res = [dict(row) for row in cursor.fetchall()]
            conn.close()
            return res
        except Exception as e:
            logger.error(f"Failed to get ingredient habits: {e}")
            return []

    def add_ingredient_habit(
        self,
        telegram_user_id: int,
        poster_ingredient_id: int,
        default_price: float = None,
        default_quantity: float = None,
        notes: str = '',
        account_name: str = ''
    ) -> bool:
        """Add or update an ingredient habit"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            account_name = account_name.strip()

            if DB_TYPE == "sqlite":
                cursor.execute("""
                    INSERT OR REPLACE INTO ingredient_habits (
                        telegram_user_id, account_name, poster_ingredient_id, default_price,
                        default_quantity, notes
                    ) VALUES (?, ?, ?, ?, ?, ?)
                """, (telegram_user_id, account_name, poster_ingredient_id, default_price, default_quantity, notes))
            else:
                cursor.execute("""
                    INSERT INTO ingredient_habits (
                        telegram_user_id, account_name, poster_ingredient_id, default_price,
                        default_quantity, notes
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (telegram_user_id, account_name, poster_ingredient_id)
                    DO UPDATE SET
                        default_price = EXCLUDED.default_price,
                        default_quantity = EXCLUDED.default_quantity,
                        notes = EXCLUDED.notes
                """, (telegram_user_id, account_name, poster_ingredient_id, default_price, default_quantity, notes))
            conn.commit()
            conn.close()
            logger.info(f"✅ Ingredient habit saved: User {telegram_user_id}, Account '{account_name}', Ingredient {poster_ingredient_id}, price {default_price}")
            return True
        except Exception as e:
            logger.error(f"Failed to add ingredient habit: {e}")
            return False

    def delete_ingredient_habit_by_id(self, habit_id: int, telegram_user_id: int) -> bool:
        """Delete an ingredient habit by ID"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            if DB_TYPE == "sqlite":
                cursor.execute("DELETE FROM ingredient_habits WHERE id = ? AND telegram_user_id = ?", (habit_id, telegram_user_id))
            else:
                cursor.execute("DELETE FROM ingredient_habits WHERE id = %s AND telegram_user_id = %s", (habit_id, telegram_user_id))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Failed to delete habit: {e}")
            return False

    def get_supply_draft_item(self, item_id: int, telegram_user_id: int = None) -> Optional[Dict]:
        """Получить позицию из черновика поставки по ее ID"""
        try:
            conn = self._get_connection()
            if DB_TYPE == "sqlite":
                cursor = conn.cursor()
                if telegram_user_id is not None:
                    cursor.execute("""
                        SELECT i.* FROM supply_draft_items i
                        JOIN supply_drafts d ON i.supply_draft_id = d.id
                        WHERE i.id = ? AND d.telegram_user_id = ?
                    """, (item_id, telegram_user_id))
                else:
                    cursor.execute("SELECT * FROM supply_draft_items WHERE id = ?", (item_id,))
                row = cursor.fetchone()
                columns = [desc[0] for desc in cursor.description] if row else []
                res = dict(zip(columns, row)) if row else None
            else:
                cursor = conn.cursor(cursor_factory=RealDictCursor)
                if telegram_user_id is not None:
                    cursor.execute("""
                        SELECT i.* FROM supply_draft_items i
                        JOIN supply_drafts d ON i.supply_draft_id = d.id
                        WHERE i.id = %s AND d.telegram_user_id = %s
                    """, (item_id, telegram_user_id))
                else:
                    cursor.execute("SELECT * FROM supply_draft_items WHERE id = %s", (item_id,))
                row = cursor.fetchone()
                res = dict(row) if row else None
            conn.close()
            return res
        except Exception as e:
            logger.error(f"Failed to get supply draft item: {e}")
            return None

    # === Supplier Aliases Methods ===

    def get_supplier_account_mappings(self, telegram_user_id: int) -> list:
        """Return explicit canonical supplier IDs for every Poster account."""
        conn = self._get_connection()
        if DB_TYPE == "sqlite":
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, canonical_name, poster_account_id, poster_account_name,
                       poster_supplier_id, poster_supplier_name, confidence, source,
                       created_at, updated_at
                FROM supplier_account_mappings
                WHERE telegram_user_id = ?
                ORDER BY canonical_name, poster_account_name
            """, (telegram_user_id,))
            columns = [desc[0] for desc in cursor.description]
            rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        else:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                SELECT id, canonical_name, poster_account_id, poster_account_name,
                       poster_supplier_id, poster_supplier_name, confidence, source,
                       created_at, updated_at
                FROM supplier_account_mappings
                WHERE telegram_user_id = %s
                ORDER BY canonical_name, poster_account_name
            """, (telegram_user_id,))
            rows = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return rows

    def replace_auto_supplier_account_mappings(
        self,
        telegram_user_id: int,
        mappings: list,
    ) -> bool:
        """Replace high-confidence automatic mappings while preserving manual rows."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            placeholder = '?' if DB_TYPE == 'sqlite' else '%s'
            cursor.execute(
                f"DELETE FROM supplier_account_mappings WHERE telegram_user_id = {placeholder} AND source = 'auto'",
                (telegram_user_id,),
            )

            for mapping in mappings:
                values = (
                    telegram_user_id,
                    mapping['canonical_name'],
                    int(mapping['poster_account_id']),
                    mapping['poster_account_name'],
                    int(mapping['poster_supplier_id']),
                    mapping['poster_supplier_name'],
                    float(mapping.get('confidence', 100)),
                    'auto',
                )
                if DB_TYPE == 'sqlite':
                    cursor.execute("""
                        INSERT OR IGNORE INTO supplier_account_mappings (
                            telegram_user_id, canonical_name, poster_account_id,
                            poster_account_name, poster_supplier_id, poster_supplier_name,
                            confidence, source, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                    """, values)
                else:
                    cursor.execute("""
                        INSERT INTO supplier_account_mappings (
                            telegram_user_id, canonical_name, poster_account_id,
                            poster_account_name, poster_supplier_id, poster_supplier_name,
                            confidence, source, updated_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                        ON CONFLICT DO NOTHING
                    """, values)

            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Failed to replace supplier account mappings: {e}")
            return False


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
                  AND COALESCE(notes, '') NOT LIKE ?
            """, (telegram_user_id, alias_normalized, 'Авто-обучено при редактировании черновика%'))
            row = cursor.fetchone()

            if not row:
                # Partial match (alias contains in text or text contains alias)
                cursor.execute("""
                    SELECT poster_supplier_id, poster_supplier_name, alias_text
                    FROM supplier_aliases
                    WHERE telegram_user_id = ?
                      AND COALESCE(notes, '') NOT LIKE ?
                    ORDER BY LENGTH(alias_text) DESC
                """, (telegram_user_id, 'Авто-обучено при редактировании черновика%'))
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
                  AND COALESCE(notes, '') NOT LIKE %s
            """, (telegram_user_id, alias_normalized, 'Авто-обучено при редактировании черновика%'))
            row = cursor.fetchone()

            if not row:
                cursor.execute("""
                    SELECT poster_supplier_id, poster_supplier_name, alias_text
                    FROM supplier_aliases
                    WHERE telegram_user_id = %s
                      AND COALESCE(notes, '') NOT LIKE %s
                    ORDER BY LENGTH(alias_text) DESC
                """, (telegram_user_id, 'Авто-обучено при редактировании черновика%'))
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

    def save_expense_drafts(self, telegram_user_id: int, items: list, source: str = "cash", source_account: str = None, date_str: str = None) -> int:
        """
        Сохранить черновики расходов в БД

        Args:
            telegram_user_id: ID пользователя Telegram
            items: Список ExpenseItem или dict с полями amount, description, expense_type, category
            source: Источник (cash, kaspi)
            source_account: Название счёта
            date_str: Кастомная дата создания (YYYY-MM-DD или YYYY-MM-DD HH:MM:SS)

        Returns:
            Количество сохранённых записей
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # Format date_str to full timestamp if needed
            full_date_str = None
            if date_str:
                date_str = date_str.strip()
                if len(date_str) == 10:
                    full_date_str = f"{date_str} 12:00:00"
                else:
                    full_date_str = date_str

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
                    if full_date_str:
                        cursor.execute("""
                            INSERT INTO expense_drafts
                            (telegram_user_id, amount, description, expense_type, category, source, source_account, quantity, unit, price_per_unit, created_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (telegram_user_id, amount, description, expense_type, category, source, source_account, quantity, unit, price_per_unit, full_date_str))
                    else:
                        cursor.execute("""
                            INSERT INTO expense_drafts
                            (telegram_user_id, amount, description, expense_type, category, source, source_account, quantity, unit, price_per_unit)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (telegram_user_id, amount, description, expense_type, category, source, source_account, quantity, unit, price_per_unit))
                else:
                    if full_date_str:
                        cursor.execute("""
                            INSERT INTO expense_drafts
                            (telegram_user_id, amount, description, expense_type, category, source, source_account, quantity, unit, price_per_unit, created_at)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """, (telegram_user_id, amount, description, expense_type, category, source, source_account, quantity, unit, price_per_unit, full_date_str))
                    else:
                        cursor.execute("""
                            INSERT INTO expense_drafts
                            (telegram_user_id, amount, description, expense_type, category, source, source_account, quantity, unit, price_per_unit)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """, (telegram_user_id, amount, description, expense_type, category, source, source_account, quantity, unit, price_per_unit))
                count += 1

            conn.commit()
            conn.close()

            logger.info(f"✅ Saved {count} expense drafts for user {telegram_user_id} on date {date_str or 'now'}")
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
        poster_amount: float = None,
        created_at: str = None
    ) -> Optional[int]:
        """
        Создать один черновик расхода (для ручного ввода или синхронизации из Poster)

        Args:
            is_income: True если это доход (например, продажа масла), False для расхода
            completion_status: 'pending' (не в Poster), 'completed' (в Poster)
            poster_amount: Текущая сумма в Poster (для отслеживания изменений)
            created_at: Кастомная дата создания (YYYY-MM-DD или YYYY-MM-DD HH:MM:SS)

        Returns:
            ID созданного черновика или None при ошибке
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            is_income_int = 1 if is_income else 0

            # Format created_at to full timestamp if needed
            full_created_at = None
            if created_at:
                created_at = created_at.strip()
                if len(created_at) == 10:
                    full_created_at = f"{created_at} 12:00:00"
                else:
                    full_created_at = created_at

            if DB_TYPE == "sqlite":
                if full_created_at:
                    cursor.execute("""
                        INSERT INTO expense_drafts
                        (telegram_user_id, amount, description, expense_type, category, source, account_id, poster_account_id, poster_transaction_id, is_income, completion_status, poster_amount, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (telegram_user_id, amount, description, expense_type, category, source, account_id, poster_account_id, poster_transaction_id, is_income_int, completion_status, poster_amount, full_created_at))
                else:
                    cursor.execute("""
                        INSERT INTO expense_drafts
                        (telegram_user_id, amount, description, expense_type, category, source, account_id, poster_account_id, poster_transaction_id, is_income, completion_status, poster_amount)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (telegram_user_id, amount, description, expense_type, category, source, account_id, poster_account_id, poster_transaction_id, is_income_int, completion_status, poster_amount))
                draft_id = cursor.lastrowid
            else:
                if full_created_at:
                    cursor.execute("""
                        INSERT INTO expense_drafts
                        (telegram_user_id, amount, description, expense_type, category, source, account_id, poster_account_id, poster_transaction_id, is_income, completion_status, poster_amount, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING id
                    """, (telegram_user_id, amount, description, expense_type, category, source, account_id, poster_account_id, poster_transaction_id, is_income_int, completion_status, poster_amount, full_created_at))
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
                'day_result', 'shift_left', 'collection', 'cashless_diff',
                'wedrink_sales'
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
                # Create a minimal row with cafe-specific default for cash_to_leave
                cursor.execute(f"""
                    INSERT INTO shift_closings (telegram_user_id, date, poster_account_id,
                        salaries_created, salaries_data, updated_at, cash_to_leave)
                    VALUES ({placeholder}, {placeholder}, {placeholder},
                        {placeholder}, {placeholder}, CURRENT_TIMESTAMP, 10000)
                """, (telegram_user_id, date, poster_account_id, true_val, salaries_data))

            conn.commit()
            conn.close()
            return True

        except Exception as e:
            logger.error(f"Failed to set cafe salaries: {e}")
            import traceback
            traceback.print_exc()
            return False

    # ==================== Daily Transactions Log Methods ====================

    def is_daily_transactions_created(self, telegram_user_id: int, date: str) -> bool:
        """Check if daily transactions were already created for this user and date.
        Also returns True for count=-1 (claim in progress) to prevent race conditions."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            placeholder = "?" if DB_TYPE == "sqlite" else "%s"

            cursor.execute(f"""
                SELECT count FROM daily_transactions_log
                WHERE telegram_user_id = {placeholder} AND date = {placeholder}
            """, (telegram_user_id, date))

            row = cursor.fetchone()
            conn.close()
            if row is None:
                return False
            count = row[0] if isinstance(row, tuple) else row['count']
            # count > 0 = done, count = -1 = claim in progress
            return count != 0

        except Exception as e:
            logger.error(f"Failed to check daily_transactions_log: {e}")
            return False

    def is_daily_transactions_created_for_date(self, date: str) -> bool:
        """Check if daily transactions were already created by ANY user for this date.
        Prevents multi-user duplication when multiple users share the same Poster account.
        Also detects claims (count=-1) to prevent race conditions."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            placeholder = "?" if DB_TYPE == "sqlite" else "%s"

            cursor.execute(f"""
                SELECT telegram_user_id, count FROM daily_transactions_log
                WHERE date = {placeholder} AND count != 0
                LIMIT 1
            """, (date,))

            row = cursor.fetchone()
            conn.close()
            if row is not None:
                uid = row[0] if isinstance(row, tuple) else row['telegram_user_id']
                logger.info(f"Daily transactions already created for {date} by user {uid}")
                return True
            return False

        except Exception as e:
            logger.error(f"Failed to check daily_transactions_log for date: {e}")
            return False

    def try_claim_daily_transactions(self, telegram_user_id: int, date: str) -> bool:
        """Atomically try to claim the daily transactions slot.
        Uses INSERT ... ON CONFLICT DO NOTHING so only the first caller succeeds.
        Returns True if claimed, False if already claimed by another process."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            placeholder = "?" if DB_TYPE == "sqlite" else "%s"

            if DB_TYPE == "sqlite":
                cursor.execute(f"""
                    INSERT OR IGNORE INTO daily_transactions_log
                    (telegram_user_id, date, count, created_at)
                    VALUES ({placeholder}, {placeholder}, -1, CURRENT_TIMESTAMP)
                """, (telegram_user_id, date))
            else:
                cursor.execute(f"""
                    INSERT INTO daily_transactions_log (telegram_user_id, date, count)
                    VALUES ({placeholder}, {placeholder}, -1)
                    ON CONFLICT (telegram_user_id, date) DO NOTHING
                """, (telegram_user_id, date))

            inserted = cursor.rowcount > 0
            conn.commit()
            conn.close()
            return inserted

        except Exception as e:
            logger.error(f"Failed to claim daily_transactions_log: {e}")
            return False

    def set_daily_transactions_created(self, telegram_user_id: int, date: str, count: int) -> bool:
        """Mark daily transactions as created for this user and date (update existing claim)"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            placeholder = "?" if DB_TYPE == "sqlite" else "%s"

            if DB_TYPE == "sqlite":
                cursor.execute(f"""
                    INSERT OR REPLACE INTO daily_transactions_log
                    (telegram_user_id, date, count, created_at)
                    VALUES ({placeholder}, {placeholder}, {placeholder}, CURRENT_TIMESTAMP)
                """, (telegram_user_id, date, count))
            else:
                cursor.execute(f"""
                    INSERT INTO daily_transactions_log (telegram_user_id, date, count)
                    VALUES ({placeholder}, {placeholder}, {placeholder})
                    ON CONFLICT (telegram_user_id, date)
                    DO UPDATE SET count = {placeholder}, created_at = CURRENT_TIMESTAMP
                """, (telegram_user_id, date, count, count))

            conn.commit()
            conn.close()
            return True

        except Exception as e:
            logger.error(f"Failed to set daily_transactions_log: {e}")
            return False

    # ==================== Daily Transactions Config Methods ====================

    def get_daily_transaction_configs(self, telegram_user_id: int) -> list:
        """Get all daily transaction configs for user, ordered by sort_order"""
        try:
            conn = self._get_connection()
            if DB_TYPE == "sqlite":
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, telegram_user_id, account_name, transaction_type,
                           category_id, category_name, account_from_id, account_from_name,
                           account_to_id, account_to_name, amount, comment,
                           is_enabled, sort_order, created_at, updated_at
                    FROM daily_transactions_config
                    WHERE telegram_user_id = ?
                    ORDER BY sort_order, id
                """, (telegram_user_id,))
                rows = cursor.fetchall()
                columns = ['id', 'telegram_user_id', 'account_name', 'transaction_type',
                           'category_id', 'category_name', 'account_from_id', 'account_from_name',
                           'account_to_id', 'account_to_name', 'amount', 'comment',
                           'is_enabled', 'sort_order', 'created_at', 'updated_at']
                result = [dict(zip(columns, row)) for row in rows]
            else:
                from psycopg2.extras import RealDictCursor
                cursor = conn.cursor(cursor_factory=RealDictCursor)
                cursor.execute("""
                    SELECT id, telegram_user_id, account_name, transaction_type,
                           category_id, category_name, account_from_id, account_from_name,
                           account_to_id, account_to_name, amount, comment,
                           is_enabled, sort_order, created_at, updated_at
                    FROM daily_transactions_config
                    WHERE telegram_user_id = %s
                    ORDER BY sort_order, id
                """, (telegram_user_id,))
                result = [dict(row) for row in cursor.fetchall()]
            conn.close()
            return result
        except Exception as e:
            logger.error(f"Failed to get daily_transaction_configs: {e}")
            return []

    def create_daily_transaction_config(self, telegram_user_id: int, data: dict) -> int:
        """Create a new daily transaction config. Returns the new ID."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            p = "?" if DB_TYPE == "sqlite" else "%s"

            cursor.execute(f"""
                INSERT INTO daily_transactions_config
                (telegram_user_id, account_name, transaction_type, category_id, category_name,
                 account_from_id, account_from_name, account_to_id, account_to_name,
                 amount, comment, is_enabled, sort_order)
                VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p})
            """, (
                telegram_user_id,
                data.get('account_name', 'Pizzburg'),
                data.get('transaction_type', 0),
                data.get('category_id', 0),
                data.get('category_name', ''),
                data.get('account_from_id', 4),
                data.get('account_from_name', ''),
                data.get('account_to_id'),
                data.get('account_to_name', ''),
                data.get('amount', 1),
                data.get('comment', ''),
                1 if data.get('is_enabled', True) else 0,
                data.get('sort_order', 0)
            ))
            new_id = cursor.lastrowid
            if DB_TYPE != "sqlite":
                cursor.execute("SELECT lastval()")
                new_id = cursor.fetchone()[0]
            conn.commit()
            conn.close()
            return new_id
        except Exception as e:
            logger.error(f"Failed to create daily_transaction_config: {e}")
            return 0

    def update_daily_transaction_config(self, config_id: int, data: dict) -> bool:
        """Update a daily transaction config"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            p = "?" if DB_TYPE == "sqlite" else "%s"

            fields = []
            values = []
            for key in ['account_name', 'transaction_type', 'category_id', 'category_name',
                        'account_from_id', 'account_from_name', 'account_to_id', 'account_to_name',
                        'amount', 'comment', 'is_enabled', 'sort_order']:
                if key in data:
                    fields.append(f"{key} = {p}")
                    values.append(data[key])

            if not fields:
                return False

            now_expr = "CURRENT_TIMESTAMP"
            fields.append(f"updated_at = {now_expr}")
            values.append(config_id)

            cursor.execute(f"""
                UPDATE daily_transactions_config
                SET {', '.join(fields)}
                WHERE id = {p}
            """, tuple(values))

            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Failed to update daily_transaction_config: {e}")
            return False

    def delete_daily_transaction_config(self, config_id: int) -> bool:
        """Delete a daily transaction config"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            p = "?" if DB_TYPE == "sqlite" else "%s"

            cursor.execute(f"DELETE FROM daily_transactions_config WHERE id = {p}", (config_id,))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Failed to delete daily_transaction_config: {e}")
            return False

    def seed_daily_transaction_configs(self, telegram_user_id: int) -> int:
        """Seed default daily transaction configs from hardcoded values.
        Only seeds if the table is empty for this user. Returns count of seeded configs."""
        existing = self.get_daily_transaction_configs(telegram_user_id)
        if existing:
            return 0

        defaults = [
            {'account_name': 'Pizzburg', 'transaction_type': 0, 'category_id': 17,
             'category_name': 'Повара', 'account_from_id': 4, 'account_from_name': 'Оставил в кассе',
             'amount': 1, 'comment': 'Заготовка', 'sort_order': 1},
            {'account_name': 'Pizzburg', 'transaction_type': 0, 'category_id': 17,
             'category_name': 'Повара', 'account_from_id': 4, 'account_from_name': 'Оставил в кассе',
             'amount': 1, 'comment': 'Мадира Т', 'sort_order': 2},
            {'account_name': 'Pizzburg', 'transaction_type': 0, 'category_id': 17,
             'category_name': 'Повара', 'account_from_id': 4, 'account_from_name': 'Оставил в кассе',
             'amount': 1, 'comment': 'Нургуль Т', 'sort_order': 3},
            {'account_name': 'Pizzburg', 'transaction_type': 0, 'category_id': 18,
             'category_name': 'КухРабочая', 'account_from_id': 4, 'account_from_name': 'Оставил в кассе',
             'amount': 1, 'comment': '', 'sort_order': 4},
            {'account_name': 'Pizzburg', 'transaction_type': 0, 'category_id': 15,
             'category_name': 'Курьер', 'account_from_id': 4, 'account_from_name': 'Оставил в кассе',
             'amount': 1, 'comment': 'Курьеры', 'sort_order': 5},
            {'account_name': 'Pizzburg', 'transaction_type': 0, 'category_id': 0,
             'category_name': 'Зарплаты', 'account_from_id': 4, 'account_from_name': 'Оставил в кассе',
             'amount': 1, 'comment': 'Мадина админ', 'sort_order': 6},
            {'account_name': 'Pizzburg', 'transaction_type': 0, 'category_id': 24,
             'category_name': 'Логистика', 'account_from_id': 4, 'account_from_name': 'Оставил в кассе',
             'amount': 1, 'comment': 'Караганда', 'sort_order': 7},
            {'account_name': 'Pizzburg', 'transaction_type': 0, 'category_id': 24,
             'category_name': 'Логистика', 'account_from_id': 4, 'account_from_name': 'Оставил в кассе',
             'amount': 700, 'comment': 'Фарш', 'sort_order': 8},
            {'account_name': 'Pizzburg', 'transaction_type': 0, 'category_id': 24,
             'category_name': 'Логистика', 'account_from_id': 4, 'account_from_name': 'Оставил в кассе',
             'amount': 1000, 'comment': 'Кюрдамир', 'sort_order': 9},
            {'account_name': 'Pizzburg', 'transaction_type': 0, 'category_id': 7,
             'category_name': 'Маркетинг', 'account_from_id': 1, 'account_from_name': 'Каспи Пей',
             'amount': 4100, 'comment': 'Реклама', 'sort_order': 10},
            {'account_name': 'Pizzburg', 'transaction_type': 0, 'category_id': 24,
             'category_name': 'Логистика', 'account_from_id': 1, 'account_from_name': 'Каспи Пей',
             'amount': 1, 'comment': 'Астана', 'sort_order': 11},
            {'account_name': 'Pizzburg', 'transaction_type': 0, 'category_id': 5,
             'category_name': 'Банковские услуги', 'account_from_id': 1, 'account_from_name': 'Каспи Пей',
             'amount': 1, 'comment': 'Комиссия', 'sort_order': 12},
            {'account_name': 'Pizzburg', 'transaction_type': 2, 'category_id': 0,
             'category_name': 'Перевод', 'account_from_id': 4, 'account_from_name': 'Оставил в кассе',
             'account_to_id': 5, 'account_to_name': 'Деньги дома', 'amount': 1,
             'comment': 'Забрал - Имя', 'sort_order': 13},
        ]

        count = 0
        for cfg in defaults:
            cfg['is_enabled'] = True
            if self.create_daily_transaction_config(telegram_user_id, cfg):
                count += 1

        logger.info(f"✅ Seeded {count} default daily transaction configs for user {telegram_user_id}")
        return count

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
        """Create a new web user with hashed password. Returns user id."""
        password_hash = _hash_web_password(password)

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
            if not _check_web_password(password, user['password_hash']):
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
                SELECT u.id, u.username, u.role, u.label, u.poster_account_id, u.is_active,
                       u.created_at, u.last_login, a.account_name
                FROM web_users u
                LEFT JOIN poster_accounts a ON a.id = u.poster_account_id
                WHERE u.telegram_user_id = {placeholder}
                ORDER BY u.role, u.username
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
        password_hash = _hash_web_password(new_password)

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

    # ==================== Account Balance Snapshots (Analytics) ====================

    def save_account_balance_snapshot(self, telegram_user_id: int, date_str: str, account_key: str,
                                       balance: float, account_name: str = None, net_change: float = 0) -> bool:
        """Upsert a daily balance snapshot for an account key."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            placeholder = "?" if DB_TYPE == "sqlite" else "%s"
            if DB_TYPE == "sqlite":
                sql = """
                    INSERT INTO account_balance_snapshots (telegram_user_id, date, account_key, account_name, balance, net_change, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(telegram_user_id, date, account_key) DO UPDATE SET
                        account_name = excluded.account_name,
                        balance = excluded.balance,
                        net_change = excluded.net_change,
                        updated_at = CURRENT_TIMESTAMP
                """
                cursor.execute(sql, (telegram_user_id, date_str, account_key, account_name, balance, net_change))
            else:
                sql = """
                    INSERT INTO account_balance_snapshots (telegram_user_id, date, account_key, account_name, balance, net_change, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT(telegram_user_id, date, account_key) DO UPDATE SET
                        account_name = EXCLUDED.account_name,
                        balance = EXCLUDED.balance,
                        net_change = EXCLUDED.net_change,
                        updated_at = CURRENT_TIMESTAMP
                """
                cursor.execute(sql, (telegram_user_id, date_str, account_key, account_name, balance, net_change))

            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Failed to save account balance snapshot: {e}")
            return False

    def get_account_balance_history(self, telegram_user_id: int, account_key: str = 'total', days: int = 15) -> list:
        """Get history of account balances and net changes for analytics."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            placeholder = "?" if DB_TYPE == "sqlite" else "%s"
            cursor.execute(f"""
                SELECT date, account_key, account_name, balance, net_change
                FROM account_balance_snapshots
                WHERE telegram_user_id = {placeholder} AND account_key = {placeholder}
                ORDER BY date DESC
                LIMIT {days}
            """, (telegram_user_id, account_key))

            columns = [desc[0] for desc in cursor.description]
            rows = cursor.fetchall()
            conn.close()
            result = [dict(zip(columns, row)) for row in rows]
            result.reverse()
            return result
        except Exception as e:
            logger.error(f"Failed to get account balance history: {e}")
            return []

    # ==================== Verified Capital Snapshots ====================

    def save_capital_balance_snapshots(self, telegram_user_id: int, rows: list) -> bool:
        """Insert missing completed-day snapshots without rewriting history."""
        if not rows:
            return True
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            if DB_TYPE == "sqlite":
                sql = """
                    INSERT OR IGNORE INTO capital_balance_snapshots
                    (telegram_user_id, date, account_key, account_name, balance,
                     net_change, cutoff_at, metadata_json, captured_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """
            else:
                sql = """
                    INSERT INTO capital_balance_snapshots
                    (telegram_user_id, date, account_key, account_name, balance,
                     net_change, cutoff_at, metadata_json, captured_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT(telegram_user_id, date, account_key) DO NOTHING
                """
            for row in rows:
                cursor.execute(sql, (
                    telegram_user_id,
                    row['date'],
                    row['account_key'],
                    row.get('account_name'),
                    row['balance'],
                    row.get('net_change', 0),
                    row['cutoff_at'],
                    row.get('metadata_json'),
                ))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Failed to save capital balance snapshots: {e}")
            return False

    def get_capital_balance_history(self, telegram_user_id: int, account_key: str, days: int = 15) -> list:
        """Return verified completed-day snapshots, oldest first."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            placeholder = "?" if DB_TYPE == "sqlite" else "%s"
            cursor.execute(f"""
                SELECT date, account_key, account_name, balance, net_change,
                       cutoff_at, metadata_json, captured_at
                FROM capital_balance_snapshots
                WHERE telegram_user_id = {placeholder} AND account_key = {placeholder}
                ORDER BY date DESC
                LIMIT {int(days)}
            """, (telegram_user_id, account_key))
            columns = [desc[0] for desc in cursor.description]
            rows = cursor.fetchall()
            conn.close()
            result = [dict(zip(columns, row)) for row in rows]
            result.reverse()
            for item in result:
                item['date'] = str(item['date'])
                item['balance'] = float(item['balance'])
                item['net_change'] = float(item['net_change'])
                item['cutoff_at'] = str(item['cutoff_at'])
                item['captured_at'] = str(item['captured_at'])
            return result
        except Exception as e:
            logger.error(f"Failed to get capital balance history: {e}")
            return []

    def get_latest_capital_snapshot_set(self, telegram_user_id: int) -> list:
        """Return every physical account from the latest complete snapshot date."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            placeholder = "?" if DB_TYPE == "sqlite" else "%s"
            cursor.execute(f"""
                SELECT date, account_key, account_name, balance, net_change,
                       cutoff_at, metadata_json, captured_at
                FROM capital_balance_snapshots
                WHERE telegram_user_id = {placeholder}
                  AND date = (
                      SELECT MAX(date) FROM capital_balance_snapshots
                      WHERE telegram_user_id = {placeholder} AND account_key = 'total'
                  )
                ORDER BY account_key
            """, (telegram_user_id, telegram_user_id))
            columns = [desc[0] for desc in cursor.description]
            rows = cursor.fetchall()
            conn.close()
            result = [dict(zip(columns, row)) for row in rows]
            for item in result:
                item['date'] = str(item['date'])
                item['balance'] = float(item['balance'])
                item['net_change'] = float(item['net_change'])
                item['cutoff_at'] = str(item['cutoff_at'])
                item['captured_at'] = str(item['captured_at'])
            return result
        except Exception as e:
            logger.error(f"Failed to get latest capital snapshot set: {e}")
            return []

    # ==================== Business Analytics ====================

    def save_business_analytics_report(
        self,
        telegram_user_id: int,
        report: dict,
        ai_commentary: Optional[dict] = None,
    ) -> bool:
        """Persist a complete Poster-derived report and its daily facts."""
        import json

        if not report or not report.get('report_date'):
            return False
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            report_json = json.dumps(report, ensure_ascii=False)
            ai_json = json.dumps(ai_commentary, ensure_ascii=False) if ai_commentary else None
            source_json = json.dumps(report.get('source_status', []), ensure_ascii=False)
            status = 'complete' if report.get('success') else 'failed'

            if DB_TYPE == "sqlite":
                cursor.execute("""
                    INSERT INTO business_analytics_reports
                    (telegram_user_id, report_date, status, payload_json,
                     ai_commentary_json, source_status_json, generated_at)
                    VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(telegram_user_id, report_date) DO UPDATE SET
                        status = excluded.status,
                        payload_json = excluded.payload_json,
                        ai_commentary_json = excluded.ai_commentary_json,
                        source_status_json = excluded.source_status_json,
                        generated_at = CURRENT_TIMESTAMP
                """, (
                    telegram_user_id, report['report_date'], status, report_json,
                    ai_json, source_json,
                ))
            else:
                cursor.execute("""
                    INSERT INTO business_analytics_reports
                    (telegram_user_id, report_date, status, payload_json,
                     ai_commentary_json, source_status_json, generated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT(telegram_user_id, report_date) DO UPDATE SET
                        status = EXCLUDED.status,
                        payload_json = EXCLUDED.payload_json,
                        ai_commentary_json = EXCLUDED.ai_commentary_json,
                        source_status_json = EXCLUDED.source_status_json,
                        generated_at = CURRENT_TIMESTAMP
                """, (
                    telegram_user_id, report['report_date'], status, report_json,
                    ai_json, source_json,
                ))

            capital_by_date = {
                item['date']: item['balance']
                for item in report.get('capital', {}).get('history', [])
            }
            if DB_TYPE == "sqlite":
                metric_sql = """
                    INSERT INTO business_daily_metrics
                    (telegram_user_id, metric_date, store_id, store_name, revenue,
                     checks, average_check, expenses, supplies, non_supply_expenses,
                     profit_withdrawals, capital_balance, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(telegram_user_id, metric_date, store_id) DO UPDATE SET
                        store_name = excluded.store_name,
                        revenue = excluded.revenue,
                        checks = excluded.checks,
                        average_check = excluded.average_check,
                        expenses = excluded.expenses,
                        supplies = excluded.supplies,
                        non_supply_expenses = excluded.non_supply_expenses,
                        profit_withdrawals = excluded.profit_withdrawals,
                        capital_balance = excluded.capital_balance,
                        updated_at = CURRENT_TIMESTAMP
                """
            else:
                metric_sql = """
                    INSERT INTO business_daily_metrics
                    (telegram_user_id, metric_date, store_id, store_name, revenue,
                     checks, average_check, expenses, supplies, non_supply_expenses,
                     profit_withdrawals, capital_balance, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT(telegram_user_id, metric_date, store_id) DO UPDATE SET
                        store_name = EXCLUDED.store_name,
                        revenue = EXCLUDED.revenue,
                        checks = EXCLUDED.checks,
                        average_check = EXCLUDED.average_check,
                        expenses = EXCLUDED.expenses,
                        supplies = EXCLUDED.supplies,
                        non_supply_expenses = EXCLUDED.non_supply_expenses,
                        profit_withdrawals = EXCLUDED.profit_withdrawals,
                        capital_balance = EXCLUDED.capital_balance,
                        updated_at = CURRENT_TIMESTAMP
                """
            for item in report.get('daily_metrics', []):
                capital_balance = capital_by_date.get(item['date']) if item['store_id'] == 'total' else None
                cursor.execute(metric_sql, (
                    telegram_user_id,
                    item['date'],
                    str(item['store_id']),
                    item['store_name'],
                    item.get('revenue', 0),
                    item.get('checks', 0),
                    item.get('average_check', 0),
                    item.get('expenses', 0),
                    item.get('supplies', 0),
                    item.get('non_supply_expenses', 0),
                    item.get('profit_withdrawals', 0),
                    capital_balance,
                ))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Failed to save business analytics report: {e}")
            return False

    def get_latest_business_analytics_report(self, telegram_user_id: int) -> Optional[dict]:
        """Return the newest complete report with parsed JSON fields."""
        import json

        try:
            conn = self._get_connection()
            placeholder = "?" if DB_TYPE == "sqlite" else "%s"
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT report_date, status, payload_json, ai_commentary_json,
                       source_status_json, generated_at, telegram_sent_at
                FROM business_analytics_reports
                WHERE telegram_user_id = {placeholder} AND status = 'complete'
                ORDER BY report_date DESC, generated_at DESC
                LIMIT 1
            """, (telegram_user_id,))
            row = cursor.fetchone()
            columns = [desc[0] for desc in cursor.description]
            conn.close()
            if not row:
                return None
            value = dict(zip(columns, row))
            report = json.loads(value['payload_json'])
            report['ai_commentary'] = (
                json.loads(value['ai_commentary_json']) if value.get('ai_commentary_json') else None
            )
            report['stored_generated_at'] = str(value['generated_at'])
            report['telegram_sent_at'] = str(value['telegram_sent_at']) if value.get('telegram_sent_at') else None
            return report
        except Exception as e:
            logger.error(f"Failed to load latest business analytics report: {e}")
            return None

    def mark_business_report_sent(self, telegram_user_id: int, report_date: str) -> bool:
        """Record successful Telegram delivery without changing report facts."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            placeholder = "?" if DB_TYPE == "sqlite" else "%s"
            cursor.execute(f"""
                UPDATE business_analytics_reports
                SET telegram_sent_at = CURRENT_TIMESTAMP
                WHERE telegram_user_id = {placeholder} AND report_date = {placeholder}
            """, (telegram_user_id, report_date))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Failed to mark business report as sent: {e}")
            return False

    def get_business_daily_metrics(self, telegram_user_id: int, days: int = 30) -> list:
        """Return combined daily metrics for dashboard charts."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            placeholder = "?" if DB_TYPE == "sqlite" else "%s"
            cursor.execute(f"""
                SELECT metric_date, revenue, checks, average_check, expenses,
                       supplies, non_supply_expenses, profit_withdrawals, capital_balance
                FROM business_daily_metrics
                WHERE telegram_user_id = {placeholder} AND store_id = 'total'
                ORDER BY metric_date DESC
                LIMIT {max(1, min(int(days), 90))}
            """, (telegram_user_id,))
            columns = [desc[0] for desc in cursor.description]
            rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
            conn.close()
            rows.reverse()
            for item in rows:
                item['metric_date'] = str(item['metric_date'])
                for key in ('revenue', 'average_check', 'expenses', 'supplies',
                            'non_supply_expenses', 'profit_withdrawals', 'capital_balance'):
                    item[key] = float(item[key]) if item.get(key) is not None else None
            return rows
        except Exception as e:
            logger.error(f"Failed to load business daily metrics: {e}")
            return []

    def update_web_user(self, user_id: int, telegram_user_id: int, username: str = None,
                        role: str = None, label: str = None, is_active: int = None,
                        poster_account_id: int = None) -> bool:
        """Update web user details. Only the telegram owner can update."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            placeholder = "?" if DB_TYPE == "sqlite" else "%s"
            updates = []
            params = []

            if username is not None:
                updates.append(f"username = {placeholder}")
                params.append(username.strip())
            if role is not None:
                updates.append(f"role = {placeholder}")
                params.append(role)
            if label is not None:
                updates.append(f"label = {placeholder}")
                params.append(label.strip())
            if is_active is not None:
                updates.append(f"is_active = {placeholder}")
                params.append(1 if is_active else 0)
            if poster_account_id is not None:
                if poster_account_id == 0 or poster_account_id == -1:
                    updates.append("poster_account_id = NULL")
                else:
                    updates.append(f"poster_account_id = {placeholder}")
                    params.append(poster_account_id)

            if not updates:
                conn.close()
                return True

            params.extend([user_id, telegram_user_id])
            sql = f"""
                UPDATE web_users
                SET {', '.join(updates)}
                WHERE id = {placeholder} AND telegram_user_id = {placeholder}
            """
            cursor.execute(sql, tuple(params))
            conn.commit()
            affected = cursor.rowcount
            conn.close()
            return affected > 0

        except Exception as e:
            logger.error(f"Failed to update web user: {e}")
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
        source: str = 'cash',
        supplier_id: int = None
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
                    (telegram_user_id, supplier_name, invoice_date, total_sum, linked_expense_draft_id, account_id, source, supplier_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (telegram_user_id, supplier_name, invoice_date, total_sum, linked_expense_draft_id, account_id, source, supplier_id))
                supply_draft_id = cursor.lastrowid
            else:
                cursor.execute("""
                    INSERT INTO supply_drafts
                    (telegram_user_id, supplier_name, invoice_date, total_sum, linked_expense_draft_id, account_id, source, supplier_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (telegram_user_id, supplier_name, invoice_date, total_sum, linked_expense_draft_id, account_id, source, supplier_id))
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
        storage_name: str = None,
        parsed_quantity: float = None,
        parsed_unit: str = None,
        parsed_price_per_unit: float = None
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
                     item_type, storage_id, storage_name, parsed_quantity, parsed_unit, parsed_price_per_unit)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (supply_draft_id, item_name, quantity, unit, price_per_unit, total,
                      poster_ingredient_id, poster_ingredient_name, poster_account_id, poster_account_name,
                      item_type, storage_id, storage_name, parsed_quantity, parsed_unit, parsed_price_per_unit))
                item_id = cursor.lastrowid
            else:
                cursor.execute("""
                    INSERT INTO supply_draft_items
                    (supply_draft_id, item_name, quantity, unit, price_per_unit, total,
                     poster_ingredient_id, poster_ingredient_name, poster_account_id, poster_account_name,
                     item_type, storage_id, storage_name, parsed_quantity, parsed_unit, parsed_price_per_unit)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (supply_draft_id, item_name, quantity, unit, price_per_unit, total,
                      poster_ingredient_id, poster_ingredient_name, poster_account_id, poster_account_name,
                      item_type, storage_id, storage_name, parsed_quantity, parsed_unit, parsed_price_per_unit))
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

    def clear_supply_draft_items(self, supply_draft_id: int) -> bool:
        """Удалить все позиции из черновика поставки."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            if DB_TYPE == "sqlite":
                cursor.execute("DELETE FROM supply_draft_items WHERE supply_draft_id = ?", (supply_draft_id,))
            else:
                cursor.execute("DELETE FROM supply_draft_items WHERE supply_draft_id = %s", (supply_draft_id,))

            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Failed to clear supply draft items for draft {supply_draft_id}: {e}")
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

    def _migrate_assistant_chat(self):
        """Create assistant_chat_messages table for conversational interface"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            if DB_TYPE == "sqlite":
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS assistant_chat_messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        telegram_user_id INTEGER NOT NULL,
                        sender TEXT NOT NULL,
                        message_text TEXT NOT NULL,
                        media_paths TEXT,
                        model_name TEXT,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id) ON DELETE CASCADE
                    )
                """)
            else:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS assistant_chat_messages (
                        id SERIAL PRIMARY KEY,
                        telegram_user_id BIGINT NOT NULL,
                        sender VARCHAR(20) NOT NULL,
                        message_text TEXT NOT NULL,
                        media_paths TEXT,
                        model_name TEXT,
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id) ON DELETE CASCADE
                    )
                """)
            conn.commit()
            conn.close()
            logger.info("✅ Created assistant_chat_messages table")

            # Migration: add model_name column if it does not exist
            try:
                conn = self._get_connection()
                cursor = conn.cursor()
                if DB_TYPE == "sqlite":
                    cursor.execute("ALTER TABLE assistant_chat_messages ADD COLUMN model_name TEXT DEFAULT NULL")
                else:
                    cursor.execute("ALTER TABLE assistant_chat_messages ADD COLUMN IF NOT EXISTS model_name TEXT DEFAULT NULL")
                conn.commit()
                conn.close()
                logger.info("✅ Migrated assistant_chat_messages: added model_name column")
            except Exception as e:
                pass
        except Exception as e:
            logger.error(f"❌ Failed to create assistant_chat_messages table: {e}")

    def _migrate_whatsapp_queue(self):
        """Create durable WhatsApp batches/jobs and recover interrupted jobs."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            if DB_TYPE == "sqlite":
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS whatsapp_batches (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        telegram_user_id INTEGER NOT NULL,
                        chat_id TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'collecting',
                        total_jobs INTEGER NOT NULL DEFAULT 0,
                        completed_jobs INTEGER NOT NULL DEFAULT 0,
                        failed_jobs INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        last_received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        summary_sent_at TEXT,
                        FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id) ON DELETE CASCADE
                    )
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS whatsapp_jobs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        batch_id INTEGER NOT NULL,
                        telegram_user_id INTEGER NOT NULL,
                        chat_id TEXT NOT NULL,
                        message_id TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'pending',
                        response_text TEXT,
                        created_drafts TEXT,
                        result_json TEXT,
                        error_text TEXT,
                        attempts INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        started_at TEXT,
                        completed_at TEXT,
                        UNIQUE(chat_id, message_id),
                        FOREIGN KEY (batch_id) REFERENCES whatsapp_batches(id) ON DELETE CASCADE,
                        FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id) ON DELETE CASCADE
                    )
                """)
            else:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS whatsapp_batches (
                        id SERIAL PRIMARY KEY,
                        telegram_user_id BIGINT NOT NULL,
                        chat_id TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'collecting',
                        total_jobs INTEGER NOT NULL DEFAULT 0,
                        completed_jobs INTEGER NOT NULL DEFAULT 0,
                        failed_jobs INTEGER NOT NULL DEFAULT 0,
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        last_received_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        summary_sent_at TIMESTAMP,
                        FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id) ON DELETE CASCADE
                    )
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS whatsapp_jobs (
                        id SERIAL PRIMARY KEY,
                        batch_id INTEGER NOT NULL,
                        telegram_user_id BIGINT NOT NULL,
                        chat_id TEXT NOT NULL,
                        message_id TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'pending',
                        response_text TEXT,
                        created_drafts TEXT,
                        result_json TEXT,
                        error_text TEXT,
                        attempts INTEGER NOT NULL DEFAULT 0,
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        started_at TIMESTAMP,
                        completed_at TIMESTAMP,
                        UNIQUE(chat_id, message_id),
                        FOREIGN KEY (batch_id) REFERENCES whatsapp_batches(id) ON DELETE CASCADE,
                        FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id) ON DELETE CASCADE
                    )
                """)

            if DB_TYPE == "sqlite":
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS whatsapp_reviews (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        telegram_user_id INTEGER NOT NULL,
                        chat_id TEXT NOT NULL,
                        batch_id INTEGER,
                        supply_draft_id INTEGER NOT NULL,
                        supply_item_id INTEGER NOT NULL,
                        original_item_name TEXT NOT NULL,
                        candidates_json TEXT NOT NULL DEFAULT '[]',
                        selected_candidate_json TEXT,
                        status TEXT NOT NULL DEFAULT 'pending',
                        prompted_at TEXT,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT,
                        UNIQUE(chat_id, supply_item_id),
                        FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id) ON DELETE CASCADE,
                        FOREIGN KEY (batch_id) REFERENCES whatsapp_batches(id) ON DELETE SET NULL,
                        FOREIGN KEY (supply_draft_id) REFERENCES supply_drafts(id) ON DELETE CASCADE,
                        FOREIGN KEY (supply_item_id) REFERENCES supply_draft_items(id) ON DELETE CASCADE
                    )
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS whatsapp_review_messages (
                        chat_id TEXT NOT NULL,
                        message_id TEXT NOT NULL,
                        handled_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (chat_id, message_id)
                    )
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS whatsapp_draft_actions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        telegram_user_id INTEGER NOT NULL,
                        chat_id TEXT NOT NULL,
                        batch_id INTEGER,
                        supply_draft_id INTEGER NOT NULL,
                        status TEXT NOT NULL DEFAULT 'pending',
                        prompted_at TEXT,
                        result_text TEXT,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT,
                        UNIQUE(chat_id, supply_draft_id),
                        FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id) ON DELETE CASCADE,
                        FOREIGN KEY (batch_id) REFERENCES whatsapp_batches(id) ON DELETE SET NULL,
                        FOREIGN KEY (supply_draft_id) REFERENCES supply_drafts(id) ON DELETE CASCADE
                    )
                """)
            else:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS whatsapp_reviews (
                        id SERIAL PRIMARY KEY,
                        telegram_user_id BIGINT NOT NULL,
                        chat_id TEXT NOT NULL,
                        batch_id INTEGER,
                        supply_draft_id INTEGER NOT NULL,
                        supply_item_id INTEGER NOT NULL,
                        original_item_name TEXT NOT NULL,
                        candidates_json TEXT NOT NULL DEFAULT '[]',
                        selected_candidate_json TEXT,
                        status TEXT NOT NULL DEFAULT 'pending',
                        prompted_at TIMESTAMP,
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP,
                        UNIQUE(chat_id, supply_item_id),
                        FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id) ON DELETE CASCADE,
                        FOREIGN KEY (batch_id) REFERENCES whatsapp_batches(id) ON DELETE SET NULL,
                        FOREIGN KEY (supply_draft_id) REFERENCES supply_drafts(id) ON DELETE CASCADE,
                        FOREIGN KEY (supply_item_id) REFERENCES supply_draft_items(id) ON DELETE CASCADE
                    )
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS whatsapp_review_messages (
                        chat_id TEXT NOT NULL,
                        message_id TEXT NOT NULL,
                        handled_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (chat_id, message_id)
                    )
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS whatsapp_draft_actions (
                        id SERIAL PRIMARY KEY,
                        telegram_user_id BIGINT NOT NULL,
                        chat_id TEXT NOT NULL,
                        batch_id INTEGER,
                        supply_draft_id INTEGER NOT NULL,
                        status TEXT NOT NULL DEFAULT 'pending',
                        prompted_at TIMESTAMP,
                        result_text TEXT,
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP,
                        UNIQUE(chat_id, supply_draft_id),
                        FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id) ON DELETE CASCADE,
                        FOREIGN KEY (batch_id) REFERENCES whatsapp_batches(id) ON DELETE SET NULL,
                        FOREIGN KEY (supply_draft_id) REFERENCES supply_drafts(id) ON DELETE CASCADE
                    )
                """)

            # Existing production databases already have whatsapp_jobs.
            # Add the structured result separately so the migration remains
            # safe for both a fresh install and an upgrade.
            try:
                if DB_TYPE == "sqlite":
                    cursor.execute("ALTER TABLE whatsapp_jobs ADD COLUMN result_json TEXT")
                else:
                    cursor.execute("ALTER TABLE whatsapp_jobs ADD COLUMN IF NOT EXISTS result_json TEXT")
            except Exception:
                if DB_TYPE != "sqlite":
                    raise

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_whatsapp_jobs_status
                ON whatsapp_jobs(status, id)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_whatsapp_batches_open
                ON whatsapp_batches(telegram_user_id, chat_id, summary_sent_at, last_received_at)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_whatsapp_reviews_prompt
                ON whatsapp_reviews(chat_id, status, id)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_whatsapp_draft_actions_prompt
                ON whatsapp_draft_actions(chat_id, status, id)
            """)
            # A deploy may interrupt a job after it has been claimed. Returning
            # it to pending makes the message retryable; message_id prevents a
            # second webhook delivery from creating a second job.
            cursor.execute("""
                UPDATE whatsapp_jobs
                SET status = 'pending', started_at = NULL
                WHERE status = 'processing'
            """)
            # An unanswered prompt must survive a restart visibly. Requeue it
            # once so the user is not left with a silent, blocked conversation.
            cursor.execute("""
                UPDATE whatsapp_reviews
                SET prompted_at = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE status IN ('awaiting_choice', 'awaiting_memory')
            """)
            # Poster submission is idempotent by the draft marker. If a deploy
            # interrupted an approval, ask again instead of leaving it stuck.
            cursor.execute("""
                UPDATE whatsapp_draft_actions
                SET status = 'pending', prompted_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE status IN ('awaiting_choice', 'processing')
            """)
            conn.commit()
            conn.close()
            logger.info("✅ WhatsApp queue migration: tables ready")
        except Exception as e:
            logger.error(f"❌ WhatsApp queue migration error: {e}")

    def enqueue_whatsapp_job(
        self,
        telegram_user_id: int,
        chat_id: str,
        message_id: str,
        payload_json: str,
        batch_window_seconds: int = 45,
    ) -> Dict:
        """Add one webhook to the latest quiet-window batch, idempotently."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor) if DB_TYPE != "sqlite" else conn.cursor()
            ph = "?" if DB_TYPE == "sqlite" else "%s"

            # Several Green-API webhooks can arrive on different Gunicorn
            # threads at the same moment. Serialize enqueueing per chat so all
            # messages join one batch and the unique message key stays clean.
            if DB_TYPE == "sqlite":
                cursor.execute("BEGIN IMMEDIATE")
            else:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))",
                    (f"{telegram_user_id}:{chat_id}",),
                )

            cursor.execute(
                f"SELECT id, batch_id, status FROM whatsapp_jobs WHERE chat_id = {ph} AND message_id = {ph}",
                (chat_id, message_id),
            )
            existing = cursor.fetchone()
            if existing:
                existing_dict = dict(existing)
                conn.close()
                return {
                    'job_id': existing_dict['id'],
                    'batch_id': existing_dict['batch_id'],
                    'duplicate': True,
                    'new_batch': False,
                }

            cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=batch_window_seconds)
            cutoff_value = cutoff.strftime('%Y-%m-%d %H:%M:%S') if DB_TYPE == "sqlite" else cutoff
            lock_suffix = " FOR UPDATE" if DB_TYPE != "sqlite" else ""
            cursor.execute(
                f"""
                SELECT id FROM whatsapp_batches
                WHERE telegram_user_id = {ph}
                  AND chat_id = {ph}
                  AND summary_sent_at IS NULL
                  AND status IN ('collecting', 'processing')
                  AND last_received_at >= {ph}
                ORDER BY id DESC
                LIMIT 1{lock_suffix}
                """,
                (telegram_user_id, chat_id, cutoff_value),
            )
            batch_row = cursor.fetchone()
            new_batch = not bool(batch_row)

            if batch_row:
                batch_id = dict(batch_row)['id']
            elif DB_TYPE == "sqlite":
                cursor.execute(
                    "INSERT INTO whatsapp_batches (telegram_user_id, chat_id) VALUES (?, ?)",
                    (telegram_user_id, chat_id),
                )
                batch_id = cursor.lastrowid
            else:
                cursor.execute(
                    "INSERT INTO whatsapp_batches (telegram_user_id, chat_id) VALUES (%s, %s) RETURNING id",
                    (telegram_user_id, chat_id),
                )
                batch_id = cursor.fetchone()['id']

            if DB_TYPE == "sqlite":
                cursor.execute(
                    """
                    INSERT INTO whatsapp_jobs
                    (batch_id, telegram_user_id, chat_id, message_id, payload_json)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (batch_id, telegram_user_id, chat_id, message_id, payload_json),
                )
                job_id = cursor.lastrowid
                cursor.execute(
                    """
                    UPDATE whatsapp_batches
                    SET total_jobs = total_jobs + 1,
                        last_received_at = CURRENT_TIMESTAMP,
                        status = 'collecting'
                    WHERE id = ?
                    """,
                    (batch_id,),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO whatsapp_jobs
                    (batch_id, telegram_user_id, chat_id, message_id, payload_json)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (batch_id, telegram_user_id, chat_id, message_id, payload_json),
                )
                job_id = cursor.fetchone()['id']
                cursor.execute(
                    """
                    UPDATE whatsapp_batches
                    SET total_jobs = total_jobs + 1,
                        last_received_at = CURRENT_TIMESTAMP,
                        status = 'collecting'
                    WHERE id = %s
                    """,
                    (batch_id,),
                )

            conn.commit()
            conn.close()
            return {
                'job_id': job_id,
                'batch_id': batch_id,
                'duplicate': False,
                'new_batch': new_batch,
            }
        except Exception:
            conn.rollback()
            conn.close()
            raise

    def claim_next_whatsapp_job(self) -> Optional[Dict]:
        """Atomically claim the oldest pending WhatsApp job."""
        conn = self._get_connection()
        try:
            if DB_TYPE == "sqlite":
                cursor = conn.cursor()
                cursor.execute("BEGIN IMMEDIATE")
                cursor.execute("SELECT * FROM whatsapp_jobs WHERE status = 'pending' ORDER BY id LIMIT 1")
                row = cursor.fetchone()
                if not row:
                    conn.commit()
                    conn.close()
                    return None
                job = dict(row)
                cursor.execute(
                    """
                    UPDATE whatsapp_jobs
                    SET status = 'processing', attempts = attempts + 1,
                        started_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND status = 'pending'
                    """,
                    (job['id'],),
                )
            else:
                cursor = conn.cursor(cursor_factory=RealDictCursor)
                cursor.execute("""
                    SELECT * FROM whatsapp_jobs
                    WHERE status = 'pending'
                    ORDER BY id
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                """)
                row = cursor.fetchone()
                if not row:
                    conn.commit()
                    conn.close()
                    return None
                job = dict(row)
                cursor.execute("""
                    UPDATE whatsapp_jobs
                    SET status = 'processing', attempts = attempts + 1,
                        started_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (job['id'],))

            cursor.execute(
                ("UPDATE whatsapp_batches SET status = 'processing' WHERE id = ?"
                 if DB_TYPE == "sqlite" else
                 "UPDATE whatsapp_batches SET status = 'processing' WHERE id = %s"),
                (job['batch_id'],),
            )
            conn.commit()
            job['attempts'] = int(job.get('attempts') or 0) + 1
            job['status'] = 'processing'
            conn.close()
            return job
        except Exception:
            conn.rollback()
            conn.close()
            raise

    def finish_whatsapp_job(
        self,
        job_id: int,
        response_text: str,
        created_drafts_json: str,
        result_json: Optional[str] = None,
    ) -> bool:
        """Mark a claimed WhatsApp job completed and update batch counters."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            ph = "?" if DB_TYPE == "sqlite" else "%s"
            cursor.execute(f"SELECT batch_id, status FROM whatsapp_jobs WHERE id = {ph}", (job_id,))
            row = cursor.fetchone()
            if not row:
                conn.close()
                return False
            batch_id = row['batch_id'] if hasattr(row, 'keys') else row[0]
            old_status = row['status'] if hasattr(row, 'keys') else row[1]
            if old_status == 'completed':
                conn.close()
                return True
            cursor.execute(
                f"""
                UPDATE whatsapp_jobs
                SET status = 'completed', response_text = {ph}, created_drafts = {ph},
                    result_json = {ph}, error_text = NULL, completed_at = CURRENT_TIMESTAMP
                WHERE id = {ph}
                """,
                (response_text, created_drafts_json, result_json, job_id),
            )
            cursor.execute(
                f"UPDATE whatsapp_batches SET completed_jobs = completed_jobs + 1 WHERE id = {ph}",
                (batch_id,),
            )
            conn.commit()
            conn.close()
            return True
        except Exception:
            conn.rollback()
            conn.close()
            raise

    def fail_whatsapp_job(self, job_id: int, error_text: str, max_attempts: int = 2) -> str:
        """Retry a failed job once, then mark it permanently failed."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            ph = "?" if DB_TYPE == "sqlite" else "%s"
            cursor.execute(f"SELECT batch_id, attempts FROM whatsapp_jobs WHERE id = {ph}", (job_id,))
            row = cursor.fetchone()
            if not row:
                conn.close()
                return 'missing'
            batch_id = row['batch_id'] if hasattr(row, 'keys') else row[0]
            attempts = int((row['attempts'] if hasattr(row, 'keys') else row[1]) or 0)
            if attempts < max_attempts:
                cursor.execute(
                    f"""
                    UPDATE whatsapp_jobs
                    SET status = 'pending', error_text = {ph}, started_at = NULL
                    WHERE id = {ph}
                    """,
                    (error_text[:2000], job_id),
                )
                result = 'pending'
            else:
                cursor.execute(
                    f"""
                    UPDATE whatsapp_jobs
                    SET status = 'failed', error_text = {ph}, completed_at = CURRENT_TIMESTAMP
                    WHERE id = {ph}
                    """,
                    (error_text[:2000], job_id),
                )
                cursor.execute(
                    f"UPDATE whatsapp_batches SET failed_jobs = failed_jobs + 1 WHERE id = {ph}",
                    (batch_id,),
                )
                result = 'failed'
            conn.commit()
            conn.close()
            return result
        except Exception:
            conn.rollback()
            conn.close()
            raise

    def get_ready_whatsapp_batches(self, settle_seconds: int = 20) -> list:
        """Return quiet batches whose jobs all reached a terminal state."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor) if DB_TYPE != "sqlite" else conn.cursor()
            ph = "?" if DB_TYPE == "sqlite" else "%s"
            if DB_TYPE == "sqlite":
                cursor.execute("BEGIN IMMEDIATE")
            cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=settle_seconds)
            cutoff_value = cutoff.strftime('%Y-%m-%d %H:%M:%S') if DB_TYPE == "sqlite" else cutoff
            lock_suffix = " FOR UPDATE OF b SKIP LOCKED" if DB_TYPE != "sqlite" else ""
            cursor.execute(f"""
                SELECT b.*
                FROM whatsapp_batches b
                WHERE b.summary_sent_at IS NULL
                  AND b.last_received_at <= {ph}
                  AND NOT EXISTS (
                      SELECT 1 FROM whatsapp_jobs j
                      WHERE j.batch_id = b.id
                        AND j.status IN ('pending', 'processing')
                  )
                ORDER BY b.id
                {lock_suffix}
            """, (cutoff_value,))
            batches = [dict(row) for row in cursor.fetchall()]
            for batch in batches:
                cursor.execute(
                    f"UPDATE whatsapp_batches SET status = 'summarizing' WHERE id = {ph}",
                    (batch['id'],),
                )
                cursor.execute(
                    f"SELECT * FROM whatsapp_jobs WHERE batch_id = {ph} ORDER BY id",
                    (batch['id'],),
                )
                batch['jobs'] = [dict(row) for row in cursor.fetchall()]
            conn.commit()
            conn.close()
            return batches
        except Exception:
            conn.rollback()
            conn.close()
            raise

    def mark_whatsapp_batch_summary_sent(self, batch_id: int) -> bool:
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            ph = "?" if DB_TYPE == "sqlite" else "%s"
            cursor.execute(
                f"""
                UPDATE whatsapp_batches
                SET status = 'completed', summary_sent_at = CURRENT_TIMESTAMP
                WHERE id = {ph} AND summary_sent_at IS NULL
                """,
                (batch_id,),
            )
            changed = cursor.rowcount > 0
            conn.commit()
            conn.close()
            return changed
        except Exception:
            conn.rollback()
            conn.close()
            raise

    def enqueue_whatsapp_review(
        self,
        telegram_user_id: int,
        chat_id: str,
        batch_id: int,
        supply_draft_id: int,
        supply_item_id: int,
        original_item_name: str,
        candidates_json: str,
    ) -> Optional[int]:
        """Queue one unmatched supply row for an explicit WhatsApp choice."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor) if DB_TYPE != "sqlite" else conn.cursor()
            ph = "?" if DB_TYPE == "sqlite" else "%s"
            if DB_TYPE == "sqlite":
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO whatsapp_reviews
                    (telegram_user_id, chat_id, batch_id, supply_draft_id,
                     supply_item_id, original_item_name, candidates_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        telegram_user_id, chat_id, batch_id, supply_draft_id,
                        supply_item_id, original_item_name, candidates_json,
                    ),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO whatsapp_reviews
                    (telegram_user_id, chat_id, batch_id, supply_draft_id,
                     supply_item_id, original_item_name, candidates_json)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (chat_id, supply_item_id) DO NOTHING
                    """,
                    (
                        telegram_user_id, chat_id, batch_id, supply_draft_id,
                        supply_item_id, original_item_name, candidates_json,
                    ),
                )
            cursor.execute(
                f"SELECT id FROM whatsapp_reviews WHERE chat_id = {ph} AND supply_item_id = {ph}",
                (chat_id, supply_item_id),
            )
            row = cursor.fetchone()
            review_id = (row['id'] if hasattr(row, 'keys') else row[0]) if row else None
            conn.commit()
            conn.close()
            return review_id
        except Exception:
            conn.rollback()
            conn.close()
            raise

    def get_active_whatsapp_review(self, telegram_user_id: int, chat_id: str) -> Optional[Dict]:
        """Return the single question currently awaiting a numeric reply."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor) if DB_TYPE != "sqlite" else conn.cursor()
            ph = "?" if DB_TYPE == "sqlite" else "%s"
            cursor.execute(f"""
                SELECT * FROM whatsapp_reviews
                WHERE telegram_user_id = {ph} AND chat_id = {ph}
                  AND status IN ('awaiting_choice', 'awaiting_memory')
                ORDER BY id
                LIMIT 1
            """, (telegram_user_id, chat_id))
            row = cursor.fetchone()
            conn.close()
            return dict(row) if row else None
        except Exception:
            conn.close()
            raise

    def reserve_whatsapp_review_message(self, chat_id: str, message_id: str) -> bool:
        """Deduplicate a numeric reply before it can advance review state."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            if DB_TYPE == "sqlite":
                cursor.execute(
                    "INSERT OR IGNORE INTO whatsapp_review_messages (chat_id, message_id) VALUES (?, ?)",
                    (chat_id, message_id),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO whatsapp_review_messages (chat_id, message_id)
                    VALUES (%s, %s)
                    ON CONFLICT (chat_id, message_id) DO NOTHING
                    """,
                    (chat_id, message_id),
                )
            reserved = cursor.rowcount > 0
            conn.commit()
            conn.close()
            return reserved
        except Exception:
            conn.rollback()
            conn.close()
            raise

    def is_whatsapp_interaction_message_handled(self, chat_id: str, message_id: str) -> bool:
        """Check whether a numeric WhatsApp reply was already consumed."""
        if not message_id:
            return False
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            ph = "?" if DB_TYPE == "sqlite" else "%s"
            cursor.execute(
                f"SELECT 1 FROM whatsapp_review_messages WHERE chat_id = {ph} AND message_id = {ph}",
                (chat_id, message_id),
            )
            handled = cursor.fetchone() is not None
            conn.close()
            return handled
        except Exception:
            conn.close()
            raise

    def requeue_active_whatsapp_prompt(self, chat_id: str) -> bool:
        """Make an unanswered question visible again after a newer batch summary."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            ph = "?" if DB_TYPE == "sqlite" else "%s"
            cursor.execute(f"""
                UPDATE whatsapp_reviews
                SET prompted_at = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE chat_id = {ph}
                  AND status IN ('awaiting_choice', 'awaiting_memory')
            """, (chat_id,))
            changed = cursor.rowcount > 0
            cursor.execute(f"""
                UPDATE whatsapp_draft_actions
                SET status = 'pending', prompted_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE chat_id = {ph} AND status = 'awaiting_choice'
            """, (chat_id,))
            changed = cursor.rowcount > 0 or changed
            conn.commit()
            conn.close()
            return changed
        except Exception:
            conn.rollback()
            conn.close()
            raise

    def get_whatsapp_reviews_needing_prompt(self, chat_id: Optional[str] = None) -> list:
        """Return at most one unsent review prompt per chat."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor) if DB_TYPE != "sqlite" else conn.cursor()
            ph = "?" if DB_TYPE == "sqlite" else "%s"
            params = []
            chat_filter = ''
            if chat_id is not None:
                chat_filter = f" AND r.chat_id = {ph}"
                params.append(chat_id)
            cursor.execute(f"""
                SELECT r.*
                FROM whatsapp_reviews r
                WHERE r.prompted_at IS NULL
                  AND NOT EXISTS (
                    SELECT 1 FROM whatsapp_draft_actions active_action
                    WHERE active_action.chat_id = r.chat_id
                      AND active_action.status = 'awaiting_choice'
                  )
                  AND (
                    r.batch_id IS NULL
                    OR EXISTS (
                      SELECT 1 FROM whatsapp_batches b
                      WHERE b.id = r.batch_id AND b.summary_sent_at IS NOT NULL
                    )
                  )
                  AND (
                    r.status IN ('awaiting_choice', 'awaiting_memory')
                    OR (
                      r.status = 'pending'
                      AND NOT EXISTS (
                        SELECT 1 FROM whatsapp_reviews active
                        WHERE active.chat_id = r.chat_id
                          AND active.status IN ('awaiting_choice', 'awaiting_memory')
                      )
                    )
                  )
                  {chat_filter}
                ORDER BY r.id
            """, params)
            rows = [dict(row) for row in cursor.fetchall()]
            conn.close()
            first_by_chat = {}
            for row in rows:
                first_by_chat.setdefault(row['chat_id'], row)
            return list(first_by_chat.values())
        except Exception:
            conn.close()
            raise

    def mark_whatsapp_review_prompted(self, review_id: int) -> bool:
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            ph = "?" if DB_TYPE == "sqlite" else "%s"
            cursor.execute(f"""
                UPDATE whatsapp_reviews
                SET status = CASE WHEN status = 'pending' THEN 'awaiting_choice' ELSE status END,
                    prompted_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = {ph} AND status IN ('pending', 'awaiting_choice', 'awaiting_memory')
            """, (review_id,))
            changed = cursor.rowcount > 0
            conn.commit()
            conn.close()
            return changed
        except Exception:
            conn.rollback()
            conn.close()
            raise

    def update_pending_whatsapp_review_candidates(
        self, review_id: int, candidates_json: str
    ) -> bool:
        """Refresh suggestions before a pending question is first shown."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            ph = "?" if DB_TYPE == "sqlite" else "%s"
            cursor.execute(f"""
                UPDATE whatsapp_reviews
                SET candidates_json = {ph}, updated_at = CURRENT_TIMESTAMP
                WHERE id = {ph} AND status = 'pending' AND prompted_at IS NULL
            """, (candidates_json, review_id))
            changed = cursor.rowcount > 0
            conn.commit()
            conn.close()
            return changed
        except Exception:
            conn.rollback()
            conn.close()
            raise

    def select_whatsapp_review_candidate(self, review_id: int, candidate_json: str) -> bool:
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            ph = "?" if DB_TYPE == "sqlite" else "%s"
            cursor.execute(f"""
                UPDATE whatsapp_reviews
                SET selected_candidate_json = {ph}, status = 'awaiting_memory',
                    prompted_at = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE id = {ph} AND status = 'awaiting_choice'
            """, (candidate_json, review_id))
            changed = cursor.rowcount > 0
            conn.commit()
            conn.close()
            return changed
        except Exception:
            conn.rollback()
            conn.close()
            raise

    def complete_whatsapp_review(self, review_id: int, skipped: bool = False) -> bool:
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            ph = "?" if DB_TYPE == "sqlite" else "%s"
            status = 'skipped' if skipped else 'resolved'
            cursor.execute(f"""
                UPDATE whatsapp_reviews
                SET status = {ph}, updated_at = CURRENT_TIMESTAMP
                WHERE id = {ph} AND status IN ('awaiting_choice', 'awaiting_memory')
            """, (status, review_id))
            changed = cursor.rowcount > 0
            conn.commit()
            conn.close()
            return changed
        except Exception:
            conn.rollback()
            conn.close()
            raise

    def enqueue_whatsapp_draft_action(
        self,
        telegram_user_id: int,
        chat_id: str,
        batch_id: Optional[int],
        supply_draft_id: int,
    ) -> Optional[int]:
        """Queue one explicit create/keep decision for a saved supply draft."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor) if DB_TYPE != "sqlite" else conn.cursor()
            ph = "?" if DB_TYPE == "sqlite" else "%s"
            if DB_TYPE == "sqlite":
                cursor.execute("""
                    INSERT OR IGNORE INTO whatsapp_draft_actions
                    (telegram_user_id, chat_id, batch_id, supply_draft_id)
                    VALUES (?, ?, ?, ?)
                """, (telegram_user_id, chat_id, batch_id, supply_draft_id))
            else:
                cursor.execute("""
                    INSERT INTO whatsapp_draft_actions
                    (telegram_user_id, chat_id, batch_id, supply_draft_id)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (chat_id, supply_draft_id) DO NOTHING
                """, (telegram_user_id, chat_id, batch_id, supply_draft_id))
            cursor.execute(
                f"SELECT id FROM whatsapp_draft_actions WHERE chat_id = {ph} AND supply_draft_id = {ph}",
                (chat_id, supply_draft_id),
            )
            row = cursor.fetchone()
            action_id = (row['id'] if hasattr(row, 'keys') else row[0]) if row else None
            conn.commit()
            conn.close()
            return action_id
        except Exception:
            conn.rollback()
            conn.close()
            raise

    def get_active_whatsapp_draft_action(
        self, telegram_user_id: int, chat_id: str
    ) -> Optional[Dict]:
        """Return the one draft decision whose numeric answer is expected."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor) if DB_TYPE != "sqlite" else conn.cursor()
            ph = "?" if DB_TYPE == "sqlite" else "%s"
            cursor.execute(f"""
                SELECT * FROM whatsapp_draft_actions
                WHERE telegram_user_id = {ph} AND chat_id = {ph}
                  AND status = 'awaiting_choice'
                ORDER BY id
                LIMIT 1
            """, (telegram_user_id, chat_id))
            row = cursor.fetchone()
            conn.close()
            return dict(row) if row else None
        except Exception:
            conn.close()
            raise

    def get_whatsapp_draft_actions_needing_prompt(
        self, chat_id: Optional[str] = None
    ) -> list:
        """Return at most one pending draft decision per idle chat."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor) if DB_TYPE != "sqlite" else conn.cursor()
            ph = "?" if DB_TYPE == "sqlite" else "%s"
            params = []
            chat_filter = ''
            if chat_id is not None:
                chat_filter = f" AND a.chat_id = {ph}"
                params.append(chat_id)
            cursor.execute(f"""
                SELECT a.*
                FROM whatsapp_draft_actions a
                WHERE a.status = 'pending' AND a.prompted_at IS NULL
                  AND (
                    a.batch_id IS NULL
                    OR EXISTS (
                      SELECT 1 FROM whatsapp_batches b
                      WHERE b.id = a.batch_id AND b.summary_sent_at IS NOT NULL
                    )
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM whatsapp_reviews r
                    WHERE r.chat_id = a.chat_id
                      AND r.status IN ('pending', 'awaiting_choice', 'awaiting_memory')
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM whatsapp_draft_actions active
                    WHERE active.chat_id = a.chat_id
                      AND active.status = 'awaiting_choice'
                  )
                  {chat_filter}
                ORDER BY a.id
            """, params)
            rows = [dict(row) for row in cursor.fetchall()]
            conn.close()
            first_by_chat = {}
            for row in rows:
                first_by_chat.setdefault(row['chat_id'], row)
            return list(first_by_chat.values())
        except Exception:
            conn.close()
            raise

    def mark_whatsapp_draft_action_prompted(self, action_id: int) -> bool:
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            ph = "?" if DB_TYPE == "sqlite" else "%s"
            cursor.execute(f"""
                UPDATE whatsapp_draft_actions
                SET status = 'awaiting_choice', prompted_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = {ph} AND status = 'pending' AND prompted_at IS NULL
            """, (action_id,))
            changed = cursor.rowcount > 0
            conn.commit()
            conn.close()
            return changed
        except Exception:
            conn.rollback()
            conn.close()
            raise

    def choose_whatsapp_draft_action(self, action_id: int, status: str) -> bool:
        """Atomically claim a create decision or complete a keep decision."""
        if status not in ('processing', 'kept'):
            raise ValueError('Unsupported WhatsApp draft action status')
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            ph = "?" if DB_TYPE == "sqlite" else "%s"
            cursor.execute(f"""
                UPDATE whatsapp_draft_actions
                SET status = {ph}, updated_at = CURRENT_TIMESTAMP
                WHERE id = {ph} AND status = 'awaiting_choice'
            """, (status, action_id))
            changed = cursor.rowcount > 0
            conn.commit()
            conn.close()
            return changed
        except Exception:
            conn.rollback()
            conn.close()
            raise

    def finish_whatsapp_draft_action(
        self, action_id: int, status: str, result_text: str = ''
    ) -> bool:
        if status not in ('created', 'kept', 'blocked', 'failed'):
            raise ValueError('Unsupported WhatsApp draft action result')
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            ph = "?" if DB_TYPE == "sqlite" else "%s"
            cursor.execute(f"""
                UPDATE whatsapp_draft_actions
                SET status = {ph}, result_text = {ph}, updated_at = CURRENT_TIMESTAMP
                WHERE id = {ph} AND status IN ('pending', 'awaiting_choice', 'processing')
            """, (status, result_text[:2000], action_id))
            changed = cursor.rowcount > 0
            conn.commit()
            conn.close()
            return changed
        except Exception:
            conn.rollback()
            conn.close()
            raise

    def _migrate_assistant_memory(self):
        """Create assistant_memory table for storing user-specific notes and rules"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            if DB_TYPE == "sqlite":
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS assistant_memory (
                        telegram_user_id INTEGER PRIMARY KEY,
                        memory_text TEXT NOT NULL DEFAULT '',
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id) ON DELETE CASCADE
                    )
                """)
            else:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS assistant_memory (
                        telegram_user_id BIGINT PRIMARY KEY,
                        memory_text TEXT NOT NULL DEFAULT '',
                        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id) ON DELETE CASCADE
                    )
                """)
            conn.commit()
            conn.close()
            logger.info("✅ Created assistant_memory table")
        except Exception as e:
            logger.error(f"❌ Failed to create assistant_memory table: {e}")

    def _migrate_assistant_memory_versions(self):
        """Create assistant_memory_versions table for storing last N versions with rollback."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            if DB_TYPE == "sqlite":
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS assistant_memory_versions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        telegram_user_id INTEGER NOT NULL,
                        memory_text TEXT NOT NULL,
                        saved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id) ON DELETE CASCADE
                    )
                """)
            else:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS assistant_memory_versions (
                        id SERIAL PRIMARY KEY,
                        telegram_user_id BIGINT NOT NULL,
                        memory_text TEXT NOT NULL,
                        saved_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id) ON DELETE CASCADE
                    )
                """)
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"❌ Failed to create assistant_memory_versions table: {e}")

    def get_assistant_memory(self, telegram_user_id: int) -> str:
        """Get assistant memory text for the user"""
        try:
            conn = self._get_connection()
            if DB_TYPE == "sqlite":
                cursor = conn.cursor()
                cursor.execute("SELECT memory_text FROM assistant_memory WHERE telegram_user_id = ?", (telegram_user_id,))
            else:
                cursor = conn.cursor(cursor_factory=RealDictCursor)
                cursor.execute("SELECT memory_text FROM assistant_memory WHERE telegram_user_id = %s", (telegram_user_id,))
            row = cursor.fetchone()
            val = row['memory_text'] if row else ""
            conn.close()
            return val
        except Exception as e:
            logger.error(f"Error fetching assistant memory: {e}")
            return ""

    def save_assistant_memory(self, telegram_user_id: int, memory_text: str):
        """Save assistant memory text, archiving the previous version first (keeps last 10)."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            ph = "?" if DB_TYPE == "sqlite" else "%s"

            # Archive current version before overwriting
            old_text = None
            cursor.execute(f"SELECT memory_text FROM assistant_memory WHERE telegram_user_id = {ph}", (telegram_user_id,))
            row = cursor.fetchone()
            if row:
                old_text = row['memory_text'] if isinstance(row, dict) else row[0]

            if old_text and old_text.strip():
                if DB_TYPE == "sqlite":
                    cursor.execute("""
                        INSERT INTO assistant_memory_versions (telegram_user_id, memory_text, saved_at)
                        VALUES (?, ?, datetime('now'))
                    """, (telegram_user_id, old_text))
                else:
                    cursor.execute("""
                        INSERT INTO assistant_memory_versions (telegram_user_id, memory_text, saved_at)
                        VALUES (%s, %s, CURRENT_TIMESTAMP)
                    """, (telegram_user_id, old_text))

                # Keep only last 10 versions
                if DB_TYPE == "sqlite":
                    cursor.execute("""
                        DELETE FROM assistant_memory_versions
                        WHERE telegram_user_id = ? AND id NOT IN (
                            SELECT id FROM assistant_memory_versions
                            WHERE telegram_user_id = ?
                            ORDER BY id DESC LIMIT 10
                        )
                    """, (telegram_user_id, telegram_user_id))
                else:
                    cursor.execute("""
                        DELETE FROM assistant_memory_versions
                        WHERE telegram_user_id = %s AND id NOT IN (
                            SELECT id FROM assistant_memory_versions
                            WHERE telegram_user_id = %s
                            ORDER BY id DESC LIMIT 10
                        )
                    """, (telegram_user_id, telegram_user_id))

            # Save new version
            if DB_TYPE == "sqlite":
                cursor.execute("""
                    INSERT INTO assistant_memory (telegram_user_id, memory_text, updated_at)
                    VALUES (?, ?, datetime('now'))
                    ON CONFLICT(telegram_user_id) DO UPDATE SET
                        memory_text = EXCLUDED.memory_text,
                        updated_at = datetime('now')
                """, (telegram_user_id, memory_text))
            else:
                cursor.execute("""
                    INSERT INTO assistant_memory (telegram_user_id, memory_text, updated_at)
                    VALUES (%s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT(telegram_user_id) DO UPDATE SET
                        memory_text = EXCLUDED.memory_text,
                        updated_at = CURRENT_TIMESTAMP
                """, (telegram_user_id, memory_text))
            conn.commit()
            conn.close()
            logger.info(f"✅ Saved assistant memory for user {telegram_user_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to save assistant memory: {e}")
            return False

    def get_assistant_memory_versions(self, telegram_user_id: int, limit: int = 10) -> list:
        """Return recent memory versions (newest first)."""
        try:
            conn = self._get_connection()
            if DB_TYPE == "sqlite":
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, memory_text, saved_at FROM assistant_memory_versions
                    WHERE telegram_user_id = ?
                    ORDER BY id DESC LIMIT ?
                """, (telegram_user_id, limit))
            else:
                cursor = conn.cursor(cursor_factory=RealDictCursor)
                cursor.execute("""
                    SELECT id, memory_text, saved_at FROM assistant_memory_versions
                    WHERE telegram_user_id = %s
                    ORDER BY id DESC LIMIT %s
                """, (telegram_user_id, limit))
            rows = cursor.fetchall()
            conn.close()
            if DB_TYPE == "sqlite":
                return [{'id': r[0], 'memory_text': r[1], 'saved_at': r[2]} for r in rows]
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"Error fetching memory versions: {e}")
            return []

    def rollback_assistant_memory(self, telegram_user_id: int, version_id: int) -> bool:
        """Restore a specific memory version as the current memory."""
        try:
            conn = self._get_connection()
            ph = "?" if DB_TYPE == "sqlite" else "%s"
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT memory_text FROM assistant_memory_versions WHERE id = {ph} AND telegram_user_id = {ph}",
                (version_id, telegram_user_id)
            )
            row = cursor.fetchone()
            conn.close()
            if not row:
                return False
            old_text = row['memory_text'] if isinstance(row, dict) else row[0]
            return self.save_assistant_memory(telegram_user_id, old_text)
        except Exception as e:
            logger.error(f"Error rolling back memory: {e}")
            return False

    def get_assistant_chat_history(self, telegram_user_id: int, limit: int = 50) -> list:
        """Retrieve recent messages in assistant chat history"""
        conn = self._get_connection()
        
        if DB_TYPE == "sqlite":
            cursor = conn.cursor()
            cursor.execute("""
                SELECT sender, message_text, media_paths, model_name, created_at
                FROM assistant_chat_messages
                WHERE telegram_user_id = ?
                ORDER BY created_at DESC
                LIMIT ?
            """, (telegram_user_id, limit))
            rows = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description]
            rows = [dict(zip(columns, row)) for row in rows]
        else:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                SELECT sender, message_text, media_paths, model_name, created_at
                FROM assistant_chat_messages
                WHERE telegram_user_id = %s
                ORDER BY created_at DESC
                LIMIT %s
            """, (telegram_user_id, limit))
            rows = cursor.fetchall()
            
        conn.close()
        
        # We want history in chronological order (oldest first)
        results = [dict(row) for row in rows]
        results.reverse()
        
        # Deserialize JSON media_paths if present and convert timestamps to Almaty TZ
        import json
        from datetime import datetime
        import pytz
        kz_tz = pytz.timezone('Asia/Almaty')
        
        for msg in results:
            if msg.get('media_paths'):
                try:
                    msg['media_paths'] = json.loads(msg['media_paths'])
                except Exception:
                    msg['media_paths'] = []
            else:
                msg['media_paths'] = []
                
            c_at = msg.get('created_at')
            if c_at:
                if isinstance(c_at, str):
                    try:
                        if '.' in c_at:
                            dt = datetime.strptime(c_at, "%Y-%m-%d %H:%M:%S.%f")
                        else:
                            dt = datetime.strptime(c_at, "%Y-%m-%d %H:%M:%S")
                        dt_utc = pytz.utc.localize(dt)
                        dt_kz = dt_utc.astimezone(kz_tz)
                        msg['created_at'] = dt_kz.strftime("%Y-%m-%d %H:%M:%S")
                    except Exception:
                        pass
                elif isinstance(c_at, datetime):
                    if c_at.tzinfo is None:
                        dt_utc = pytz.utc.localize(c_at)
                    else:
                        dt_utc = c_at
                    dt_kz = dt_utc.astimezone(kz_tz)
                    msg['created_at'] = dt_kz.strftime("%Y-%m-%d %H:%M:%S")
                    
        return results

    def add_assistant_chat_message(self, telegram_user_id: int, sender: str, message_text: str, media_paths: Optional[list] = None, model_name: Optional[str] = None) -> int:
        """Add a new chat message to the assistant history"""
        import json
        media_json = json.dumps(media_paths or [])
        
        conn = self._get_connection()
        cursor = conn.cursor()
        
        msg_id = None
        if DB_TYPE == "sqlite":
            cursor.execute("""
                INSERT INTO assistant_chat_messages (telegram_user_id, sender, message_text, media_paths, model_name)
                VALUES (?, ?, ?, ?, ?)
            """, (telegram_user_id, sender, message_text, media_json, model_name))
            msg_id = cursor.lastrowid
        else:
            cursor.execute("""
                INSERT INTO assistant_chat_messages (telegram_user_id, sender, message_text, media_paths, model_name)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
            """, (telegram_user_id, sender, message_text, media_json, model_name))
            res = cursor.fetchone()
            msg_id = res[0] if res else None
            
        conn.commit()
        conn.close()
        return msg_id

    def clear_assistant_chat_history(self, telegram_user_id: int) -> bool:
        """Clear all chat messages for this user"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        if DB_TYPE == "sqlite":
            cursor.execute("DELETE FROM assistant_chat_messages WHERE telegram_user_id = ?", (telegram_user_id,))
        else:
            cursor.execute("DELETE FROM assistant_chat_messages WHERE telegram_user_id = %s", (telegram_user_id,))
            
        conn.commit()
        conn.close()
        return True

    def get_supplier_ingredient_profiles(self, telegram_user_id: int) -> dict:
        """
        Build profiles mapping supplier names to lists of ingredients
        they typically supply, based on processed supplies from the last 60 days.
        """
        from datetime import datetime, timedelta
        date_limit = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
        
        conn = self._get_connection()
        cursor = conn.cursor()
        
        query = """
            SELECT s.supplier_name, i.item_name
            FROM supply_drafts s
            JOIN supply_draft_items i ON s.id = i.supply_draft_id
            WHERE s.telegram_user_id = {} AND s.status = 'processed' AND s.invoice_date >= {}
        """
        
        if DB_TYPE == "sqlite":
            cursor.execute(query.format("?", "?"), (telegram_user_id, date_limit))
            rows = cursor.fetchall()
        else:
            cursor.execute(query.format("%s", "%s"), (telegram_user_id, date_limit))
            rows = cursor.fetchall()
            
        conn.close()
        
        # Build mapping: Supplier -> Set of ingredient names
        profiles = {}
        for row in rows:
            if isinstance(row, dict) or (hasattr(row, 'keys') and not isinstance(row, tuple)):
                supplier = row.get('supplier_name')
                item = row.get('item_name')
            else:
                supplier = row[0]
                item = row[1]
                
            if not supplier or not item:
                continue
                
            supplier_clean = supplier.strip()
            item_clean = item.strip().lower()
            
            if supplier_clean not in profiles:
                profiles[supplier_clean] = set()
            profiles[supplier_clean].add(item_clean)
            
        # Convert sets to sorted lists for JSON serialization
        return {k: sorted(list(v)) for k, v in profiles.items()}



    def _migrate_shift_closings_wedrink(self):
        """Add wedrink_sales column to shift_closings for tracking WeDrink category sales"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            try:
                if DB_TYPE != "sqlite":
                    cursor.execute("SAVEPOINT migration_wedrink_sp")
                cursor.execute("ALTER TABLE shift_closings ADD COLUMN wedrink_sales REAL DEFAULT 0")
                if DB_TYPE != "sqlite":
                    cursor.execute("RELEASE SAVEPOINT migration_wedrink_sp")
                logger.info("✅ WeDrink migration: added wedrink_sales to shift_closings")
            except Exception:
                if DB_TYPE != "sqlite":
                    cursor.execute("ROLLBACK TO SAVEPOINT migration_wedrink_sp")

            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"WeDrink migration error: {e}")

    def _migrate_purchase_sheet(self):
        """Create purchase_suppliers, purchase_ingredients, and purchase_history tables"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            if DB_TYPE == "sqlite":
                # Create purchase_suppliers
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS purchase_suppliers (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        telegram_user_id INTEGER NOT NULL,
                        name TEXT NOT NULL,
                        schedule TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        UNIQUE(telegram_user_id, name)
                    )
                """)
                # Create purchase_ingredients
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS purchase_ingredients (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        telegram_user_id INTEGER NOT NULL,
                        supplier_id INTEGER NOT NULL,
                        name TEXT NOT NULL,
                        poster_ingredient_id INTEGER,
                        default_target_stock REAL,
                        sort_order INTEGER DEFAULT 0,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        FOREIGN KEY (supplier_id) REFERENCES purchase_suppliers(id) ON DELETE CASCADE
                    )
                """)
                # Create purchase_history
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS purchase_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        telegram_user_id INTEGER NOT NULL,
                        date TEXT NOT NULL,
                        supplier_name TEXT NOT NULL,
                        items_json TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                """)
            else:
                # PostgreSQL
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS purchase_suppliers (
                        id SERIAL PRIMARY KEY,
                        telegram_user_id BIGINT NOT NULL,
                        name VARCHAR(255) NOT NULL,
                        schedule TEXT NOT NULL,
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(telegram_user_id, name)
                    )
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS purchase_ingredients (
                        id SERIAL PRIMARY KEY,
                        telegram_user_id BIGINT NOT NULL,
                        supplier_id INTEGER NOT NULL,
                        name VARCHAR(255) NOT NULL,
                        poster_ingredient_id INTEGER,
                        default_target_stock REAL,
                        sort_order INTEGER DEFAULT 0,
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (supplier_id) REFERENCES purchase_suppliers(id) ON DELETE CASCADE
                    )
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS purchase_history (
                        id SERIAL PRIMARY KEY,
                        telegram_user_id BIGINT NOT NULL,
                        date VARCHAR(20) NOT NULL,
                        supplier_name VARCHAR(255) NOT NULL,
                        items_json TEXT NOT NULL,
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                """)

            conn.commit()
            conn.close()
            logger.info("✅ Purchase Sheet migration: tables created successfully")
        except Exception as e:
            logger.error(f"Purchase Sheet migration error: {e}")


# Singleton instance
_db: Optional[UserDatabase] = None


def get_database() -> UserDatabase:
    """Get singleton database instance"""
    global _db
    if _db is None:
        _db = UserDatabase()
    return _db
