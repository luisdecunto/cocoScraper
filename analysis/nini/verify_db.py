"""Verify Nini full scrape results in PostgreSQL."""
import asyncio
from dotenv import load_dotenv
load_dotenv()
from scraper.db import get_pool


async def check():
    pool = await get_pool()
    async with pool.acquire() as conn:
        run = await conn.fetchrow(
            "SELECT * FROM run_log WHERE supplier='nini' ORDER BY id DESC LIMIT 1"
        )
        pc = await conn.fetchval(
            "SELECT COUNT(*) FROM products WHERE supplier='nini'"
        )
        sc = await conn.fetchval(
            "SELECT COUNT(*) FROM price_snapshots WHERE supplier='nini'"
        )
        # Check the previously broken product
        fixed = await conn.fetchrow(
            "SELECT sku, name, category FROM products WHERE sku='2965593' AND supplier='nini'"
        )
        sample = await conn.fetch(
            "SELECT sku, name, category, units_per_package, packs_per_pallet "
            "FROM products WHERE supplier='nini' LIMIT 5"
        )

    print(f"Products in DB  : {pc}")
    print(f"Snapshots in DB : {sc}")
    print(f"Last run        : status={run['status']}  "
          f"categories={run['categories_done']}/{run['categories_total']}  "
          f"products={run['products_scraped']}  snapshots={run['snapshots_written']}")
    print()
    print(f"SKU 2965593 fixed: {dict(fixed)}")
    print()
    print("--- Sample products ---")
    for r in sample:
        print(dict(r))

    await pool.close()


asyncio.run(check())
