# Prompt 02 — Scraper Core

> Paste this into Claude Code after prompt 01 is complete.
> Prerequisite: folder structure exists, base.py and config.py are written.
> Goal: implement db.py, scraper.py, export.py, and complete main.py.
> Implement one file at a time. Confirm each works before moving to the next.

---

## 1. `scraper/db.py`

Replace the stub. Implement the full database layer.

### Connection pool

```python
async def get_pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 5432)),
        database=os.getenv("DB_NAME", "prices"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASS"),
        min_size=2,
        max_size=10,
    )
```

### Schema init — `init_schema(pool)`

Creates all tables if they don't exist. Tables: `products`, `price_snapshots`,
`run_log`, `users`. Full DDL is in root `CLAUDE.md`.
Add indexes:
```sql
CREATE INDEX IF NOT EXISTS idx_snapshots_sku_supplier ON price_snapshots(sku, supplier);
CREATE INDEX IF NOT EXISTS idx_snapshots_date ON price_snapshots(scraped_at DESC);
CREATE INDEX IF NOT EXISTS idx_run_log_supplier ON run_log(supplier);
```

### Product upsert — `upsert_product(pool, supplier, product_dict) -> None`

Insert or update `products`. On conflict (sku, supplier), update name/url/category/updated_at.

### Snapshot upsert — `upsert_snapshot(...) -> bool`

```python
async def upsert_snapshot(
    pool: asyncpg.Pool,
    sku: str,
    supplier: str,
    price_unit: float | None,
    price_bulk: float | None,
    stock: str,
) -> bool:
    """
    Insert a price snapshot only if prices changed vs. last recorded row.
    Returns True if a row was written, False if skipped (no change).
    """
```

Price-change check:
```python
last = await conn.fetchrow(
    "SELECT price_unit, price_bulk FROM price_snapshots "
    "WHERE sku=$1 AND supplier=$2 ORDER BY scraped_at DESC LIMIT 1",
    sku, supplier
)
if last and last["price_unit"] == price_unit and last["price_bulk"] == price_bulk:
    return False
```

On insert, use `ON CONFLICT (sku, supplier, scraped_at) DO UPDATE` to handle
re-runs on the same day.

### Run log helpers

```python
async def start_run(pool, supplier: str) -> int:
    """Insert a run_log row with status='running'. Return the new id."""

async def finish_run(
    pool,
    run_id: int,
    status: str,              # 'success' or 'failed'
    categories_done: int,
    products_scraped: int,
    snapshots_written: int,
    error_message: str | None = None,
) -> None:
    """Update run_log row with final stats and finished_at=NOW()."""
```

### Verification

```bash
# Start a local PostgreSQL instance, create a DB named 'prices', set .env, then:
python -c "
import asyncio
from scraper.db import get_pool, init_schema
async def test():
    pool = await get_pool()
    await init_schema(pool)
    print('Schema created OK')
    await pool.close()
asyncio.run(test())
"
```

Expected output: `Schema created OK`. Fix any errors before continuing.

---

## 2. `scraper/scraper.py`

Orchestrator. Imports from `config.py`, `db.py`, and supplier classes.

### Key functions

```python
async def run_supplier(supplier_id: str, pool: asyncpg.Pool) -> None:
    """Run a full scrape for one supplier."""

async def run_all(pool: asyncpg.Pool) -> None:
    """Run scrape for all suppliers defined in config."""
```

### `run_supplier` implementation

```python
async def run_supplier(supplier_id: str, pool: asyncpg.Pool) -> None:
    config = get_supplier_config(supplier_id)
    supplier = load_supplier_class(config)
    sem = asyncio.Semaphore(config.get("concurrency", 10))
    run_id = await start_run(pool, supplier_id)
    products_scraped = 0
    snapshots_written = 0
    categories_done = 0

    try:
        async with httpx.AsyncClient(
            headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"},
            timeout=httpx.Timeout(30.0),
            follow_redirects=True,
        ) as client:

            if config["requires_login"]:
                await supplier.login(client)
                logger.info(f"{supplier_id}: login successful")

            urls = config["category_urls"] or await supplier.discover_categories(client)
            logger.info(f"{supplier_id}: {len(urls)} categories found")
            await update_run_categories_total(pool, run_id, len(urls))

            for url in urls:
                try:
                    products = await supplier.scrape_category(client, url, sem)
                    for p in products:
                        await upsert_product(pool, supplier_id, p)
                        wrote = await upsert_snapshot(
                            pool, p["sku"], supplier_id,
                            p["price_unit"], p["price_bulk"], p["stock"]
                        )
                        products_scraped += 1
                        if wrote:
                            snapshots_written += 1
                    categories_done += 1
                    logger.info(f"{supplier_id}: {url} — {len(products)} products")
                except Exception as e:
                    logger.warning(f"{supplier_id}: failed on {url} — {e}")

                await asyncio.sleep(2)

    except Exception as e:
        logger.error(f"{supplier_id}: run aborted — {e}")
        await finish_run(pool, run_id, "failed", categories_done,
                         products_scraped, snapshots_written, str(e))
        return

    await finish_run(pool, run_id, "success", categories_done,
                     products_scraped, snapshots_written)

    if snapshots_written == 0:
        logger.error(
            f"{supplier_id}: ALERT — run completed but 0 snapshots written. "
            "Login may have failed silently, or site structure changed."
        )
    else:
        logger.info(f"{supplier_id}: done — {products_scraped} products, "
                    f"{snapshots_written} snapshots written")
```

Add `update_run_categories_total` to `db.py`:
```python
async def update_run_categories_total(pool, run_id: int, total: int) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE run_log SET categories_total=$2 WHERE id=$1", run_id, total
        )
```

### `run_all` implementation

```python
async def run_all(pool: asyncpg.Pool) -> None:
    for s in SUPPLIERS:
        await run_supplier(s["id"], pool)
```

---

## 3. `scraper/export.py`

Three exports. Use `asyncpg` to query, `csv` stdlib for CSV, `openpyxl` for XLSX.

### `export_latest(pool, output_path: str) -> None`

CSV with one row per (product, supplier), latest price only.

Query:
```sql
SELECT p.name, p.sku, p.supplier, p.category,
       s.price_unit, s.price_bulk, s.stock, s.scraped_at
FROM products p
JOIN price_snapshots s ON s.sku = p.sku AND s.supplier = p.supplier
WHERE s.scraped_at = (
    SELECT MAX(scraped_at) FROM price_snapshots
    WHERE sku = p.sku AND supplier = p.supplier
)
ORDER BY p.supplier, p.name;
```

### `export_history(pool, sku: str, supplier: str, output_path: str) -> None`

CSV with all snapshots for one (sku, supplier), ordered by date ascending.

### `export_comparison(pool, output_csv: str, output_xlsx: str) -> None`

Cross-supplier comparison. One row per product, one column per supplier.

**Matching logic:**
1. Get all products with their latest price per supplier
2. Group by normalized name: `name.lower().strip()`
3. Within each name group, pivot suppliers into columns
4. Add columns: `cheapest_supplier`, `price_diff_pct`
   - `price_diff_pct` = `(max_price - min_price) / min_price * 100`
5. Write uncertain matches (products found in only one supplier) to `uncertain_matches.csv`

**XLSX formatting with openpyxl:**
- Green fill (`PatternFill(fgColor="00B050")`) on the lowest price cell per row
- Bold header row
- Auto-width columns (approximate: max character length per column * 1.2)

---

## 4. `scraper/main.py`

Replace the stub. Full implementation of all CLI subcommands.

```python
async def async_main(args):
    pool = await get_pool()

    if args.command == "db" and args.db_command == "init":
        await init_schema(pool)
        print("Database schema initialized.")

    elif args.command == "discover":
        config = get_supplier_config(args.supplier)
        supplier = load_supplier_class(config)
        async with httpx.AsyncClient(follow_redirects=True) as client:
            if config["requires_login"]:
                await supplier.login(client)
            urls = await supplier.discover_categories(client)
        print(f"Found {len(urls)} categories:")
        for url in urls:
            print(f"  {url}")

    elif args.command == "scrape":
        if args.supplier:
            await run_supplier(args.supplier, pool)
        else:
            await run_all(pool)

    elif args.command == "export":
        if args.export_command == "latest":
            path = getattr(args, "output", "exports/latest_prices.csv")
            await export_latest(pool, path)
            print(f"Exported: {path}")

        elif args.export_command == "comparison":
            csv_path = getattr(args, "output_csv", "exports/comparison.csv")
            xlsx_path = getattr(args, "output_xlsx", "exports/comparison.xlsx")
            await export_comparison(pool, csv_path, xlsx_path)
            print(f"Exported: {csv_path}, {xlsx_path}")

        elif args.export_command == "history":
            path = getattr(args, "output", f"exports/history_{args.supplier}_{args.sku}.csv")
            await export_history(pool, args.sku, args.supplier, path)
            print(f"Exported: {path}")

    await pool.close()
```

Also add `--output`, `--output-csv`, `--output-xlsx` optional arguments to the relevant
export subcommands.

Add logging to file:
```python
os.makedirs("logs", exist_ok=True)
file_handler = logging.FileHandler("logs/scraper.log")
file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s — %(message)s"))
logging.getLogger().addHandler(file_handler)
```

---

## 5. Scheduling stub

Add `schedule` subcommand to `main.py`:

```python
elif args.command == "schedule":
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    scheduler = AsyncIOScheduler()
    scheduler.add_job(run_all, "cron", hour=6, args=[pool])
    scheduler.start()
    logger.info("Scheduler running. Daily scrape at 06:00. Ctrl+C to stop.")
    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
```

For production, use cron instead:
```bash
# /etc/cron.d/cocoscraper
0 6 * * * user cd /path/to/cocoScraper && .venv/bin/python scraper/main.py scrape >> logs/cron.log 2>&1
```

---

## Final verification sequence

Run these in order. Fix any errors before the next step.

```bash
# 1. Init DB
python scraper/main.py db init

# 2. Discover categories (no supplier implementation yet — will fail gracefully)
# Skip until prompt 03 is done

# 3. Check run_log table exists
# psql -d prices -c "SELECT * FROM run_log LIMIT 1;"

# 4. Check all imports work
python -c "from scraper.db import get_pool, init_schema; print('db OK')"
python -c "from scraper.scraper import run_supplier, run_all; print('scraper OK')"
python -c "from scraper.export import export_latest, export_comparison; print('export OK')"
python -c "from scraper.config import SUPPLIERS, get_supplier_config; print('config OK')"
```

---

## End of session

Update root `CLAUDE.md` Status:
- Mark "Scraper core engine" as done
- Mark "Supplier: Maxiconsumo" as in progress
