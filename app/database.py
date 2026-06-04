"""
database.py — Async SQLite layer (aiosqlite).
Swap DATABASE_URL for a Postgres DSN to upgrade with zero query changes.
"""
import os
import aiosqlite

DATABASE_URL = os.getenv("DATABASE_URL", "store_intelligence.db")

_DDL = """
CREATE TABLE IF NOT EXISTS events (
    event_id        TEXT PRIMARY KEY,
    store_id        TEXT NOT NULL,
    camera_id       TEXT NOT NULL,
    visitor_id      TEXT NOT NULL,
    event_type      TEXT NOT NULL,
    timestamp       TEXT NOT NULL,
    zone_id         TEXT,
    dwell_ms        INTEGER DEFAULT 0,
    is_staff        INTEGER DEFAULT 0,
    confidence      REAL    NOT NULL,
    queue_depth     INTEGER,
    sku_zone        TEXT,
    session_seq     INTEGER,
    ingested_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_store_ts   ON events(store_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_visitor    ON events(visitor_id);
CREATE INDEX IF NOT EXISTS idx_etype      ON events(store_id, event_type);
CREATE INDEX IF NOT EXISTS idx_zone       ON events(store_id, zone_id);
"""


async def init_db():
    async with aiosqlite.connect(DATABASE_URL) as db:
        for stmt in _DDL.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                await db.execute(stmt)
        await db.commit()


async def get_db():
    try:
        async with aiosqlite.connect(DATABASE_URL) as db:
            db.row_factory = aiosqlite.Row
            yield db
    except Exception as e:
        raise RuntimeError(f"Database unavailable: {e}") from e
