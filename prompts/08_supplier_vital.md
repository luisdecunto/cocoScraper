# Prompt — Supplier: Vital

> Prerequisite: prompts 01–03 complete.
> Full recon notes: analysis/vital/NOTES.md

---

## Context

- Site: https://tiendaonline.vital.com.ar/
- Platform: VTEX
- Approach: httpx JSON API — VTEX catalog/search API
- Auth: none — all prices public
- No HTML parsing needed

---

## Step 1 — Verify API and inspect response structure

```python
# analysis/vital/api_debug.py
import asyncio, httpx, json

BASE = "https://tiendaonline.vital.com.ar"

async def main():
    async with httpx.AsyncClient(follow_redirects=True) as client:

        # 1. Fetch category tree (depth 3)
        r = await client.get(f"{BASE}/api/catalog_system/pub/category/tree/3")
        print("Category tree status:", r.status_code)
        if r.status_code == 200:
            tree = r.json()
            print(f"Top-level categories: {len(tree)}")
            for cat in tree[:3]:
                print(f"  id={cat['id']} name={cat['name']} children={len(cat.get('children', []))}")
                for sub in cat.get("children", [])[:2]:
                    print(f"    id={sub['id']} name={sub['name']}")

        # 2. Fetch 3 products from first category
        if r.status_code == 200 and tree:
            first_cat_id = tree[0]["id"]
            r2 = await client.get(
                f"{BASE}/api/catalog_system/pub/products/search",
                params={"_from": 0, "_to": 2, "fq": f"C:/{first_cat_id}/"}
            )
            print(f"\nProducts search status: {r2.status_code}")
            print("Resources header:", r2.headers.get("X-VTEX-Resources-Info", "not present"))
            if r2.status_code == 200:
                products = r2.json()
                print(f"Products returned: {len(products)}")
                if products:
                    p = products[0]
                    print(f"\nSample product (raw):")
                    print(json.dumps(p, indent=2, ensure_ascii=False)[:2000])

asyncio.run(main())
```

**Checkpoints:**
- Category tree returns structured JSON with IDs → use for discovery
- Products search returns array with price data → confirm field names
- Check `X-VTEX-Resources-Info` header for total count
- Check if `referenceId` contains EAN/barcode
- Confirm `Price` is a float, not a string

Update `analysis/vital/NOTES.md` with any differences from expected structure.

---

## Step 2 — Implement `scraper/suppliers/vital.py`

```python
"""
Vital supplier.
URL: https://tiendaonline.vital.com.ar/
Platform: VTEX
Approach: httpx JSON API — VTEX catalog/search API
Auth: none — all prices public
Price: float returned directly from API, no parsing needed
"""

import asyncio
import logging
import re

import httpx

from scraper.suppliers.base import BaseSupplier

logger = logging.getLogger(__name__)

BASE = "https://tiendaonline.vital.com.ar"
PAGE_SIZE = 50  # VTEX hard limit per request


class VitalSupplier(BaseSupplier):

    async def login(self, client: httpx.AsyncClient) -> None:
        """
        VTEX two-step login.
        Step 1: startlogin — establishes session, sets _vss cookie
        Step 2: validate — authenticates, sets VtexIdclientAutCookie_arvital
        """
        import os

        email    = os.getenv(self.config["credentials_env"]["username"])
        password = os.getenv(self.config["credentials_env"]["password"])

        # Step 1 — startlogin
        await client.post(
            f"{BASE}/api/vtexid/pub/authentication/startlogin",
            data={
                "accountName": "arvital",
                "scope":       "arvital",
                "returnUrl":   f"{BASE}/",
                "callbackUrl": f"{BASE}/api/vtexid/oauth/finish?popup=false",
                "user":        email,
            },
        )

        # Step 2 — validate
        resp = await client.post(
            f"{BASE}/api/vtexid/pub/authentication/classic/validate",
            data={
                "login":          email,
                "password":       password,
                "recaptcha":      "",
                "fingerprint":    "",
                "recaptchaToken": "",
            },
        )

        body = resp.json()
        if body.get("authStatus") != "Success":
            raise RuntimeError(
                f"Vital: login failed — authStatus={body.get('authStatus')}. "
                "Check VITAL_USER and VITAL_PASS."
            )
        logger.info("Vital: authenticated successfully")

    async def discover_categories(self, client: httpx.AsyncClient) -> list[str]:
        """
        Fetch category tree and return all leaf category IDs as pseudo-URLs.
        Returns strings in format: "vtex-category:<id>:<name>"
        so scrape_category knows which category ID to query.
        """
        r = await client.get(f"{BASE}/api/catalog_system/pub/category/tree/3")
        r.raise_for_status()
        tree = r.json()

        leaves = []
        self._collect_leaves(tree, leaves)
        logger.info(f"Vital: discovered {len(leaves)} leaf categories")
        return leaves

    async def scrape_category(
        self,
        client: httpx.AsyncClient,
        url: str,           # format: "vtex-category:<id>:<name>"
        sem: asyncio.Semaphore,
    ) -> list[dict]:
        """Paginate through all products in a VTEX category."""
        # Parse the pseudo-URL
        _, cat_id, cat_name = url.split(":", 2)
        results = []
        offset = 0

        while True:
            params = {
                "_from": offset,
                "_to":   offset + PAGE_SIZE - 1,
                "fq":    f"C:/{cat_id}/",
            }
            try:
                async with sem:
                    await asyncio.sleep(0.3)
                    r = await client.get(
                        f"{BASE}/api/catalog_system/pub/products/search",
                        params=params,
                    )
                r.raise_for_status()
            except httpx.HTTPError as e:
                logger.warning(f"Vital: HTTP error on category {cat_id} offset {offset} — {e}")
                break

            products = r.json()
            if not products:
                break

            for p in products:
                parsed = self._parse_product(p, cat_name)
                results.extend(parsed)

            if len(products) < PAGE_SIZE:
                break
            offset += PAGE_SIZE

        return results

    def parse_price(self, raw) -> float | None:
        """VTEX returns price as float directly — no string parsing needed."""
        try:
            return float(raw) if raw is not None else None
        except (ValueError, TypeError):
            return None

    # ------------------------------------------------------------------ #

    def _collect_leaves(self, nodes: list, leaves: list, parent_name: str = "") -> None:
        """Recursively collect leaf category nodes from the tree."""
        for node in nodes:
            children = node.get("children", [])
            name = node["name"]
            full_name = f"{parent_name} > {name}" if parent_name else name
            if not children:
                # Leaf node
                leaves.append(f"vtex-category:{node['id']}:{full_name}")
            else:
                self._collect_leaves(children, leaves, full_name)

    def _parse_product(self, p: dict, category: str) -> list[dict]:
        """
        Extract product rows from a VTEX product object.
        One row per item (SKU/variant).
        """
        results = []
        base_name = p.get("productName", "")
        product_id = p.get("productId", "")
        link = p.get("link", "")

        for item in p.get("items", []):
            try:
                item_id = item.get("itemId", "")

                # EAN from referenceId if available
                ref_ids = item.get("referenceId", [])
                sku = next(
                    (r["Value"] for r in ref_ids if r.get("Key") == "RefId"),
                    item_id  # fallback to itemId
                )

                # Name: append item name if different from product name
                item_name = item.get("name", "")
                name = f"{base_name} — {item_name}" if item_name and item_name != base_name else base_name

                # Price from first available seller
                price_unit = None
                stock = "sin stock"
                for seller in item.get("sellers", []):
                    offer = seller.get("commertialOffer", {})
                    if offer.get("IsAvailable"):
                        price_unit = self.parse_price(offer.get("Price"))
                        stock = "disponible"
                        break
                    elif price_unit is None:
                        # Keep price even if unavailable
                        price_unit = self.parse_price(offer.get("Price"))

                results.append({
                    "sku":        sku,
                    "name":       name,
                    "url":        link,
                    "category":   category,
                    "price_unit": price_unit,
                    "price_bulk": None,
                    "stock":      stock,
                })

            except Exception as e:
                logger.warning(f"Vital: failed to parse item {item.get('itemId')} — {e}")

        return results
```

---

## Step 3 — Register in `scraper/config.py`

```python
{
    "id": "vital",
    "class": "VitalSupplier",
    "module": "scraper.suppliers.vital",
    "base_url": "https://tiendaonline.vital.com.ar",
    "requires_login": True,
    "login_page_url": "https://tiendaonline.vital.com.ar/login",
    "login_post_url": None,  # Two-step flow handled entirely in login()
    "credentials_env": {
        "username": "VITAL_USER",
        "password": "VITAL_PASS",
    },
    "selectors": {},        # Not used — JSON API
    "category_urls": [],
    "concurrency": 10,
},
```

Add to `.env.example`:
```
VITAL_USER=
VITAL_PASS=
```

---

## Step 4 — Test single category

```bash
python -c "
import asyncio, httpx
from scraper.config import get_supplier_config, load_supplier_class

async def test():
    config = get_supplier_config('vital')
    supplier = load_supplier_class(config)
    sem = asyncio.Semaphore(3)
    async with httpx.AsyncClient(follow_redirects=True) as client:
        # Discover to get first category
        cats = await supplier.discover_categories(client)
        print(f'Categories found: {len(cats)}')
        print(f'First 3: {cats[:3]}')

        # Scrape first category
        products = await supplier.scrape_category(client, cats[0], sem)
    print(f'Got {len(products)} products from first category')
    for p in products[:3]:
        print(p)

asyncio.run(test())
"
```

**Checkpoint:** categories discovered with IDs, products returned with float prices.

---

## Step 5 — Full scrape and verify

```bash
python scraper/main.py scrape --supplier vital
```

```bash
python -c "
import asyncio
from scraper.db import get_pool
async def check():
    pool = await get_pool()
    async with pool.acquire() as conn:
        r = await conn.fetchrow(\"SELECT * FROM run_log WHERE supplier='vital' ORDER BY id DESC LIMIT 1\")
        pc = await conn.fetchval(\"SELECT COUNT(*) FROM products WHERE supplier='vital'\")
    print(f'Products: {pc}')
    print(f'Last run: {dict(r)}')
    await pool.close()
asyncio.run(check())
"
```

**Checkpoint:** status = 'success', snapshots_written > 0, product count reasonable (expect 1000+).

---

## Known unknowns — resolve in Step 1

| Unknown | How to resolve |
|---|---|
| referenceId key name for EAN | Check raw product JSON in api_debug.py |
| Whether items[] has multiple variants | Check sample product with multiple items |
| X-VTEX-Resources-Info header format | Print headers in api_debug.py |
| Actual rate limit | Start at concurrency=10, reduce if 429s appear |

---

## End of session

- Update `analysis/vital/NOTES.md` with confirmed field names and any quirks
- Add vital section to `scraper/CLAUDE.md`
- Update root `CLAUDE.md` Status
