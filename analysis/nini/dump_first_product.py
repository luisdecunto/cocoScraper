"""Dump all fields from the first product in sector 210010 (Aceites Y Grasas)."""
import asyncio, httpx, os, re, json, time
from dotenv import load_dotenv

load_dotenv()
BASE = "http://ecommerce.nini.com.ar:8081"

async def main():
    async with httpx.AsyncClient(follow_redirects=True) as client:
        username = os.getenv("NINI_USER")
        password = os.getenv("NINI_PASS")

        ts = int(time.time() * 1000)
        r = await client.get(f"{BASE}/ventas.administracion/Account/ValidateUser",
            params={"userName": username, "password": password, "callback": "_jqjsp", f"_{ts}": ""})
        zone = str(json.loads(re.search(r'\((.+)\)', r.text).group(1))["Zone"])

        r2 = await client.post(f"{BASE}/nodejs/onlineUserDao/getUnique",
            data={"daoName": "onlineUserDao", "method": "getUnique", "params[]": username})
        user = r2.json()[0]
        seller_id = user["sellerId"]

        r3 = await client.post(f"{BASE}/nodejs/onlineOrderDao/findByClientId",
            data={"daoName": "onlineOrderDao", "method": "findByClientId",
                  "params[clientId]": username, "params[sellerId]": username,
                  "params[isClient]": "true", "params[userName]": username,
                  "params[zone]": zone, "params[quotaSellerId]": username})
        order_id = str(next(o for o in r3.json() if o.get("orderEndDate") is None)["id"])

        r4 = await client.post(f"{BASE}/nodejs/onlineProductDao/findAllWithOrder",
            data={
                "daoName": "onlineProductDao", "method": "findAllWithOrder",
                "params[filter][departamentId]": "210",
                "params[filter][sectorId]": "210010",
                "params[filter][onlypaquete]": "true",
                "params[filter][withStock]": "true",
                "params[filter][buyArticles][]": "-1",
                "params[filter][limit]": "50",
                "params[filter][offsetProducts]": "0",
                "params[filter][currentOrder][id]": order_id,
                "params[sellerId]": seller_id,
                "params[isClient]": "true",
                "params[userName]": username,
                "params[zone]": zone,
                "params[quotaSellerId]": username,
            })
        products = r4.json()
        print(f"Total products in sector (from API): {products[0].get('totalProducts')}")
        print(f"\n--- First product (all fields) ---")
        print(json.dumps(products[0], indent=2, ensure_ascii=False))

asyncio.run(main())
