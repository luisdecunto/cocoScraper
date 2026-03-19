"""
Database layer.
PostgreSQL connection pool, schema init, upsert logic, and run log helpers.
"""

import asyncpg
import datetime
import os
import logging

logger = logging.getLogger(__name__)


async def get_pool() -> asyncpg.Pool:
    """Create and return an asyncpg connection pool.

    Supports two connection modes:
    - DATABASE_URL: full connection string (Neon, Supabase, etc.)
      asyncpg does not parse ?sslmode=require, so ssl="require" is added
      automatically when the DSN contains that param.
    - DB_HOST / DB_PORT / DB_NAME / DB_USER / DB_PASS: individual vars (local dev)
    """
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        # Strip unsupported query params and pass ssl separately if needed
        ssl = "require" if "sslmode=require" in database_url else None
        dsn = database_url.split("?")[0]
        return await asyncpg.create_pool(dsn=dsn, ssl=ssl, min_size=2, max_size=10)

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
            ALTER TABLE products ADD COLUMN IF NOT EXISTS canonical_name   TEXT;
            ALTER TABLE products ADD COLUMN IF NOT EXISTS features_version INT DEFAULT 0;
            ALTER TABLE products ADD COLUMN IF NOT EXISTS last_scraped_at  TIMESTAMPTZ;
            ALTER TABLE products ADD COLUMN IF NOT EXISTS classification_status TEXT;
            ALTER TABLE products ADD COLUMN IF NOT EXISTS classification_confidence TEXT;

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

            -- Price history: one row per stable price period per product.
            -- stock_flags is a per-calendar-day string from first_seen to last_seen:
            --   position i  →  first_seen + i days
            --   '1' = in stock that day, '0' = no stock
            -- Unscraped days inherit the last known status (gaps filled on next scrape).
            CREATE TABLE IF NOT EXISTS price_history (
                id              BIGSERIAL   PRIMARY KEY,
                sku             TEXT        NOT NULL,
                supplier        TEXT        NOT NULL,
                price_unit      NUMERIC(12,2),
                first_seen      DATE        NOT NULL DEFAULT CURRENT_DATE,
                last_seen       DATE        NOT NULL DEFAULT CURRENT_DATE,
                stock_flags     TEXT        NOT NULL DEFAULT '',
                FOREIGN KEY (sku, supplier) REFERENCES products(sku, supplier)
            );

            ALTER TABLE price_history ADD COLUMN IF NOT EXISTS stock_flags TEXT NOT NULL DEFAULT '';

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

    # ── One-time migration: auto-approve pre-existing valid classifications ──
    # Products classified before classification_status column existed have
    # valid brand/product_type/features_version but NULL or 'pending' status.
    # Auto-approve them so they don't appear in the manual review queue.
    async with pool.acquire() as conn:
        r1 = await conn.execute("""
            UPDATE products
            SET classification_status = 'approved'
            WHERE classification_status IS NULL
              AND brand IS NOT NULL
              AND product_type IS NOT NULL
              AND features_version > 0
        """)
        r2 = await conn.execute("""
            UPDATE products
            SET classification_status = 'approved'
            WHERE classification_status = 'pending'
              AND classification_confidence = 'high'
              AND brand IS NOT NULL
              AND product_type IS NOT NULL
              AND features_version > 0
        """)
        n1 = int(r1.split()[-1]) if r1 else 0
        n2 = int(r2.split()[-1]) if r2 else 0
        if n1 + n2 > 0:
            logger.info(f"Migration: auto-approved {n1 + n2} pre-existing classified products "
                        f"({n1} from NULL, {n2} from pending/high)")

    logger.info("Schema initialized.")


async def upsert_product(pool: asyncpg.Pool, supplier: str, product_dict: dict) -> None:
    """Insert a new product or update price/stock of existing product.

    On insert: all fields set (name, url, category, prices, stock)
    On conflict: only update price_unit, price_bulk, stock, last_scraped_at.
                 Name, category, and all classification fields remain frozen.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO products
                (sku, supplier, name, url, category, units_per_package, packs_per_pallet, updated_at, last_scraped_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, NOW(), NOW())
            ON CONFLICT (sku, supplier) DO UPDATE
                SET price_unit        = EXCLUDED.price_unit,
                    price_bulk        = EXCLUDED.price_bulk,
                    stock             = EXCLUDED.stock,
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


def _build_gap_fill(last_seen: datetime.date, target_date: datetime.date, last_flag: str) -> str:
    """Return a string of last_flag repeated for each day from last_seen+1 to target_date-1."""
    gap = (target_date - last_seen).days - 1
    return last_flag * max(0, gap)


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

    stock_flags is a per-calendar-day string: position i = first_seen + i days.
    '1' = in stock, '0' = no stock. Unscraped days are filled with the last known
    status so gaps propagate correctly (e.g. no-stock persists until confirmed again).

    - Same price: fill gap days with last flag, then append today's actual flag.
    - Price changed: fill previous period's gap up to yesterday, close it,
      then open a new period starting today.

    Returns True if a new price period was opened (price changed), False otherwise.
    """
    no_stock = (stock == "sin stock")
    flag = "0" if no_stock else "1"
    today = datetime.date.today()

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

        # Fetch most recent period: same price? last_seen? last stock flag?
        last = await conn.fetchrow(
            "SELECT id, last_seen, "
            "       COALESCE(NULLIF(RIGHT(stock_flags, 1), ''), '1') AS last_flag, "
            "       (price_unit IS NOT DISTINCT FROM $3::NUMERIC) AS same_price "
            "FROM price_history WHERE sku=$1 AND supplier=$2 "
            "ORDER BY first_seen DESC LIMIT 1",
            sku, supplier, price_unit,
        )

        if last and last["same_price"]:
            # Fill unscraped days since last_seen with the previous flag, then append today's
            gap = _build_gap_fill(last["last_seen"], today, last["last_flag"])
            await conn.execute(
                "UPDATE price_history "
                "SET last_seen   = CURRENT_DATE, "
                "    stock_flags = stock_flags || $2 "
                "WHERE id = $1",
                last["id"], gap + flag,
            )
            return False
        else:
            if last:
                # Close previous period: fill gap up to yesterday, set last_seen = yesterday
                gap = _build_gap_fill(last["last_seen"], today, last["last_flag"])
                await conn.execute(
                    "UPDATE price_history "
                    "SET last_seen   = CURRENT_DATE - 1, "
                    "    stock_flags = stock_flags || $2 "
                    "WHERE id = $1",
                    last["id"], gap,
                )
            # Open new period starting today
            await conn.execute(
                "INSERT INTO price_history "
                "    (sku, supplier, price_unit, first_seen, last_seen, stock_flags) "
                "VALUES ($1, $2, $3, CURRENT_DATE, CURRENT_DATE, $4)",
                sku, supplier, price_unit, flag,
            )
            return True


async def reconcile_missing_as_no_stock(
    pool: asyncpg.Pool,
    supplier: str,
    scraped_skus: set[str],
) -> int:
    """Mark products not seen in this scrape as no-stock for today.

    For suppliers that silently remove out-of-stock products (e.g. Nini), absence
    from the scrape result is the only signal that a product is unavailable.

    Fills the gap from last_seen+1 to today with the last known flag, then appends '0'
    for today — consistent with the per-calendar-day stock_flags convention.

    Only touches the most-recent price_history period per missing SKU, and only if
    last_seen < CURRENT_DATE to prevent double-marking on the same day.

    Returns the number of products marked no-stock.
    """
    if not scraped_skus:
        return 0

    skus_list = list(scraped_skus)

    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE price_history ph
            SET last_seen   = CURRENT_DATE,
                stock_flags = stock_flags
                              || repeat(
                                  COALESCE(NULLIF(RIGHT(stock_flags, 1), ''), '1'),
                                  GREATEST(0, CURRENT_DATE - ph.last_seen - 1)
                              )
                              || '0'
            WHERE ph.supplier = $1
              AND NOT (ph.sku = ANY($2::TEXT[]))
              AND ph.last_seen < CURRENT_DATE
              AND ph.id = (
                  SELECT id FROM price_history
                  WHERE sku = ph.sku AND supplier = ph.supplier
                  ORDER BY first_seen DESC
                  LIMIT 1
              )
            """,
            supplier, skus_list,
        )
        # Flip products.stock so the dashboard reflects current status
        await conn.execute(
            "UPDATE products SET stock = 'sin stock' "
            "WHERE supplier = $1 AND NOT (sku = ANY($2::TEXT[]))",
            supplier, skus_list,
        )

    return int(result.split()[-1]) if result else 0


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
    canonical_name: str | None,
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
                canonical_name   = $13,
                features_version = $14
            WHERE sku = $1 AND supplier = $2
            """,
            sku, supplier, product_id, brand, product_type, variant, size,
            size_value, size_unit, category_dept, category_sub, canonical_key,
            canonical_name, features_version,
        )


async def batch_upsert_product_features(
    pool: asyncpg.Pool,
    records: list[tuple],
) -> None:
    """
    Bulk-write normalized features using a single executemany call.
    Only updates products with classification_status IS NULL (unclassified).

    Each record is a tuple of 16 values:
    (sku, supplier, product_id, brand, product_type, variant, size,
     size_value, size_unit, category_dept, category_sub,
     canonical_key, canonical_name, features_version,
     classification_status, classification_confidence)
    """
    async with pool.acquire() as conn:
        await conn.executemany(
            """
            UPDATE products
            SET product_id               = $3,
                brand                    = $4,
                product_type             = $5,
                variant                  = $6,
                size                     = $7,
                size_value               = $8,
                size_unit                = $9,
                category_dept            = $10,
                category_sub             = $11,
                canonical_key            = $12,
                canonical_name           = $13,
                features_version         = $14,
                classification_status    = $15,
                classification_confidence = $16
            WHERE sku = $1 AND supplier = $2
              AND classification_status IS NULL
            """,
            records,
        )


async def fetch_products_for_postprocess(
    pool: asyncpg.Pool,
    supplier: str,
    min_version: int = 0,
) -> list:
    """
    Return unclassified products needing postprocessing.
    Filters by classification_status IS NULL (never classified before).
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT sku, name, category
            FROM products
            WHERE supplier = $1
              AND classification_status IS NULL
            ORDER BY sku
            """,
            supplier,
        )
        return rows


async def approve_classification(
    pool: asyncpg.Pool,
    sku: str,
    supplier: str,
) -> None:
    """Mark a classification as approved (frozen forever)."""
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE products SET classification_status = 'approved' "
            "WHERE sku = $1 AND supplier = $2",
            sku, supplier,
        )


async def reject_classification(
    pool: asyncpg.Pool,
    sku: str,
    supplier: str,
) -> None:
    """Reject a classification and reset for re-classification.

    Resets canonical_name and classification_status to NULL so the product
    can be classified again by the pipeline.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE products
               SET classification_status = NULL,
                   canonical_name = NULL,
                   canonical_key = NULL,
                   brand = NULL,
                   product_type = NULL,
                   variant = NULL,
                   size = NULL,
                   size_value = NULL,
                   size_unit = NULL,
                   category_dept = NULL,
                   category_sub = NULL,
                   classification_confidence = NULL
               WHERE sku = $1 AND supplier = $2
            """,
            sku, supplier,
        )
