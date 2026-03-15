# Prompt — Supplier: Luvik

> Prerequisite: prompts 01–03 complete.
> Full recon notes: analysis/luvik/NOTES.md

---

## Context

- Site: https://tiendaluvik.com.ar/
- Platform: Shopify
- Approach: httpx JSON API — Shopify's built-in /products.json endpoint
- Auth: likely none (public store) — verify in Step 1
- No HTML parsing needed

---

## Step 1 — Verify login requirement

Products are visible without login (confirmed). The question is whether login
reveals additional products. Run this to compare counts:

```python
# analysis/luvik/api_debug.py
import asyncio, httpx, os
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()
BASE = "https://tiendaluvik.com.ar"

async def main():
    # Guest count
    async with httpx.AsyncClient(follow_redirects=True) as client:
        r = await client.get(f"{BASE}/collections/girasol/products.json?limit=250")
        guest_count = len(r.json().get("products", []))
        print(f"Guest — girasol products: {guest_count}")

    # Authenticated count
    async with httpx.AsyncClient(follow_redirects=True) as client:
        r = await client.get(f"{BASE}/account/login")
        soup = BeautifulSoup(r.text, "lxml")
        token_el = soup.select_one('input[name="authenticity_token"]')
        if token_el:
            await client.post(f"{BASE}/account/login", data={
                "form_type":          "customer_login",
                "utf8":               "✓",
                "authenticity_token": token_el["value"],
                "customer[email]":    os.getenv("LUVIK_USER"),
                "customer[password]": os.getenv("LUVIK_PASS"),
            })
            r = await client.get(f"{BASE}/collections/girasol/products.json?limit=250")
            auth_count = len(r.json().get("products", []))
            print(f"Auth  — girasol products: {auth_count}")
            print(f"Login adds {auth_count - guest_count} products")
        else:
            print("Login form not found — may not use standard Shopify login")

asyncio.run(main())
```

**If counts are equal:** set `requires_login: False` in config, remove login logic.
**If auth adds products:** keep login as implemented.
**If login form not found:** investigate — may use a different auth mechanism.

---

## Step 2 — Implement `scraper/suppliers/luvik.py`

```python
"""
Luvik supplier.
URL: https://tiendaluvik.com.ar/
Platform: Shopify
Approach: httpx JSON API (/products.json) — no HTML parsing
Auth: none (public store)
Price format: standard decimal string ("1500.00") — parse with float()
SKU: Shopify variants[0].sku — often contains EAN/barcode
"""

import asyncio
import logging

import httpx

from scraper.suppliers.base import BaseSupplier

logger = logging.getLogger(__name__)

BASE = "https://tiendaluvik.com.ar"
PAGE_SIZE = 250  # Shopify hard limit


class LuvikSupplier(BaseSupplier):

    async def login(self, client: httpx.AsyncClient) -> None:
        """
        Shopify customer login — required to see all products.
        Some products are only visible to logged-in wholesale accounts.
        """
        import os
        username = os.getenv(self.config["credentials_env"].get("username", ""))
        password = os.getenv(self.config["credentials_env"].get("password", ""))

        if not username or not password:
            logger.warning("Luvik: no credentials set, scraping as guest (some products may be missing)")
            return

        # GET login page to extract authenticity_token (Shopify CSRF)
        r = await client.get(f"{BASE}/account/login")
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(r.text, "lxml")
        token_el = soup.select_one('input[name="authenticity_token"]')
        if not token_el:
            raise RuntimeError("Luvik: authenticity_token not found on login page")

        resp = await client.post(f"{BASE}/account/login", data={
            "form_type":           "customer_login",
            "utf8":                "✓",
            "authenticity_token":  token_el["value"],
            "customer[email]":     username,
            "customer[password]":  password,
            "return_url":          "/account",
        })

        if "/account/login" in str(resp.url):
            raise RuntimeError("Luvik: login failed — check LUVIK_USER and LUVIK_PASS")
        logger.info("Luvik: authenticated successfully")

    async def discover_categories(self, client: httpx.AsyncClient) -> list[str]:
        """
        Parse all /collections/<slug> URLs from homepage nav.
        Returns collection API URLs directly.
        """
        r = await client.get(f"{BASE}/")
        r.raise_for_status()

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(r.text, "lxml")

        slugs = set()
        for a in soup.select("a[href*='/collections/']"):
            href = a.get("href", "")
            # Filter: leaf collections only (no /collections/all or /collections/vendor)
            parts = href.strip("/").split("/")
            if len(parts) == 2 and parts[0] == "collections" and parts[1] not in ("all", "vendors", "types"):
                slugs.add(parts[1])

        urls = [f"{BASE}/collections/{slug}/products.json" for slug in sorted(slugs)]
        logger.info(f"Luvik: discovered {len(urls)} collections")
        return urls

    async def scrape_category(
        self,
        client: httpx.AsyncClient,
        url: str,
        sem: asyncio.Semaphore,
    ) -> list[dict]:
        """
        Fetch all pages from a collection's products.json endpoint.
        url format: https://tiendaluvik.com.ar/collections/<slug>/products.json
        """
        results = []
        page = 1
        # Extract category name from URL for the category field
        category = url.split("/collections/")[1].replace("/products.json", "").replace("-", " ").title()

        while True:
            page_url = f"{url}?limit={PAGE_SIZE}&page={page}"
            try:
                async with sem:
                    await asyncio.sleep(0.3)
                    r = await client.get(page_url)
                r.raise_for_status()
            except httpx.HTTPError as e:
                logger.warning(f"Luvik: HTTP error on {page_url} — {e}")
                break

            products = r.json().get("products", [])
            if not products:
                break

            for p in products:
                parsed = self._parse_product(p, category)
                results.extend(parsed)  # _parse_product returns a list (one per variant)

            if len(products) < PAGE_SIZE:
                break
            page += 1

        return results

    def parse_price(self, raw: str) -> float | None:
        """Standard decimal string — no Argentine formatting."""
        try:
            return float(raw) if raw else None
        except (ValueError, TypeError):
            return None

    # ------------------------------------------------------------------ #

    def _parse_product(self, p: dict, category: str) -> list[dict]:
        """
        Expand all variants into separate rows.
        Each variant has its own SKU and price.

        Confirmed JSON structure (from live API):
          p["sku"] does not exist — SKU is on each variant
          v["sku"]              → internal supplier code e.g. "270038"
          v["price"]            → decimal string e.g. "63480.00"
          v["available"]        → bool — use for stock (inventory_quantity not present)
          v["title"]            → "Default Title" for single-variant products
          p["vendor"]           → brand name e.g. "CAÑUELAS"
        """
        results = []
        base_name = p.get("title", "")
        handle = p.get("handle", "")

        for v in p.get("variants", []):
            try:
                variant_title = v.get("title", "")
                # Don't append "Default Title" — it's a Shopify placeholder
                name = f"{base_name} — {variant_title}" if variant_title != "Default Title" else base_name

                sku = v.get("sku") or f"{p['id']}-{v['id']}"
                price_unit = self.parse_price(v.get("price"))
                stock = "disponible" if v.get("available") else "sin stock"

                results.append({
                    "sku":        sku,
                    "name":       name,
                    "url":        f"{BASE}/products/{handle}",
                    "category":   category,
                    "price_unit": price_unit,
                    "price_bulk": None,
                    "stock":      stock,
                })
            except Exception as e:
                logger.warning(f"Luvik: failed to parse variant {v.get('id')} of {p.get('id')} — {e}")

        return results
```

---

## Step 3 — Register in `scraper/config.py`

```python
{
    "id": "luvik",
    "class": "LuvikSupplier",
    "module": "scraper.suppliers.luvik",
    "base_url": "https://tiendaluvik.com.ar",
    "requires_login": True,
    "login_page_url": "https://tiendaluvik.com.ar/account/login",
    "login_post_url": "https://tiendaluvik.com.ar/account/login",
    "credentials_env": {
        "username": "LUVIK_USER",
        "password": "LUVIK_PASS",
    },
    "selectors": {},        # Not used — JSON API
    "category_urls": [],
    "concurrency": 4,       # Shopify rate limit: ~2 req/s unauthenticated
},
```

Add to `.env.example`:
```
LUVIK_USER=
LUVIK_PASS=
```

---

## Step 4 — Test single collection

```bash
python -c "
import asyncio, httpx
from scraper.config import get_supplier_config, load_supplier_class

async def test():
    config = get_supplier_config('luvik')
    supplier = load_supplier_class(config)
    sem = asyncio.Semaphore(2)
    async with httpx.AsyncClient(follow_redirects=True) as client:
        products = await supplier.scrape_category(
            client,
            'https://tiendaluvik.com.ar/collections/girasol/products.json',
            sem
        )
    print(f'Got {len(products)} products')
    for p in products[:3]:
        print(p)

asyncio.run(test())
"
```

**Checkpoint:** products with non-None name, sku, price_unit.
Check if `sku` contains an EAN (13-digit number) or is empty/generic.

---

## Step 5 — Full scrape and verify

```bash
python scraper/main.py scrape --supplier luvik
```

```bash
python -c "
import asyncio
from scraper.db import get_pool
async def check():
    pool = await get_pool()
    async with pool.acquire() as conn:
        r = await conn.fetchrow(\"SELECT * FROM run_log WHERE supplier='luvik' ORDER BY id DESC LIMIT 1\")
        pc = await conn.fetchval(\"SELECT COUNT(*) FROM products WHERE supplier='luvik'\")
    print(f'Products: {pc}')
    print(f'Last run: {dict(r)}')
    await pool.close()
asyncio.run(check())
"
```

**Checkpoint:** status = 'success', snapshots_written > 0.

---

## Variant note

Each Shopify product variant (e.g. "Caja x 6", "Caja x 12", "Unidad") is stored as a
separate row in the DB with its own SKU and price. The variant title is appended to the
product name. "Default Title" variants (single-variant products) are stored without suffix.

---

## End of session

- Update `analysis/luvik/NOTES.md` with verified SKU format and stock field behavior
- Add luvik section to `scraper/CLAUDE.md`
- Update root `CLAUDE.md` Status
