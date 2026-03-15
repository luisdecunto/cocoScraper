# Prompt 04 — New Supplier: [SUPPLIER NAME]

> Copy this file, fill in every [PLACEHOLDER], and paste into Claude Code.
> Prerequisite: prompts 01–03 are complete (scraper core + Maxiconsumo working).
> Read docs/adding_a_supplier.md for the full guide.

---

## Supplier details

| Field | Value |
|---|---|
| Supplier ID | `[supplier_id]` — lowercase, no spaces, used as DB key and filename |
| Display name | `[Supplier Name]` |
| Base URL | `[https://...]` |
| Platform | `[Magento 2 / WooCommerce / custom / unknown]` |
| Login required | `[yes / no]` |
| Price format | `[Argentine ($1.234,56) / standard (1,234.56) / other: describe]` |
| JS rendering needed | `[no — use httpx+BS4 / yes — use Playwright / unknown — investigate first]` |
| Known API | `[none / yes — describe]` |

---

## Recon notes location

Before writing any code, document findings in:
`analysis/[supplier_id]/NOTES.md`

Create that file with the following sections filled in:

```markdown
# [Supplier Name] — Recon Notes

## Basic info
- URL:
- Platform:
- Login required:
- Price tiers (if any):

## Scraping approach decision
[ ] httpx + BeautifulSoup (static HTML)
[ ] Playwright (JS-rendered)
[ ] httpx JSON API (no HTML parsing)
[ ] Other: describe

Reason for choice:

## Auth flow (if login required)
-

## Price format
-

## Category structure
- URL pattern:
- Pagination pattern:

## CSS selectors (or API endpoints)
- product_item:
- name:
- sku:
- prices:
- stock:
- next_page:

## Anything unusual
-
```

---

## New env vars to add

Add these to `.env.example` (not `.env`):
```
[SUPPLIER_USER_ENV_VAR]=
[SUPPLIER_PASS_ENV_VAR]=
```

---

## Step 1 — Selector/API debug script

Create `analysis/[supplier_id]/selector_debug.py`.

**If httpx + BeautifulSoup:**
```python
import asyncio, httpx
from bs4 import BeautifulSoup

TEST_URL = "[one category URL]"

async def main():
    async with httpx.AsyncClient(follow_redirects=True) as client:
        r = await client.get(TEST_URL)
        soup = BeautifulSoup(r.text, "lxml")
        items = soup.select("[product_item selector]")
        print(f"Found {len(items)} items")
        if items:
            print(items[0].prettify())

asyncio.run(main())
```

**If Playwright:**
```python
import asyncio
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

TEST_URL = "[one category URL]"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(TEST_URL)
        await page.wait_for_selector("[a selector that appears when products load]")
        html = await page.content()
        await browser.close()
    soup = BeautifulSoup(html, "lxml")
    items = soup.select("[product_item selector]")
    print(f"Found {len(items)} items")
    if items:
        print(items[0].prettify())

asyncio.run(main())
```

**If JSON API:**
```python
import asyncio, httpx, json

API_URL = "[discovered API endpoint]"

async def main():
    async with httpx.AsyncClient() as client:
        r = await client.get(API_URL, params={"[param]": "[value]"})
        data = r.json()
        print(json.dumps(data, indent=2, ensure_ascii=False)[:2000])

asyncio.run(main())
```

Run the debug script. Confirm selectors return expected data before continuing.

---

## Step 2 — Implement `scraper/suppliers/[supplier_id].py`

```python
"""
[Supplier Name] supplier.
URL: [base_url]
Platform: [platform]
Approach: [httpx+BS4 / Playwright / JSON API]
Auth: [required/not required — describe]
Price format: [Argentine / standard / other]
"""

import asyncio
import logging
from scraper.suppliers.base import BaseSupplier

logger = logging.getLogger(__name__)


class [SupplierClassName]Supplier(BaseSupplier):

    async def login(self, client) -> None:
        # Implement or `pass` if no login needed
        ...

    async def discover_categories(self, client) -> list[str]:
        # Return list of category URLs to scrape
        ...

    async def scrape_category(self, client, url: str, sem) -> list[dict]:
        # Paginate through the category and return list of product dicts
        # Each dict: sku, name, url, category, price_unit, price_bulk, stock
        ...

    def parse_price(self, raw: str) -> float | None:
        # Use self._parse_argentine_price(raw) for Argentine format
        # or implement custom parsing
        ...
```

---

## Step 3 — Register in `scraper/config.py`

Add to the `SUPPLIERS` list:

```python
{
    "id": "[supplier_id]",
    "class": "[SupplierClassName]Supplier",
    "module": "scraper.suppliers.[supplier_id]",
    "base_url": "[base_url]",
    "requires_login": [True/False],
    "login_page_url": "[url or None]",
    "login_post_url": "[url or None]",
    "credentials_env": {
        "username": "[SUPPLIER_USER_ENV_VAR]",
        "password": "[SUPPLIER_PASS_ENV_VAR]",
    },
    "selectors": {
        # fill in or remove if using JSON API
    },
    "category_urls": [],
    "concurrency": 8,
},
```

---

## Step 4 — Test single category

```bash
python -c "
import asyncio, httpx
from scraper.config import get_supplier_config, load_supplier_class
async def test():
    config = get_supplier_config('[supplier_id]')
    supplier = load_supplier_class(config)
    sem = asyncio.Semaphore(3)
    async with httpx.AsyncClient(follow_redirects=True) as client:
        if config['requires_login']:
            await supplier.login(client)
        products = await supplier.scrape_category(client, '[one category url]', sem)
    print(f'Got {len(products)} products')
    for p in products[:3]:
        print(p)
asyncio.run(test())
"
```

**Checkpoint:** products returned with non-None name, sku, price_unit.

---

## Step 5 — Full scrape and verify

```bash
python scraper/main.py scrape --supplier [supplier_id]
```

Check run_log:
```bash
python -c "
import asyncio
from scraper.db import get_pool
async def check():
    pool = await get_pool()
    async with pool.acquire() as conn:
        r = await conn.fetchrow(\"SELECT * FROM run_log WHERE supplier='[supplier_id]' ORDER BY id DESC LIMIT 1\")
    print(dict(r))
    await pool.close()
asyncio.run(check())
"
```

**Checkpoint:** status = 'success', snapshots_written > 0.

---

## Step 6 — Run comparison export

```bash
python scraper/main.py export comparison
```

Verify the new supplier appears in the comparison XLSX.

---

## End of session

- Update `analysis/[supplier_id]/NOTES.md` with any corrections from testing
- Add a section for the new supplier in `scraper/CLAUDE.md`
- Update root `CLAUDE.md` Status section
