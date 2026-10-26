"""SQLite migration runner for liveoption_web.

Migrations live as ``NNN_name.sql`` files inside
``liveoption_web/db/migrations/``.  The integer prefix is the version
number.  A migration is applied exactly once; progress is tracked in the
``schema_version`` table.

Example
-------
>>> from liveoption_web.db.migrations import apply_migrations
>>> await apply_migrations(Path("/var/lib/liveoption_web/state.db"))
"""

from __future__ import annotations

from pathlib import Path

from .connection import open_db

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


async def apply_migrations(db_path: Path) -> None:
    """Apply any pending migrations under ``MIGRATIONS_DIR``.

    Idempotent: re-running after all migrations are applied is a no-op.
    """
    db_path = Path(db_path)
    async with open_db(db_path) as db:
        # Ensure bookkeeping table exists before probing current version.
        # The initial migration also creates it (IF NOT EXISTS), so this
        # is safe on fresh databases too.
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_version (
                version     INTEGER PRIMARY KEY,
                applied_at  TEXT NOT NULL,
                description TEXT
            )
            """
        )
        await db.commit()

        cur = await db.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version")
        row = await cur.fetchone()
        current = int(row[0]) if row and row[0] is not None else 0

        for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
            try:
                version = int(sql_file.name.split("_", 1)[0])
            except ValueError:
                # Non-migration file — skip silently.
                continue
            if version <= current:
                continue
            sql = sql_file.read_text()
            await db.executescript(sql)
            await db.commit()
