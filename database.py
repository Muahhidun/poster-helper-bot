"""Database management for multi-tenant bot"""
import sqlite3
import logging
from pathlib import Path
from typing import Optional, Dict
from datetime import datetime, timedelta
from config import DATABASE_PATH

logger = logging.getLogger(__name__)

DB_PATH = DATABASE_PATH


class UserDatabase:
    """Database for managing user accounts"""

    def __init__(self):
        self.db_path = DB_PATH
        self._init_db()

    def _init_db(self):
        """Initialize database with tables"""
        # Create data directory if not exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Users table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_user_id INTEGER PRIMARY KEY,
                poster_token TEXT NOT NULL,
                poster_user_id TEXT NOT NULL,
                poster_base_url TEXT NOT NULL DEFAULT 'https://joinposter.com/api',
                subscription_status TEXT NOT NULL DEFAULT 'trial',
                subscription_expires_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        # User settings table (optional features)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_settings (
                telegram_user_id INTEGER PRIMARY KEY,
                language TEXT DEFAULT 'ru',
                timezone TEXT DEFAULT 'UTC+6',
                notifications_enabled INTEGER DEFAULT 1,
                FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id)
            )
        """)

        conn.commit()
        conn.close()
        logger.info(f"✅ Database initialized: {self.db_path}")

    def get_user(self, telegram_user_id: int) -> Optional[Dict]:
        """Get user by Telegram ID"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("""
            SELECT * FROM users WHERE telegram_user_id = ?
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
        poster_base_url: str = "https://joinposter.com/api"
    ) -> bool:
        """Create new user"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            now = datetime.now().isoformat()
            trial_expires = (datetime.now() + timedelta(days=14)).isoformat()

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
                trial_expires,
                now,
                now
            ))

            conn.commit()
            conn.close()

            logger.info(f"✅ User created: telegram_id={telegram_user_id}")
            return True

        except sqlite3.IntegrityError:
            logger.error(f"User already exists: telegram_id={telegram_user_id}")
            return False
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
            conn = sqlite3.connect(self.db_path)
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
            params.append(datetime.now().isoformat())
            params.append(telegram_user_id)

            query = f"UPDATE users SET {', '.join(updates)} WHERE telegram_user_id = ?"
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
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute("DELETE FROM users WHERE telegram_user_id = ?", (telegram_user_id,))

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
            expires_at = datetime.fromisoformat(user['subscription_expires_at'])
            if datetime.now() > expires_at:
                # Update status to expired
                self.update_user(telegram_user_id, subscription_status='expired')
                return False

        return True


# Singleton instance
_db: Optional[UserDatabase] = None


def get_database() -> UserDatabase:
    """Get singleton database instance"""
    global _db
    if _db is None:
        _db = UserDatabase()
    return _db
