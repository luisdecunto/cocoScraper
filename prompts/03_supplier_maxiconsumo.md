# Prompt 03 — Supplier: Maxiconsumo

> Paste this into Claude Code after prompt 02 is complete.
> Prerequisite: db.py, scraper.py, export.py, main.py are all working.
> Goal: implement scraper/suppliers/maxiconsumo.py and verify it works end-to-end.

---

## Context

- Site: `https://maxiconsumo.com/sucursal_moreno/`
- Platform: Magento 2
- Approach: `httpx` + `BeautifulSoup` (static HTML — no Playwright needed)
- Auth: required for supplier-tier pricing ("categorizado")
- Full recon notes: `analysis/maxiconsumo/NOTES.md`

---

## Step 1 — Verify selectors before writing any scraping code

Create a throwaway debug script at `analysis/maxiconsumo/selector_debug.py`.
Run it and confirm all selectors work on live HTML.

```python
"""
Throwaway script — verify Maxiconsumo CSS selectors.
Run with: python analysis/maxiconsumo/selector_debug.py
Delete or keep in analysis/ — never import from production code.
"""

import asyncio
import httpx
from bs4 import BeautifulSoup

TEST_URL = "https://maxiconsumo.com/sucursal_moreno/almacen/aceites-y-vinagres/aceites.html?product_list_limit=12"

async def main():
    async with httpx.AsyncClient(follow_redirects=True) as client:
        r = await client.get(TEST_URL)
        soup = BeautifulSoup(r.text, "lxml")

        items = soup.select(".product-item")
        print(f"Found {len(items)} product items")

        if items:
            item = items[0]
            print("\n--- RAW HTML OF FIRST PRODUCT ITEM ---")
            print(item.prettify())
            print("\n--- EXTRACTED FIELDS ---")
            print("name:  ", item.select_one(".product-item-link"))
            print("sku:   ", item.select_one(".product-item-sku"))
            print("prices:", item.select(".price-box .price"))
            print("stock: ", item.select_one(".stock"))

        next_page = soup.select_one('a[title="Siguiente"]')
        print(f"\nnext_page link: {next_page}")

asyncio.run(main())
```

**Checkpoint:** all five selectors return non-None values.
If any selector returns None or empty, examine the raw HTML and fix the selector.
Update `analysis/maxiconsumo/NOTES.md` with the correct selectors before continuing.

---

## Step 2 — Implement `scraper/suppliers/maxiconsumo.py`

```python
"""
Maxiconsumo supplier.
URL: https://maxiconsumo.com/sucursal_moreno/
Platform: Magento 2
Approach: httpx + BeautifulSoup, static HTML
Auth: Magento form login (supplier-tier "categorizado" pricing)
Price format: Argentine ($1.234,56 -> 1234.56)
"""

import asyncio
import logging
import os
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from scraper.suppliers.base import BaseSupplier

logger = logging.getLogger(__name__)


class MaxiconsumoSupplier(BaseSupplier):

    async def login(self, client: httpx.AsyncClient) -> None:
        """Authenticate via Magento form login."""
        r = await client.get(self.config["login_page_url"])
        soup = BeautifulSoup(r.text, "lxml")

        form_key_el = soup.select_one('input[name="form_key"]')
        if not form_key_el:
            raise RuntimeError("Maxiconsumo: could not find form_key on login page")
        form_key = form_key_el["value"]

        resp = await client.post(
            self.config["login_post_url"],
            data={
                "form_key": form_key,
                "login[username]": os.getenv(self.config["credentials_env"]["username"]),
                "login[password]": os.getenv(self.config["credentials_env"]["password"]),
            },
            follow_redirects=True,
        )

        if "customer/account/login" in str(resp.url):
            raise RuntimeError(
                "Maxiconsumo: login failed — check MAXICONSUMO_USER and MAXICONSUMO_PASS in .env"
            )
        logger.info("Maxiconsumo: authenticated successfully")

    async def discover_categories(self, client: httpx.AsyncClient) -> list[str]:
        """Parse all leaf category URLs from the homepage nav."""
        r = await client.get(self.config["base_url"])
        soup = BeautifulSoup(r.text, "lxml")

        urls = set()
        for a in soup.select("nav a[href], .navigation a[href]"):
            href = a.get("href", "")
            path = urlparse(href).path
            # Leaf categories: end in .html and have 4+ path segments
            if path.endswith(".html") and path.count("/") >= 4:
                urls.add(href)

        logger.info(f"Maxiconsumo: discovered {len(urls)} category URLs")
        return sorted(urls)

    async def scrape_category(
        self,
        client: httpx.AsyncClient,
        url: str,
        sem: asyncio.Semaphore,
    ) -> list[dict]:
        """Scrape all products from a category, following pagination."""
        results = []
        page = 1
        category = self._extract_category(url)

        while True:
            page_url = f"{url}?product_list_limit=96&p={page}"
            try:
                async with sem:
                    await asyncio.sleep(0.3)
                    r = await client.get(page_url)
                r.raise_for_status()
            except httpx.HTTPError as e:
                logger.warning(f"Maxiconsumo: HTTP error on {page_url} — {e}")
                break

            soup = BeautifulSoup(r.text, "lxml")
            items = soup.select(self.config["selectors"]["product_item"])

            if not items:
                break

            for item in items:
                product = self._parse_product(item, category)
                if product:
                    results.append(product)

            if not soup.select_one(self.config["selectors"]["next_page"]):
                break
            page += 1

        return results

    def parse_price(self, raw: str) -> float | None:
        return self._parse_argentine_price(raw)

    # ------------------------------------------------------------------ #
    # Private helpers                                                      #
    # ------------------------------------------------------------------ #

    def _parse_product(self, item, category: str) -> dict | None:
        """Extract product data from a .product-item element."""
        try:
            sel = self.config["selectors"]

            name_el = item.select_one(sel["name"])
            sku_el  = item.select_one(sel["sku"])
            price_els = item.select(sel["prices"])
            stock_el  = item.select_one(sel["stock"])

            if not name_el or not sku_el:
                return None

            name = name_el.get_text(strip=True)
            sku  = sku_el.get_text(strip=True).replace("SKU", "").strip()
            url  = name_el.get("href", "")

            # Two prices: [0] = bulk (closed box), [1] = unit
            price_bulk = self.parse_price(price_els[0].get_text()) if len(price_els) > 0 else None
            price_unit = self.parse_price(price_els[1].get_text()) if len(price_els) > 1 else None

            stock = stock_el.get_text(strip=True) if stock_el else "unknown"

            return {
                "sku":        sku,
                "name":       name,
                "url":        url,
                "category":   category,
                "price_unit": price_unit,
                "price_bulk": price_bulk,
                "stock":      stock,
            }

        except Exception as e:
            logger.warning(f"Maxiconsumo: failed to parse product — {e}")
            return None

    def _extract_category(self, url: str) -> str:
        """Extract a readable category string from a category URL."""
        path = urlparse(url).path
        parts = [p for p in path.split("/") if p and p != "sucursal_moreno"]
        return " > ".join(parts).replace(".html", "").replace("-", " ").title()
```

---

## Step 3 — Test single category

```bash
python -c "
import asyncio, httpx
from bs4 import BeautifulSoup
from scraper.config import get_supplier_config, load_supplier_class
from scraper.db import get_pool

async def test():
    pool = await get_pool()
    config = get_supplier_config('maxiconsumo')
    supplier = load_supplier_class(config)
    sem = asyncio.Semaphore(3)
    async with httpx.AsyncClient(follow_redirects=True) as client:
        await supplier.login(client)
        products = await supplier.scrape_category(
            client,
            'https://maxiconsumo.com/sucursal_moreno/almacen/aceites-y-vinagres/aceites.html',
            sem
        )
    print(f'Got {len(products)} products')
    for p in products[:3]:
        print(p)
    await pool.close()

asyncio.run(test())
"
```

**Checkpoint:** at least 10 products returned, all with non-None name, sku, price_unit.

---

## Step 4 — Verify authenticated prices differ from guest

Run the debug script twice — once logged in, once without login — on the same product URL.
Prices should differ. If identical, login may have failed silently.

```bash
python -c "
import asyncio, httpx
from bs4 import BeautifulSoup
from scraper.config import get_supplier_config, load_supplier_class

async def check():
    config = get_supplier_config('maxiconsumo')
    supplier = load_supplier_class(config)

    url = 'https://maxiconsumo.com/sucursal_moreno/almacen/aceites-y-vinagres/aceites.html?product_list_limit=12'

    async with httpx.AsyncClient(follow_redirects=True) as client:
        # Guest prices
        r = await client.get(url)
        soup = BeautifulSoup(r.text, 'lxml')
        guest_prices = [el.get_text(strip=True) for el in soup.select('.price-box .price')[:4]]
        print('Guest prices:', guest_prices)

    async with httpx.AsyncClient(follow_redirects=True) as client:
        # Authenticated prices
        await supplier.login(client)
        r = await client.get(url)
        soup = BeautifulSoup(r.text, 'lxml')
        auth_prices = [el.get_text(strip=True) for el in soup.select('.price-box .price')[:4]]
        print('Auth prices: ', auth_prices)

asyncio.run(check())
"
```

**Checkpoint:** the two price lists are different. If they're the same, check login logic.

---

## Step 5 — Full scrape

```bash
python scraper/main.py scrape --supplier maxiconsumo
```

Monitor the log output. Expected: INFO messages per category, no ERRORs.

After it finishes:
```bash
# Check counts
python -c "
import asyncio
from scraper.db import get_pool
async def check():
    pool = await get_pool()
    async with pool.acquire() as conn:
        pc = await conn.fetchval(\"SELECT COUNT(*) FROM products WHERE supplier='maxiconsumo'\")
        sc = await conn.fetchval(\"SELECT COUNT(*) FROM price_snapshots WHERE supplier='maxiconsumo'\")
        rc = await conn.fetchrow(\"SELECT * FROM run_log WHERE supplier='maxiconsumo' ORDER BY id DESC LIMIT 1\")
    print(f'Products:  {pc}')
    print(f'Snapshots: {sc}')
    print(f'Last run:  {dict(rc)}')
    await pool.close()
asyncio.run(check())
"
```

**Checkpoint:** product count > 100, snapshots_written > 0, run status = 'success'.

---

## Step 6 — Test exports

```bash
python scraper/main.py export latest
python scraper/main.py export comparison
```

Open the CSV files and verify they contain readable data.

---

## End of session

Update root `CLAUDE.md` Status:
- Mark "Supplier: Maxiconsumo" as done
- Update decisions.md if any selector corrections were made
- Update `analysis/maxiconsumo/NOTES.md` with any corrected selectors
