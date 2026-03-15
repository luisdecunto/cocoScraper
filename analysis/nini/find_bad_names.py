"""Find products with suspiciously short or bad names."""
import asyncio
from dotenv import load_dotenv
load_dotenv()
from scraper.db import get_pool


async def check():
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT sku, name, category FROM products "
            "WHERE supplier='nini' AND length(trim(name)) < 8 "
            "ORDER BY name"
        )
    await pool.close()
    print(f"Products with name shorter than 8 chars: {len(rows)}")
    for r in rows:
        print(dict(r))


asyncio.run(check())
