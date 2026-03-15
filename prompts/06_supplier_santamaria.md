# Prompt — Supplier: Santa Maria

> Prerequisite: prompts 01–03 complete (scraper core + Maxiconsumo working).
> Full recon notes: analysis/santamaria/NOTES.md

---

## Context

- Site: https://tienda.santamariasa.com.ar/comercio/
- Platform: osCommerce (classic PHP — index.php?cPath=, osCsid session)
- Approach: httpx + BeautifulSoup (static HTML, no JS rendering needed)
- Auth: session-based login, cookie carried automatically by httpx AsyncClient
- Credentials env vars: SANTAMARIA_USER, SANTAMARIA_PASS

---

## Step 1 — Login form (already verified, skip inspect step)

Form details confirmed manually:
- Action: `https://tienda.santamariasa.com.ar/comercio/login.php?action=process`
- Fields: `email_address`, `password`
- Hidden field: `formid` (CSRF token — must be extracted fresh each login from the login page HTML)

Skip `inspect_login.py`. Proceed directly to Step 2.

---

## Step 2 — Verify login and inspect a category page

```python
# analysis/santamaria/selector_debug.py
import asyncio, httpx, os, warnings
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()
warnings.filterwarnings("ignore")

BASE = "https://tienda.santamariasa.com.ar/comercio"

async def main():
    async with httpx.AsyncClient(follow_redirects=True, verify=False) as client:

        # 1. Fetch login page, extract hidden fields
        r = await client.get(f"{BASE}/login.php")
        soup = BeautifulSoup(r.text, "lxml")
        form = soup.select_one("form")
        hidden = {i["name"]: i.get("value", "") for i in form.select("input[type=hidden]")}
        action = form.get("action", "login.php")
        if not action.startswith("http"):
            action = f"{BASE}/{action.lstrip('/')}"

        # 2. POST login
        payload = {
            **hidden,
            # UPDATE these field names from Step 1 output:
            "email_address": os.getenv("SANTAMARIA_USER"),
            "password":      os.getenv("SANTAMARIA_PASS"),
        }
        r = await client.post(action, data=payload)
        print("After login URL:", str(r.url))
        print("Login success:", "login.php" not in str(r.url))

        # 3. Fetch a known leaf category
        r = await client.get(f"{BASE}/index.php?cPath=1_101")
        soup = BeautifulSoup(r.text, "lxml")

        # Print full HTML of the first product row/block to find selectors
        # Try common osCommerce patterns:
        for selector in [
            ".productListing-odd", ".productListing-even",
            "td.productListing-data", ".product-listing tr",
            "table.productListing tr",
        ]:
            items = soup.select(selector)
            if items:
                print(f"\nSelector '{selector}' → {len(items)} items")
                print(items[0].prettify()[:1500])
                break
        else:
            print("\nNo product items found with standard selectors.")
            print("Printing body snippet:")
            print(soup.body.prettify()[:3000] if soup.body else r.text[:3000])

        # 4. Check pagination
        pages = soup.select("a[href*='page=']")
        print(f"\nPagination links: {[a['href'] for a in pages[:5]]}")

asyncio.run(main())
```

From this output, determine:
1. The correct product item selector
2. Where name, price, SKU/product_id are within the item
3. How pagination links look
4. Whether prices are visible (confirms login worked)

Update `analysis/santamaria/NOTES.md` with all verified selectors.

---

## Step 3 — Also check product detail page for SKU

osCommerce may not show a supplier SKU in category listings. Check the product page:

```python
# Add to selector_debug.py or run separately
async with httpx.AsyncClient(follow_redirects=True, verify=False) as client:
    # Login first (same as above), then:
    r = await client.get(f"{BASE}/product_info.php?products_id=463")
    soup = BeautifulSoup(r.text, "lxml")
    print(soup.body.prettify()[:4000])
```

Look for: barcode, EAN, SKU, internal reference, or any product code field.
If none found: use `products_id` from the URL as the SKU.

---

## Step 4 — Implement `scraper/suppliers/santamaria.py`

Use the verified selectors from the debug steps above.

```python
"""
Santa Maria supplier.
URL: https://tienda.santamariasa.com.ar/comercio/
Platform: osCommerce (classic PHP)
Approach: httpx + BeautifulSoup, static HTML
Auth: session-based form login (cookie carried automatically by httpx)
SSL: verify=False (site has incomplete cert chain)
Price format: verify after first authenticated run — likely Argentine ($1.234,56)
"""

import asyncio
import logging
import os
import re
import warnings
from urllib.parse import urljoin, urlparse, parse_qs

import httpx
from bs4 import BeautifulSoup

from scraper.suppliers.base import BaseSupplier

warnings.filterwarnings("ignore", message="Unverified HTTPS request")
logger = logging.getLogger(__name__)

BASE = "https://tienda.santamariasa.com.ar/comercio"


class SantaMariaSupplier(BaseSupplier):

    async def login(self, client: httpx.AsyncClient) -> None:
        """Authenticate via osCommerce form login."""
        r = await client.get(f"{BASE}/login.php")
        soup = BeautifulSoup(r.text, "lxml")

        soup = BeautifulSoup(r.text, "lxml")
        formid_el = soup.select_one('input[name="formid"]')
        if not formid_el:
            raise RuntimeError("SantaMaria: formid CSRF token not found on login page")

        payload = {
            "formid":        formid_el["value"],
            "email_address": os.getenv(self.config["credentials_env"]["username"]),
            "password":      os.getenv(self.config["credentials_env"]["password"]),
        }

        resp = await client.post(
            "https://tienda.santamariasa.com.ar/comercio/login.php?action=process",
            data=payload,
        )

        if "login.php" in str(resp.url):
            raise RuntimeError(
                "SantaMaria: login failed — check SANTAMARIA_USER and SANTAMARIA_PASS"
            )
        logger.info("SantaMaria: authenticated successfully")

    async def discover_categories(self, client: httpx.AsyncClient) -> list[str]:
        """Extract all leaf category URLs (cPath=N_M pattern) from the homepage."""
        r = await client.get(f"{BASE}/index.php")
        soup = BeautifulSoup(r.text, "lxml")

        urls = set()
        for a in soup.select("a[href*='cPath=']"):
            href = a.get("href", "")
            # Leaf categories have cPath=N_M (underscore = subcategory)
            if re.search(r"cPath=\d+_\d+", href):
                # Strip osCsid — the session cookie handles auth
                clean = re.sub(r"[&?]?osCsid=[^&]*", "", href).strip("&?")
                full = clean if clean.startswith("http") else f"{BASE}/{clean.lstrip('/')}"
                urls.add(full)

        logger.info(f"SantaMaria: discovered {len(urls)} leaf categories")
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
            page_url = url if page == 1 else f"{url}&page={page}"
            try:
                async with sem:
                    await asyncio.sleep(0.3)
                    r = await client.get(page_url)
                r.raise_for_status()

                # Redirect to login = session expired
                if "login.php" in str(r.url):
                    logger.error("SantaMaria: session expired mid-scrape")
                    break

            except httpx.HTTPError as e:
                logger.warning(f"SantaMaria: HTTP error on {page_url} — {e}")
                break

            soup = BeautifulSoup(r.text, "lxml")
            sel = self.config["selectors"]
            items = soup.select(sel["product_item"])

            if not items:
                break

            for item in items:
                product = self._parse_product(item, category)
                if product:
                    results.append(product)

            # Check for next page
            if not soup.select_one(sel["next_page"]):
                break
            page += 1

        return results

    def parse_price(self, raw: str) -> float | None:
        # Verify price format after first authenticated run.
        # If Argentine format: use self._parse_argentine_price(raw)
        # If standard format: implement here
        return self._parse_argentine_price(raw)

    # ------------------------------------------------------------------ #

    def _parse_product(self, item, category: str) -> dict | None:
        """Extract product dict from a product item element."""
        try:
            sel = self.config["selectors"]

            name_el  = item.select_one(sel["name"])
            price_el = item.select_one(sel["price"])
            link_el  = item.select_one(sel["product_link"])

            if not name_el:
                return None

            name = name_el.get_text(strip=True)
            price_unit = self.parse_price(price_el.get_text()) if price_el else None

            # Extract products_id from href as fallback SKU
            href = link_el.get("href", "") if link_el else ""
            sku_match = re.search(r"products_id=(\d+)", href)
            sku = sku_match.group(1) if sku_match else None

            if not sku:
                return None

            full_url = href if href.startswith("http") else f"{BASE}/{href.lstrip('/')}"
            # Clean osCsid from product URL
            full_url = re.sub(r"[&?]?osCsid=[^&]*", "", full_url).strip("&?")

            return {
                "sku":        sku,
                "name":       name,
                "url":        full_url,
                "category":   category,
                "price_unit": price_unit,
                "price_bulk": None,  # osCommerce typically shows one price
                "stock":      "unknown",
            }

        except Exception as e:
            logger.warning(f"SantaMaria: failed to parse product — {e}")
            return None

    def _extract_category(self, url: str) -> str:
        qs = parse_qs(urlparse(url).query)
        cpath = qs.get("cPath", [""])[0]
        return cpath  # Raw cPath for now — improve once category names are visible
```

---

## Step 5 — Register in `scraper/config.py`

```python
{
    "id": "santamaria",
    "class": "SantaMariaSupplier",
    "module": "scraper.suppliers.santamaria",
    "base_url": "https://tienda.santamariasa.com.ar/comercio",
    "requires_login": True,
    "login_page_url": "https://tienda.santamariasa.com.ar/comercio/login.php",
    "login_post_url": "https://tienda.santamariasa.com.ar/comercio/login.php?action=process",
    "credentials_env": {
        "username": "SANTAMARIA_USER",
        "password": "SANTAMARIA_PASS",
    },
    "selectors": {
        # UPDATE these after running selector_debug.py in Step 2
        "product_item":   ".productListing-odd, .productListing-even",
        "name":           "a",           # first <a> in product item — verify
        "price":          ".productListing-price",
        "product_link":   "a[href*='product_info.php']",
        "next_page":      "a[href*='page=']:last-of-type",
    },
    "category_urls": [],
    "concurrency": 6,  # Conservative — old PHP site, likely lower capacity
    "http_verify_ssl": False,
},
```

Also add to `.env.example`:
```
SANTAMARIA_USER=
SANTAMARIA_PASS=
```

And update `httpx.AsyncClient` instantiation in `scraper/scraper.py` to read `verify` from config:
```python
verify = not config.get("http_verify_ssl", False) is True
# i.e.: verify=True by default, verify=False only if explicitly set
async with httpx.AsyncClient(verify=not config.get("http_verify_ssl", False), ...) as client:
```

---

## Step 6 — Test single category

```bash
python -c "
import asyncio, httpx
from scraper.config import get_supplier_config, load_supplier_class

async def test():
    config = get_supplier_config('santamaria')
    supplier = load_supplier_class(config)
    sem = asyncio.Semaphore(3)
    async with httpx.AsyncClient(follow_redirects=True, verify=False) as client:
        await supplier.login(client)
        products = await supplier.scrape_category(
            client,
            'https://tienda.santamariasa.com.ar/comercio/index.php?cPath=1_101',
            sem
        )
    print(f'Got {len(products)} products')
    for p in products[:3]:
        print(p)

asyncio.run(test())
"
```

**Checkpoint:** products returned with non-None name, sku, price_unit.
If selectors are wrong: go back to selector_debug.py, fix, update config.

---

## Step 7 — Full scrape and verify

```bash
python scraper/main.py scrape --supplier santamaria
```

Check run_log:
```bash
python -c "
import asyncio
from scraper.db import get_pool
async def check():
    pool = await get_pool()
    async with pool.acquire() as conn:
        r = await conn.fetchrow(\"SELECT * FROM run_log WHERE supplier='santamaria' ORDER BY id DESC LIMIT 1\")
    print(dict(r))
    await pool.close()
asyncio.run(check())
"
```

**Checkpoint:** status = 'success', snapshots_written > 0.

---

## Known unknowns — resolve during implementation

| Unknown | How to resolve |
|---|---|
| ~~Exact login form field names~~ | ✅ Confirmed: email_address, password, formid |
| Product item CSS selector | Step 2: selector_debug.py |
| Price format (Argentine vs standard) | Check raw price text after login |
| Whether SKU/barcode is in listing HTML | Step 3: product detail page check |
| Whether category names are in nav HTML | Check homepage nav after login |
| `next_page` selector | Check pagination HTML in category page |

Update `analysis/santamaria/NOTES.md` with every answer.

---

## End of session

- Update `analysis/santamaria/NOTES.md` with all verified selectors and field names
- Add santamaria section to `scraper/CLAUDE.md`
- Update root `CLAUDE.md` Status
