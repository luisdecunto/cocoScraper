import asyncio
from dotenv import load_dotenv
load_dotenv()
from scraper.db import get_pool

SKUS = ["2965593", "6877117", "6877230", "6877176"]

async def check():
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT sku, name, category FROM products "
            "WHERE sku = ANY($1) AND supplier='nini'",
            SKUS
        )
    await pool.close()
    for r in rows:
        print(dict(r))

asyncio.run(check())
