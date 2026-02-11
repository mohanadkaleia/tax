"""Database schema migrations."""

import sqlite3

from app.db.schema import SCHEMA_VERSION


def get_current_version(conn: sqlite3.Connection) -> int:
    """Get the current schema version from the database."""
    try:
        cursor = conn.execute("SELECT MAX(version) FROM schema_version")
        row = cursor.fetchone()
        return row[0] if row and row[0] else 0
    except sqlite3.OperationalError:
        return 0


def migrate(conn: sqlite3.Connection) -> None:
    """Run any pending migrations."""
    current = get_current_version(conn)
    if current >= SCHEMA_VERSION:
        return

    # TODO: Add migration steps as schema evolves
    # Example:
    # if current < 2:
    #     conn.execute("ALTER TABLE lots ADD COLUMN new_field TEXT")
    #     conn.execute("INSERT INTO schema_version (version) VALUES (2)")
    #     conn.commit()

    pass
