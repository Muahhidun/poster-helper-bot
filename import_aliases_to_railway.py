"""Import aliases from local SQLite to Railway PostgreSQL

This script copies all aliases from local SQLite database to Railway PostgreSQL.
Run this ONCE after deploying to Railway to migrate existing aliases.

Usage:
    # Set Railway DATABASE_URL first:
    export DATABASE_URL="postgresql://..."
    python import_aliases_to_railway.py
"""
import os
import sqlite3
import psycopg2
from psycopg2.extras import RealDictCursor
import logging

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


def import_aliases():
    """Import aliases from local SQLite to Railway PostgreSQL"""

    # Check for Railway DATABASE_URL
    railway_db_url = os.getenv("DATABASE_URL")
    if not railway_db_url:
        logger.error("❌ DATABASE_URL not set! Set it to your Railway PostgreSQL URL")
        logger.info("Example: export DATABASE_URL='postgresql://user:pass@host:port/db'")
        return

    # Local SQLite path
    local_db_path = "data/users.db"
    if not os.path.exists(local_db_path):
        logger.error(f"❌ Local database not found: {local_db_path}")
        return

    logger.info("🔄 Starting alias import from SQLite to PostgreSQL...")
    logger.info(f"   Source: {local_db_path}")
    logger.info(f"   Target: Railway PostgreSQL")

    # Connect to both databases
    sqlite_conn = sqlite3.connect(local_db_path)
    sqlite_conn.row_factory = sqlite3.Row
    pg_conn = psycopg2.connect(railway_db_url)

    try:
        # Read all aliases from SQLite
        sqlite_cursor = sqlite_conn.cursor()
        sqlite_cursor.execute("""
            SELECT telegram_user_id, alias_text, poster_item_id,
                   poster_item_name, source, notes
            FROM ingredient_aliases
        """)
        aliases = sqlite_cursor.fetchall()

        logger.info(f"📋 Found {len(aliases)} aliases in local SQLite")

        if not aliases:
            logger.warning("   No aliases to import!")
            return

        # Insert into PostgreSQL
        pg_cursor = pg_conn.cursor()

        imported = 0
        for alias in aliases:
            try:
                pg_cursor.execute("""
                    INSERT INTO ingredient_aliases (
                        telegram_user_id, alias_text, poster_item_id,
                        poster_item_name, source, notes
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (telegram_user_id, alias_text) DO NOTHING
                """, (
                    alias['telegram_user_id'],
                    alias['alias_text'],
                    alias['poster_item_id'],
                    alias['poster_item_name'],
                    alias['source'] if alias['source'] else 'user',
                    alias['notes'] if alias['notes'] else ''
                ))
                imported += 1

            except Exception as e:
                logger.warning(f"   Failed to import '{alias['alias_text']}': {e}")

        pg_conn.commit()

        logger.info(f"✅ Import complete: {imported}/{len(aliases)} aliases imported to Railway")

        # Verify
        pg_cursor.execute("SELECT COUNT(*) FROM ingredient_aliases")
        total = pg_cursor.fetchone()[0]
        logger.info(f"   Total aliases in Railway PostgreSQL: {total}")

    except Exception as e:
        logger.error(f"❌ Import failed: {e}", exc_info=True)
        pg_conn.rollback()

    finally:
        sqlite_conn.close()
        pg_conn.close()


if __name__ == "__main__":
    import_aliases()
