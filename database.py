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

        conn.commit()
        conn.close()

        if DB_TYPE == "sqlite":
            logger.info(f"✅ SQLite database initialized: {self.db_path}")
        else:
            logger.info(f"✅ PostgreSQL database initialized")

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


# Singleton instance
_db: Optional[UserDatabase] = None


def get_database() -> UserDatabase:
    """Get singleton database instance"""
    global _db
    if _db is None:
        _db = UserDatabase()
    return _db
