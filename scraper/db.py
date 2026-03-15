"""
Database layer.
PostgreSQL connection pool, schema init, upsert logic, and run log helpers.
"""

import asyncpg
import os
import logging

logger = logging.getLogger(__name__)


async def get_pool() -> asyncpg.Pool:
    """Create and return an asyncpg connection pool."""
    return await asyncpg.create_pool(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 5432)),
        database=os.getenv("DB_NAME", "prices"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASS"),
        min_size=2,
        max_size=10,
    )


async def init_schema(pool: asyncpg.Pool) -> None:
    """Create all tables and indexes if they don't exist."""
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS products (
                sku                 TEXT        NOT NULL,
                supplier            TEXT        NOT NULL,
                name                TEXT,
                url                 TEXT,
                category            TEXT,
                units_per_package   INT,
                packs_per_pallet    INT,
                updated_at          TIMESTAMPTZ DEFAULT NOW(),
                PRIMARY KEY (sku, supplier)
            );

            -- Migrate existing databases: add columns if they don't exist yet
            ALTER TABLE products ADD COLUMN IF NOT EXISTS units_per_package INT;
            ALTER TABLE products ADD COLUMN IF NOT EXISTS packs_per_pallet  INT;

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

            CREATE TABLE IF NOT EXISTS users (
                id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
                email         TEXT        UNIQUE NOT NULL,
                password_hash TEXT        NOT NULL,
                role          TEXT        NOT NULL DEFAULT 'viewer',
                is_active     BOOLEAN     DEFAULT TRUE,
                created_at    TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE INDEX IF NOT EXISTS idx_snapshots_sku_supplier
                ON price_snapshots(sku, supplier);
            CREATE INDEX IF NOT EXISTS idx_snapshots_date
                ON price_snapshots(scraped_at DESC);
            CREATE INDEX IF NOT EXISTS idx_run_log_supplier
                ON run_log(supplier);
        """)
    logger.info("Schema initialized.")


async def upsert_product(pool: asyncpg.Pool, supplier: str, product_dict: dict) -> None:
    """Insert or update a product row. On conflict, update all mutable fields."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO products
                (sku, supplier, name, url, category, units_per_package, packs_per_pallet, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())
            ON CONFLICT (sku, supplier) DO UPDATE
                SET name              = EXCLUDED.name,
                    url               = EXCLUDED.url,
                    category          = EXCLUDED.category,
                    units_per_package = COALESCE(EXCLUDED.units_per_package, products.units_per_package),
                    packs_per_pallet  = COALESCE(EXCLUDED.packs_per_pallet,  products.packs_per_pallet),
                    updated_at        = NOW()
            """,
            product_dict["sku"],
            supplier,
            product_dict.get("name"),
            product_dict.get("url"),
            product_dict.get("category"),
            product_dict.get("units_per_package"),
            product_dict.get("packs_per_pallet"),
        )


async def upsert_snapshot(
    pool: asyncpg.Pool,
    sku: str,
    supplier: str,
    price_unit: float | None,
    price_bulk: float | None,
    stock: str,
) -> bool:
    """
    Insert a price snapshot only if prices changed vs. the last recorded row.
    Returns True if a row was written, False if skipped (no change).
    """
    async with pool.acquire() as conn:
        last = await conn.fetchrow(
            "SELECT price_unit, price_bulk FROM price_snapshots "
            "WHERE sku=$1 AND supplier=$2 ORDER BY scraped_at DESC LIMIT 1",
            sku, supplier,
        )
        if last and last["price_unit"] == price_unit and last["price_bulk"] == price_bulk:
            return False

        await conn.execute(
            """
            INSERT INTO price_snapshots (sku, supplier, scraped_at, price_unit, price_bulk, stock)
            VALUES ($1, $2, CURRENT_DATE, $3, $4, $5)
            ON CONFLICT (sku, supplier, scraped_at) DO UPDATE
                SET price_unit = EXCLUDED.price_unit,
                    price_bulk = EXCLUDED.price_bulk,
                    stock      = EXCLUDED.stock
            """,
            sku, supplier, price_unit, price_bulk, stock,
        )
        return True


async def start_run(pool: asyncpg.Pool, supplier: str) -> int:
    """Insert a run_log row with status='running'. Return the new id."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO run_log (supplier, status) VALUES ($1, 'running') RETURNING id",
            supplier,
        )
        return row["id"]


async def finish_run(
    pool: asyncpg.Pool,
    run_id: int,
    status: str,
    categories_done: int,
    products_scraped: int,
    snapshots_written: int,
    error_message: str | None = None,
) -> None:
    """Update run_log row with final stats and finished_at=NOW()."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE run_log
            SET finished_at       = NOW(),
                status            = $2,
                categories_done   = $3,
                products_scraped  = $4,
                snapshots_written = $5,
                error_message     = $6
            WHERE id = $1
            """,
            run_id, status, categories_done, products_scraped, snapshots_written, error_message,
        )


async def update_run_categories_total(pool: asyncpg.Pool, run_id: int, total: int) -> None:
    """Update the categories_total field on a run_log row."""
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE run_log SET categories_total=$2 WHERE id=$1", run_id, total
        )
