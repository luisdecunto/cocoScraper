"""
migrate_db.py — Copy all data from one PostgreSQL database to another.

Usage:
    python scripts/migrate_db.py <SOURCE_DATABASE_URL> <TARGET_DATABASE_URL>

Example:
    python scripts/migrate_db.py \
        "postgresql://user:pass@neon.host/dbname?sslmode=require" \
        "postgresql://user:pass@render.host/dbname"

What it does:
    1. Creates the schema on the target (idempotent — safe to run again)
    2. Copies each table in dependency order using PostgreSQL COPY protocol
    3. Resets BIGSERIAL sequences so future inserts don't collide
    4. Prints a row-count verification for every table

Notes:
    - Existing rows on the target are deleted before copy (clean migration).
    - Run from the project root: PYTHONPATH=. python scripts/migrate_db.py ...
"""

from __future__ import annotations

import asyncio
import sys

import asyncpg


# Tables in dependency order (products before anything that FKs to it)
TABLES = [
    "users",
    "products",
    "price_snapshots",
    "price_history",
    "run_log",
]

# Sequences to reset after copy: (sequence_name, table, id_column)
SEQUENCES = [
    ("price_snapshots_id_seq", "price_snapshots", "id"),
    ("price_history_id_seq",   "price_history",   "id"),
    ("run_log_id_seq",          "run_log",          "id"),
]


async def get_columns(conn: asyncpg.Connection, table: str) -> list[str]:
    rows = await conn.fetch(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = $1
        ORDER BY ordinal_position;
        """,
        table,
    )
    return [r["column_name"] for r in rows]


async def migrate(source_url: str, target_url: str) -> None:
    print("Connecting to source (Neon)…")
    source = await asyncpg.connect(source_url)

    print("Connecting to target (Render)…")
    target = await asyncpg.connect(target_url)

    try:
        await _run_migration(source, target)
    finally:
        await source.close()
        await target.close()


async def _run_migration(
    source: asyncpg.Connection, target: asyncpg.Connection
) -> None:
    print("\n── Creating schema on target ──────────────────────────────────────")
    from scraper.db import init_schema  # uses target connection indirectly

    # Run the project's own init_schema against the target
    await _init_schema_on(target)

    print("\n── Copying tables ─────────────────────────────────────────────────")
    for table in TABLES:
        columns = await get_columns(source, table)
        if not columns:
            print(f"  {table}: no columns found, skipping")
            continue

        # Fetch all rows from source
        rows = await source.fetch(f"SELECT * FROM {table}")
        src_count = len(rows)

        if src_count == 0:
            print(f"  {table}: 0 rows — skipped")
            continue

        # Truncate target table (CASCADE handles FK children; we do it in reverse order
        # but since we're truncating each individually with RESTART IDENTITY we need
        # no cascade here — just truncate in safe order)
        await target.execute(f"TRUNCATE {table} RESTART IDENTITY CASCADE")

        # COPY using asyncpg's efficient protocol
        records = [tuple(r[c] for c in columns) for r in rows]
        await target.copy_records_to_table(table, records=records, columns=columns)

        dst_count = await target.fetchval(f"SELECT COUNT(*) FROM {table}")
        status = "✓" if dst_count == src_count else "✗ MISMATCH"
        print(f"  {table}: {src_count:,} rows → {dst_count:,} {status}")

    print("\n── Resetting sequences ────────────────────────────────────────────")
    for seq, table, col in SEQUENCES:
        max_id = await target.fetchval(f"SELECT COALESCE(MAX({col}), 0) FROM {table}")
        await target.execute(f"SELECT setval('{seq}', $1)", max_id + 1)
        print(f"  {seq} → {max_id + 1}")

    print("\n── Verification ───────────────────────────────────────────────────")
    for table in TABLES:
        src = await source.fetchval(f"SELECT COUNT(*) FROM {table}")
        dst = await target.fetchval(f"SELECT COUNT(*) FROM {table}")
        ok = "✓" if src == dst else "✗"
        print(f"  {ok} {table}: source={src:,}  target={dst:,}")

    print("\nDone.")


async def _init_schema_on(conn: asyncpg.Connection) -> None:
    """Replay the project's CREATE TABLE statements on the target connection."""
    ddl = """
    CREATE TABLE IF NOT EXISTS users (
        id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
        email         TEXT        UNIQUE NOT NULL,
        password_hash TEXT        NOT NULL,
        role          TEXT        NOT NULL DEFAULT 'viewer',
        is_active     BOOLEAN     DEFAULT TRUE,
        created_at    TIMESTAMPTZ DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS products (
        sku                 TEXT        NOT NULL,
        supplier            TEXT        NOT NULL,
        name                TEXT,
        url                 TEXT,
        category            TEXT,
        updated_at          TIMESTAMPTZ DEFAULT NOW(),
        units_per_package   INT,
        packs_per_pallet    INT,
        product_id          TEXT,
        brand               TEXT,
        product_type        TEXT,
        size_value          NUMERIC,
        size_unit           TEXT,
        category_dept       TEXT,
        category_sub        TEXT,
        canonical_key       TEXT,
        features_version    INT         DEFAULT 0,
        last_scraped_at     TIMESTAMPTZ,
        variant             TEXT,
        size                TEXT,
        price_unit          NUMERIC(12,2),
        price_bulk          NUMERIC(12,2),
        stock               TEXT,
        canonical_name      TEXT,
        PRIMARY KEY (sku, supplier)
    );

    CREATE TABLE IF NOT EXISTS price_snapshots (
        id          BIGSERIAL   PRIMARY KEY,
        sku         TEXT        NOT NULL,
        supplier    TEXT        NOT NULL,
        scraped_at  DATE        NOT NULL DEFAULT CURRENT_DATE,
        price_unit  NUMERIC(12,2),
        price_bulk  NUMERIC(12,2),
        stock       TEXT,
        UNIQUE (sku, supplier, scraped_at),
        FOREIGN KEY (sku, supplier) REFERENCES products(sku, supplier)
    );

    CREATE TABLE IF NOT EXISTS price_history (
        id          BIGSERIAL   PRIMARY KEY,
        sku         TEXT        NOT NULL,
        supplier    TEXT        NOT NULL,
        price_unit  NUMERIC(12,2),
        first_seen  DATE        NOT NULL DEFAULT CURRENT_DATE,
        last_seen   DATE        NOT NULL DEFAULT CURRENT_DATE,
        FOREIGN KEY (sku, supplier) REFERENCES products(sku, supplier)
    );

    CREATE TABLE IF NOT EXISTS run_log (
        id                BIGSERIAL    PRIMARY KEY,
        supplier          TEXT         NOT NULL,
        started_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
        finished_at       TIMESTAMPTZ,
        status            TEXT         NOT NULL DEFAULT 'running',
        categories_total  INT          DEFAULT 0,
        categories_done   INT          DEFAULT 0,
        products_scraped  INT          DEFAULT 0,
        snapshots_written INT          DEFAULT 0,
        error_message     TEXT
    );
    """
    await conn.execute(ddl)
    print("  Schema ready on target.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)

    source_url, target_url = sys.argv[1], sys.argv[2]
    asyncio.run(migrate(source_url, target_url))
