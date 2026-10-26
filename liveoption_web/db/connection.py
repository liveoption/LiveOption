"""Async SQLite connection helper with WAL PRAGMAs.

All callers open the database through :func:`open_db`, which applies the
same PRAGMA set on every connection.  SQLite PRAGMAs are per-connection
(except ``journal_mode=WAL`` which persists on the database file), so
centralizing them here is the only safe way to guarantee consistent
behavior across the Job Server.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import aiosqlite


@asynccontextmanager
async def open_db(path: Path) -> AsyncIterator[aiosqlite.Connection]:
    """Open an aiosqlite connection with WAL-friendly PRAGMAs.

    Creates the parent directory on demand so callers don't have to
    pre-create ``LIVEOPTION_STATE_DIR``.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(str(path)) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=NORMAL")
        await db.execute("PRAGMA busy_timeout=5000")
        await db.execute("PRAGMA foreign_keys=ON")
        yield db
