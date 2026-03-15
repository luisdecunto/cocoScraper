# Prompt — Supplier: Nini

> Prerequisite: prompts 01–03 complete.
> Full recon notes: analysis/nini/NOTES.md

---

## Context

- Site: http://ecommerce.nini.com.ar:8081/ventas.online/
- Platform: Custom ASP.NET + Node.js
- Approach: httpx JSON API — POST to /nodejs/<dao>/<method>
- Auth: ASP.NET cookie (.ASPXAUTH) + session params (sellerId, userName, orderId)
- No HTML parsing needed
- HTTP only (port 8081)

---

## Step 1 — Verify full login chain

All endpoints confirmed. Create `analysis/nini/login_debug.py` to verify the full
login chain end-to-end before implementing the supplier:

```python
# analysis/nini/login_debug.py
import asyncio, httpx, os, re, json, time
from dotenv import load_dotenv

load_dotenv()

BASE = "http://ecommerce.nini.com.ar:8081"

async def main():
    async with httpx.AsyncClient(follow_redirects=True) as client:
        username = os.getenv("NINI_USER")
        password = os.getenv("NINI_PASS")

        # Step 1 — ValidateUser
        ts = int(time.time() * 1000)
        r = await client.get(
            f"{BASE}/ventas.administracion/Account/ValidateUser",
            params={"userName": username, "password": password,
                    "callback": "_jqjsp", f"_{ts}": ""}
        )
        print("ValidateUser status:", r.status_code)
        print("Has .ASPXAUTH:", ".ASPXAUTH" in client.cookies)
        match = re.search(r'\((.+)\)', r.text)
        data = json.loads(match.group(1))
        zone = str(data["Zone"])
        print("Zone:", zone)

        # Step 2 — getUnique
        r2 = await client.post(
            f"{BASE}/nodejs/onlineUserDao/getUnique",
            data={"daoName": "onlineUserDao", "method": "getUnique", "params[]": username}
        )
        user = r2.json()[0]
        print("sellerId:", user["sellerId"], "userName:", user["userName"])

        # Step 3 — findByClientId → active order
        r3 = await client.post(
            f"{BASE}/nodejs/onlineOrderDao/findByClientId",
            data={
                "daoName":               "onlineOrderDao",
                "method":                "findByClientId",
                "params[clientId]":      username,
                "params[sellerId]":      username,
                "params[isClient]":      "true",
                "params[userName]":      username,
                "params[zone]":          zone,
                "params[quotaSellerId]": username,
            }
        )
        orders = r3.json()
        active = next((o for o in orders if o.get("orderEndDate") is None), None)
        print("Active order id:", active["id"] if active else "NOT FOUND")
        print("Total orders in history:", len(orders))

asyncio.run(main())
```

**Checkpoints:**
- ValidateUser 200, `.ASPXAUTH` in cookies, Zone parsed
- getUnique returns sellerId and userName
- Active order ID printed (not None)

All three must pass before continuing.

---

## Step 3 — Implement `scraper/suppliers/nini.py`

```python
"""
Nini supplier.
URL: http://ecommerce.nini.com.ar:8081/ventas.online/
Platform: Custom ASP.NET + Node.js
Approach: httpx JSON API — POST /nodejs/<dao>/<method>
Auth: .ASPXAUTH cookie (ASP.NET forms auth) + session params
Price: float, no parsing needed
SKU: internal product id field (no EAN available)
"""

import asyncio
import logging
import os

import httpx

from scraper.suppliers.base import BaseSupplier

logger = logging.getLogger(__name__)

BASE = "http://ecommerce.nini.com.ar:8081"

# Confirmed department IDs
DEPARTMENTS = {
    "Almacen":      "210",
    "Anexo":        "220",
    "Bebidas":      "230",
    "Golosinas":    "240",
    "Limpieza":     "250",
    "Mascotas":     "260",
    "Perfumeria":   "270",
    "Refrigerados": "290",
}


class NiniSupplier(BaseSupplier):

    def __init__(self, config: dict):
        super().__init__(config)
        # Session state — populated during login
        self._seller_id:  str | None = None
        self._user_name:  str | None = None
        self._order_id:   str | None = None
        self._zone:       str = "10000002"

    async def login(self, client: httpx.AsyncClient) -> None:
        """
        Three-step login:
        1. GET ValidateUser → sets .ASPXAUTH cookie, returns Zone
        2. POST getUnique → gets sellerId
        3. Fetch active orderId (endpoint TBD — see Step 2 of this prompt)
        """
        import re, json, time

        username = os.getenv(self.config["credentials_env"]["username"])
        password = os.getenv(self.config["credentials_env"]["password"])

        # Step 1 — ValidateUser (JSONP GET, credentials in query string)
        timestamp = int(time.time() * 1000)
        r = await client.get(
            f"{BASE}/ventas.administracion/Account/ValidateUser",
            params={
                "userName": username,
                "password": password,
                "callback": "_jqjsp",
                f"_{timestamp}": "",
            }
        )
        if ".ASPXAUTH" not in client.cookies:
            raise RuntimeError(
                "Nini: .ASPXAUTH cookie not set — login failed. "
                "Check NINI_USER and NINI_PASS."
            )

        # Parse Zone from JSONP response: _jqjsp({"Rol":"3","Zone":10000002})
        match = re.search(r'\((.+)\)', r.text)
        if match:
            data = json.loads(match.group(1))
            self._zone = str(data.get("Zone", "10000002"))

        # Step 2 — getUnique → sellerId, userName
        r2 = await client.post(
            f"{BASE}/nodejs/onlineUserDao/getUnique",
            data={
                "daoName":  "onlineUserDao",
                "method":   "getUnique",
                "params[]": username,
            }
        )
        user_data = r2.json()[0]
        self._seller_id = user_data["sellerId"]
        self._user_name = user_data["userName"]
        logger.info(f"Nini: authenticated as userName={self._user_name} sellerId={self._seller_id} zone={self._zone}")

        # Step 3 — fetch active orderId
        self._order_id = await self._fetch_order_id(client)
        logger.info(f"Nini: active orderId={self._order_id}")

    async def discover_categories(self, client: httpx.AsyncClient) -> list[str]:
        """
        For each department, fetch sectors via onlineSectorDao/findFacets.
        Returns pseudo-URLs: "nini-sector:<departamentId>:<sectorId>:<description>"
        """
        categories = []
        for dept_name, dept_id in DEPARTMENTS.items():
            sectors = await self._fetch_sectors(client, dept_id)
            for s in sectors:
                categories.append(f"nini-sector:{dept_id}:{s['id']}:{s['description']}")
            logger.info(f"Nini: {dept_name} → {len(sectors)} sectors")
        return categories

    async def scrape_category(
        self,
        client: httpx.AsyncClient,
        url: str,
        sem: asyncio.Semaphore,
    ) -> list[dict]:
        """
        Fetch all products for a sector, paginated by 50.
        url format: "nini-sector:<departamentId>:<sectorId>:<description>"
        """
        _, dept_id, sector_id, description = url.split(":", 3)
        results = []
        offset = 0

        while True:
            payload = self._build_product_payload(dept_id, sector_id, offset)
            try:
                async with sem:
                    await asyncio.sleep(0.3)
                    r = await client.post(
                        f"{BASE}/nodejs/onlineProductDao/findAllWithOrder",
                        data=payload,
                    )
                r.raise_for_status()
            except httpx.HTTPError as e:
                logger.warning(f"Nini: HTTP error on sector {sector_id} offset {offset} — {e}")
                break

            products = r.json()
            if not products:
                break

            for p in products:
                parsed = self._parse_product(p, description)
                if parsed:
                    results.append(parsed)

            if len(products) < 50:
                break
            offset += 50

        return results

    def parse_price(self, raw) -> float | None:
        try:
            return float(raw) if raw is not None else None
        except (ValueError, TypeError):
            return None

    # ------------------------------------------------------------------ #

    async def _fetch_sectors(self, client: httpx.AsyncClient, dept_id: str) -> list[dict]:
        """Fetch subcategories for a department."""
        r = await client.post(
            f"{BASE}/nodejs/onlineSectorDao/findFacets",
            data=self._build_filter_payload(
                daoName="onlineSectorDao",
                method="findFacets",
                dept_id=dept_id,
                sector_id="null",
            )
        )
        r.raise_for_status()
        return r.json()

    async def _fetch_order_id(self, client: httpx.AsyncClient) -> str:
        """
        Fetch the active order ID via onlineOrderDao/findByClientId.
        Active order = first element with orderEndDate == None.
        """
        r = await client.post(
            f"{BASE}/nodejs/onlineOrderDao/findByClientId",
            data={
                "daoName":               "onlineOrderDao",
                "method":                "findByClientId",
                "params[clientId]":      self._user_name,
                "params[sellerId]":      self._user_name,
                "params[isClient]":      "true",
                "params[userName]":      self._user_name,
                "params[zone]":          self._zone,
                "params[quotaSellerId]": self._user_name,
            }
        )
        orders = r.json()
        active = next((o for o in orders if o.get("orderEndDate") is None), None)
        if active is None:
            raise RuntimeError(
                "Nini: no active order found (orderEndDate is None). "
                "Client may need to create a new order manually in the browser."
            )
        return active["id"]

    def _build_filter_payload(
        self,
        daoName: str,
        method: str,
        dept_id: str,
        sector_id: str = "null",
        offset: int = 0,
        limit: int = 50,
        extra: dict | None = None,
    ) -> dict:
        payload = {
            "daoName": daoName,
            "method":  method,
            "params[filter][where]":               "",
            "params[filter][staticWhere]":         "",
            "params[filter][departamentId]":       dept_id,
            "params[filter][sectorId]":            sector_id,
            "params[filter][lineId]":              "null",
            "params[filter][sublineId]":           "null",
            "params[filter][catalogId]":           "null",
            "params[filter][orderId]":             "null",
            "params[filter][onlypaquete]":         "true",
            "params[filter][onlyrelated]":         "null",
            "params[filter][trademarkId]":         "null",
            "params[filter][supplierId]":          "null",
            "params[filter][presentation]":        "null",
            "params[filter][selectedPopular]":     "null",
            "params[filter][showMostPopular]":     "false",
            "params[filter][currentOrder][id]":    self._order_id or "null",
            "params[filter][articlesInCatalog]":   "false",
            "params[filter][offsetPromotions]":    "0",
            "params[filter][offsetProducts]":      str(offset),
            "params[filter][magazinePage]":        "null",
            "params[filter][advertisingProductId]": "null",
            "params[objectiveGroup]":              "null",
            "params[showStrategicPartners]":       "null",
            "params[sellerId]":                    self._seller_id,
            "params[isClient]":                    "true",
            "params[userName]":                    self._user_name,
            "params[zone]":                        self._zone,
            "params[quotaSellerId]":               self._user_name,
        }
        if extra:
            payload.update(extra)
        return payload

    def _build_product_payload(self, dept_id: str, sector_id: str, offset: int) -> dict:
        return self._build_filter_payload(
            daoName="onlineProductDao",
            method="findAllWithOrder",
            dept_id=dept_id,
            sector_id=sector_id,
            offset=offset,
            extra={
                "params[filter][withStock]":             "true",
                "params[filter][buyArticles][]":         "-1",
                "params[filter][limit]":                 "50",
                "params[filter][currentOrder][client][averageOrderCost]": "0",
                "params[filter][currentOrder][totalCost]": "0",
                "params[filter][offsetProducts]":        str(offset),
            }
        )

    def _parse_product(self, p: dict, category: str) -> dict | None:
        try:
            return {
                "sku":        p["id"],
                "name":       p.get("largeDescription", p.get("smallDescription", "")),
                "url":        f"{BASE}/ventas.online/?nini.controllers.listadoDeProductos",
                "category":   category,
                "price_unit": self.parse_price(p.get("price")),
                "price_bulk": None,
                "stock":      p.get("stock", "unknown"),
            }
        except Exception as e:
            logger.warning(f"Nini: failed to parse product {p.get('id')} — {e}")
            return None
```

---

## Step 4 — Register in `scraper/config.py`

```python
{
    "id": "nini",
    "class": "NiniSupplier",
    "module": "scraper.suppliers.nini",
    "base_url": "http://ecommerce.nini.com.ar:8081",
    "requires_login": True,
    "login_page_url": "http://ecommerce.nini.com.ar:8081/ventas.online/?nini.controllers.login",
    "login_post_url": None,   # Determined in Step 1
    "credentials_env": {
        "username": "NINI_USER",
        "password": "NINI_PASS",
    },
    "selectors": {},          # Not used — JSON API
    "category_urls": [],
    "concurrency": 6,
},
```

Add to `.env.example`:
```
NINI_USER=
NINI_PASS=
```

---

## Step 5 — Test after resolving Steps 1 and 2

```bash
python -c "
import asyncio, httpx
from scraper.config import get_supplier_config, load_supplier_class

async def test():
    config = get_supplier_config('nini')
    supplier = load_supplier_class(config)
    sem = asyncio.Semaphore(3)
    async with httpx.AsyncClient(follow_redirects=True) as client:
        await supplier.login(client)
        print('sellerId:', supplier._seller_id)
        print('userName:', supplier._user_name)
        print('orderId: ', supplier._order_id)

        cats = await supplier.discover_categories(client)
        print(f'Categories: {len(cats)}')
        print(f'First 3: {cats[:3]}')

        products = await supplier.scrape_category(client, cats[0], sem)
        print(f'Products in first sector: {len(products)}')
        for p in products[:3]:
            print(p)

asyncio.run(test())
"
```

**Checkpoint:** login succeeds, orderId is populated, categories discovered, products returned with float prices.

---

## Step 6 — Full scrape and verify

```bash
python scraper/main.py scrape --supplier nini
```

```bash
python -c "
import asyncio
from scraper.db import get_pool
async def check():
    pool = await get_pool()
    async with pool.acquire() as conn:
        r = await conn.fetchrow(\"SELECT * FROM run_log WHERE supplier='nini' ORDER BY id DESC LIMIT 1\")
        pc = await conn.fetchval(\"SELECT COUNT(*) FROM products WHERE supplier='nini'\")
    print(f'Products: {pc}')
    print(f'Last run: {dict(r)}')
    await pool.close()
asyncio.run(check())
"
```

---

## Known unknowns — must resolve before implementation

| Unknown | Status | How to resolve |
|---|---|---|
| Login URL + field names | ✅ Confirmed — GET ValidateUser with JSONP | Done |
| Zone from login response | ✅ Parsed from JSONP response | Done |
| Order ID endpoint | ❌ Not yet confirmed | Step 1: login_debug.py probe |

---

## End of session

- Update `analysis/nini/NOTES.md` with confirmed login URL, field names, and order ID endpoint
- Add nini section to `scraper/CLAUDE.md`
- Update root `CLAUDE.md` Status
