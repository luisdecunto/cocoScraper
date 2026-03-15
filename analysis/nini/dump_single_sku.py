"""Dump raw API + DB data for a single Nini SKU. Pass SKU as argument."""
import asyncio, httpx, os, re, json, time, sys
from dotenv import load_dotenv
load_dotenv()

BASE = "http://ecommerce.nini.com.ar:8081"
TARGET_SKU = sys.argv[1] if len(sys.argv) > 1 else "6877230"

# Search these departments first (ordered by likelihood)
DEPT_PRIORITY = ["210", "240", "220", "230", "250", "260", "270", "290"]


async def login(client):
    username = os.getenv("NINI_USER")
    password = os.getenv("NINI_PASS")
    ts = int(time.time() * 1000)
    r = await client.get(f"{BASE}/ventas.administracion/Account/ValidateUser",
        params={"userName": username, "password": password, "callback": "_jqjsp", f"_{ts}": ""})
    zone = str(json.loads(re.search(r'\((.+)\)', r.text).group(1))["Zone"])
    r2 = await client.post(f"{BASE}/nodejs/onlineUserDao/getUnique",
        data={"daoName": "onlineUserDao", "method": "getUnique", "params[]": username})
    seller_id = r2.json()[0]["sellerId"]
    r3 = await client.post(f"{BASE}/nodejs/onlineOrderDao/findByClientId",
        data={"daoName": "onlineOrderDao", "method": "findByClientId",
              "params[clientId]": username, "params[sellerId]": username,
              "params[isClient]": "true", "params[userName]": username,
              "params[zone]": zone, "params[quotaSellerId]": username})
    order_id = str(next(o for o in r3.json() if o.get("orderEndDate") is None)["id"])
    return username, zone, seller_id, order_id


async def main():
    # DB record first
    from scraper.db import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        prod = await conn.fetchrow(
            "SELECT * FROM products WHERE sku=$1 AND supplier='nini'", TARGET_SKU)
        snaps = await conn.fetch(
            "SELECT * FROM price_snapshots WHERE sku=$1 AND supplier='nini' "
            "ORDER BY scraped_at DESC", TARGET_SKU)
    await pool.close()

    print("=== DB: products ===")
    print(dict(prod) if prod else "NOT FOUND")
    print("\n=== DB: price_snapshots ===")
    for s in snaps:
        print(dict(s))

    # Raw API scan
    async with httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(30.0)) as client:
        username, zone, seller_id, order_id = await login(client)
        base_params = {
            "params[filter][onlypaquete]": "true",
            "params[filter][withStock]": "true",
            "params[filter][buyArticles][]": "-1",
            "params[filter][limit]": "50",
            "params[filter][currentOrder][id]": order_id,
            "params[sellerId]": seller_id,
            "params[isClient]": "true",
            "params[userName]": username,
            "params[zone]": zone,
            "params[quotaSellerId]": username,
        }

        found = None
        found_dept = found_sector = None
        for dept in DEPT_PRIORITY:
            r_sec = await client.post(f"{BASE}/nodejs/onlineSectorDao/findFacets",
                data={"daoName": "onlineSectorDao", "method": "findFacets",
                      "params[filter][departamentId]": dept,
                      "params[filter][sectorId]": "null", **base_params})
            for sec in r_sec.json():
                offset = 0
                while True:
                    rp = await client.post(f"{BASE}/nodejs/onlineProductDao/findAllWithOrder",
                        data={"daoName": "onlineProductDao", "method": "findAllWithOrder",
                              "params[filter][departamentId]": dept,
                              "params[filter][sectorId]": sec["id"],
                              "params[filter][offsetProducts]": str(offset),
                              **base_params})
                    products = rp.json()
                    for p in products:
                        if str(p.get("id")) == TARGET_SKU:
                            found = p
                            found_dept, found_sector = dept, sec["description"]
                            break
                    if found or len(products) < 50:
                        break
                    offset += 50
                if found:
                    break
            if found:
                break

    print(f"\n=== Raw API (dept={found_dept}, sector={found_sector}) ===")
    if found:
        print(json.dumps(found, indent=2, ensure_ascii=False))
    else:
        print("NOT FOUND in live API (out of stock or not in onlypaquete filter)")


asyncio.run(main())
