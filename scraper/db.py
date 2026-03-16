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
    """Create all tables and indexes if they don't exist. Migrates price_snapshots on first run."""
    async with pool.acquire() as conn:
        # ── Core tables and all column migrations ──────────────────────────
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

            ALTER TABLE products ADD COLUMN IF NOT EXISTS units_per_package INT;
            ALTER TABLE products ADD COLUMN IF NOT EXISTS packs_per_pallet  INT;
            ALTER TABLE products ADD COLUMN IF NOT EXISTS product_id       TEXT;
            ALTER TABLE products ADD COLUMN IF NOT EXISTS brand            TEXT;
            ALTER TABLE products ADD COLUMN IF NOT EXISTS product_type     TEXT;
            ALTER TABLE products ADD COLUMN IF NOT EXISTS variant          TEXT;
            ALTER TABLE products ADD COLUMN IF NOT EXISTS size             TEXT;
            ALTER TABLE products ADD COLUMN IF NOT EXISTS size_value       NUMERIC(12,4);
            ALTER TABLE products ADD COLUMN IF NOT EXISTS size_unit        TEXT;
            ALTER TABLE products ADD COLUMN IF NOT EXISTS category_dept    TEXT;
            ALTER TABLE products ADD COLUMN IF NOT EXISTS category_sub     TEXT;
            ALTER TABLE products ADD COLUMN IF NOT EXISTS canonical_key    TEXT;
            ALTER TABLE products ADD COLUMN IF NOT EXISTS features_version INT DEFAULT 0;
            ALTER TABLE products ADD COLUMN IF NOT EXISTS last_scraped_at  TIMESTAMPTZ;

            -- Current price/stock — updated on every scrape (no history here)
            ALTER TABLE products ADD COLUMN IF NOT EXISTS price_unit  NUMERIC(12,2);
            ALTER TABLE products ADD COLUMN IF NOT EXISTS price_bulk  NUMERIC(12,2);
            ALTER TABLE products ADD COLUMN IF NOT EXISTS stock       TEXT;

            CREATE UNIQUE INDEX IF NOT EXISTS idx_products_product_id ON products(product_id)
                WHERE product_id IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_products_canonical_key ON products(canonical_key)
                WHERE canonical_key IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_products_category ON products(category_dept, category_sub);
            CREATE INDEX IF NOT EXISTS idx_products_last_scraped ON products(last_scraped_at DESC);

            -- price_snapshots kept for reference / backward compat — not written to after migration
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

            -- Price history: one row per stable price period per product
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

            CREATE TABLE IF NOT EXISTS users (
                id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
                email         TEXT        UNIQUE NOT NULL,
                password_hash TEXT        NOT NULL,
                role          TEXT        NOT NULL DEFAULT 'viewer',
                is_active     BOOLEAN     DEFAULT TRUE,
                created_at    TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE INDEX IF NOT EXISTS idx_price_history_sku_supplier
                ON price_history(sku, supplier, first_seen DESC);
            CREATE INDEX IF NOT EXISTS idx_snapshots_sku_supplier
                ON price_snapshots(sku, supplier);
            CREATE INDEX IF NOT EXISTS idx_snapshots_date
                ON price_snapshots(scraped_at DESC);
            CREATE INDEX IF NOT EXISTS idx_run_log_supplier
                ON run_log(supplier);
        """)

        # ── One-time migration: collapse price_snapshots → price_history ──
        history_count = await conn.fetchval("SELECT COUNT(*) FROM price_history")
        if history_count == 0:
            snapshots_count = await conn.fetchval("SELECT COUNT(*) FROM price_snapshots")
            if snapshots_count > 0:
                await conn.execute("""
                    INSERT INTO price_history (sku, supplier, price_unit, first_seen, last_seen)
                    SELECT sku, supplier, price_unit, MIN(scraped_at), MAX(scraped_at)
                    FROM (
                        SELECT sku, supplier, price_unit, scraped_at,
                               ROW_NUMBER() OVER (PARTITION BY sku, supplier ORDER BY scraped_at) -
                               ROW_NUMBER() OVER (PARTITION BY sku, supplier, price_unit ORDER BY scraped_at) AS grp
                        FROM price_snapshots
                        WHERE price_unit IS NOT NULL
                    ) g
                    GROUP BY sku, supplier, price_unit, grp
                    ORDER BY sku, supplier, MIN(scraped_at)
                """)
                migrated = await conn.fetchval("SELECT COUNT(*) FROM price_history")
                logger.info(f"Migration: collapsed {snapshots_count} snapshots → {migrated} price_history periods")

                # Also back-fill current price/stock on products from latest snapshot
                await conn.execute("""
                    UPDATE products p
                    SET price_unit = s.price_unit,
                        price_bulk = s.price_bulk,
                        stock      = s.stock
                    FROM price_snapshots s
                    WHERE s.sku = p.sku
                      AND s.supplier = p.supplier
                      AND s.scraped_at = (
                          SELECT MAX(scraped_at) FROM price_snapshots
                          WHERE sku = p.sku AND supplier = p.supplier
                      )
                """)
                logger.info("Migration: back-filled price_unit/price_bulk/stock on products table")

    logger.info("Schema initialized.")


async def upsert_product(pool: asyncpg.Pool, supplier: str, product_dict: dict) -> None:
    """Insert or update a product row. On conflict, update all mutable fields."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO products
                (sku, supplier, name, url, category, units_per_package, packs_per_pallet, updated_at, last_scraped_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, NOW(), NOW())
            ON CONFLICT (sku, supplier) DO UPDATE
                SET name              = EXCLUDED.name,
                    url               = EXCLUDED.url,
                    category          = EXCLUDED.category,
                    units_per_package = COALESCE(EXCLUDED.units_per_package, products.units_per_package),
                    packs_per_pallet  = COALESCE(EXCLUDED.packs_per_pallet,  products.packs_per_pallet),
                    features_version  = CASE
                        WHEN products.name IS DISTINCT FROM EXCLUDED.name THEN NULL
                        ELSE products.features_version
                    END,
                    updated_at        = NOW(),
                    last_scraped_at   = NOW()
            """,
            product_dict["sku"],
            supplier,
            product_dict.get("name"),
            product_dict.get("url"),
            product_dict.get("category"),
            product_dict.get("units_per_package"),
            product_dict.get("packs_per_pallet"),
        )


async def upsert_price_history(
    pool: asyncpg.Pool,
    sku: str,
    supplier: str,
    price_unit: float | None,
    price_bulk: float | None,
    stock: str,
) -> bool:
    """
    Update current price/stock on products, then manage price_history periods.
    - If price_unit unchanged from the last period: extend last_seen to today.
    - If price_unit changed (or no history yet): open a new period row.
    Returns True if a new price period was opened (price changed), False otherwise.
    """
    async with pool.acquire() as conn:
        # Always update current price/stock on the products row
        await conn.execute(
            "UPDATE products SET price_unit=$3, price_bulk=$4, stock=$5 "
            "WHERE sku=$1 AND supplier=$2",
            sku, supplier, price_unit, price_bulk, stock,
        )

        # Only track non-NULL prices in history
        if price_unit is None:
            return False

        # Check whether the most recent period has the same price_unit
        last = await conn.fetchrow(
            "SELECT id, (price_unit IS NOT DISTINCT FROM $3::NUMERIC) AS same_price "
            "FROM price_history WHERE sku=$1 AND supplier=$2 "
            "ORDER BY first_seen DESC LIMIT 1",
            sku, supplier, price_unit,
        )
        if last and last["same_price"]:
            await conn.execute(
                "UPDATE price_history SET last_seen=CURRENT_DATE WHERE id=$1",
                last["id"],
            )
            return False
        else:
            await conn.execute(
                "INSERT INTO price_history (sku, supplier, price_unit, first_seen, last_seen) "
                "VALUES ($1, $2, $3, CURRENT_DATE, CURRENT_DATE)",
                sku, supplier, price_unit,
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


async def upsert_product_features(
    pool: asyncpg.Pool,
    sku: str,
    supplier: str,
    product_id: str,
    brand: str | None,
    product_type: str | None,
    variant: str | None,
    size: str | None,
    size_value: float | None,
    size_unit: str | None,
    category_dept: str | None,
    category_sub: str | None,
    canonical_key: str | None,
    features_version: int,
) -> None:
    """
    Write normalized features to an existing products row.
    Called by the postprocess pipeline after feature extraction.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE products
            SET product_id       = $3,
                brand            = $4,
                product_type     = $5,
                variant          = $6,
                size             = $7,
                size_value       = $8,
                size_unit        = $9,
                category_dept    = $10,
                category_sub     = $11,
                canonical_key    = $12,
                features_version = $13
            WHERE sku = $1 AND supplier = $2
            """,
            sku, supplier, product_id, brand, product_type, variant, size,
            size_value, size_unit, category_dept, category_sub, canonical_key,
            features_version,
        )


async def fetch_products_for_postprocess(
    pool: asyncpg.Pool,
    supplier: str,
    min_version: int = 0,
) -> list:
    """
    Return products needing postprocessing.
    Filters by features_version < min_version or IS NULL.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT sku, name, category
            FROM products
            WHERE supplier = $1
              AND (features_version IS NULL OR features_version < $2)
            ORDER BY sku
            """,
            supplier, min_version,
        )
        return rows
