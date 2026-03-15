# Prompt 01 — Project Setup

> Paste this into Claude Code as the very first session.
> Goal: create the folder structure, config files, and verify the environment works.
> Do NOT write any scraping logic yet.

---

## Task

Set up the `cocoScraper` project skeleton. Create all folders and files listed below.
After each file is created, confirm it exists before moving to the next.

---

## Folder structure to create

```
cocoScraper/
├── .env.example
├── .gitignore
├── requirements.txt
├── scraper/
│   ├── __init__.py
│   ├── main.py
│   ├── db.py
│   ├── scraper.py
│   ├── export.py
│   ├── config.py
│   └── suppliers/
│       ├── __init__.py
│       └── base.py
├── analysis/
│   └── maxiconsumo/
│       └── NOTES.md
├── api/
│   └── __init__.py
├── logs/
│   └── .gitkeep
└── docs/
    └── decisions.md
```

---

## File contents

### `.gitignore`
```
.env
.venv/
__pycache__/
*.pyc
*.pyo
logs/*.log
*.egg-info/
dist/
.DS_Store
```

### `.env.example`
```
# PostgreSQL
DB_HOST=localhost
DB_PORT=5432
DB_NAME=prices
DB_USER=scraper
DB_PASS=

# Suppliers
MAXICONSUMO_USER=
MAXICONSUMO_PASS=

# API (future)
JWT_SECRET=
JWT_ALGORITHM=HS256
JWT_EXPIRE_MINUTES=480
```

### `requirements.txt`
```
httpx==0.27.0
beautifulsoup4==4.12.3
lxml==5.2.1
asyncpg==0.29.0
python-dotenv==1.0.1
apscheduler==3.10.4
openpyxl==3.1.2
```

### `scraper/suppliers/base.py`

Abstract base class. Every supplier must extend this.

```python
"""
Base supplier class.
Every supplier implementation must extend this and implement all abstract methods.
The internals of each method can use completely different approaches
(httpx, Playwright, JSON API, etc.) — the contract only defines the interface.
"""

import asyncio
from abc import ABC, abstractmethod
from typing import Optional
import httpx


class BaseSupplier(ABC):
    """Abstract base class for all supplier scrapers."""

    def __init__(self, config: dict):
        self.config = config

    @abstractmethod
    async def login(self, client: httpx.AsyncClient) -> None:
        """
        Authenticate with the supplier website.
        No-op if the supplier requires no login.
        Must raise RuntimeError if login fails.
        """

    @abstractmethod
    async def discover_categories(self, client: httpx.AsyncClient) -> list[str]:
        """
        Return a list of all leaf category URLs to scrape.
        Called once per run if config['category_urls'] is empty.
        """

    @abstractmethod
    async def scrape_category(
        self,
        client: httpx.AsyncClient,
        url: str,
        sem: asyncio.Semaphore,
    ) -> list[dict]:
        """
        Scrape all products from a category URL, including pagination.

        Returns a list of dicts, each with keys:
            sku          (str)
            name         (str)
            url          (str)
            category     (str)
            price_unit   (float | None)
            price_bulk   (float | None)
            stock        (str)
        """

    @abstractmethod
    def parse_price(self, raw: str) -> Optional[float]:
        """Parse a raw price string to float. Return None on failure."""

    def _parse_argentine_price(self, raw: str) -> Optional[float]:
        """
        Parse Argentine number format: $1.234,56 -> 1234.56
        Dot is thousands separator, comma is decimal.
        Use this as default implementation for Argentine suppliers.
        """
        try:
            cleaned = (
                raw.replace("$", "")
                   .replace("\xa0", "")
                   .replace(".", "")
                   .replace(",", ".")
                   .strip()
            )
            return float(cleaned) if cleaned else None
        except (ValueError, AttributeError):
            return None
```

### `scraper/config.py`

Supplier registry. Load `.env` here.

```python
"""
Supplier registry.
Add new suppliers to the SUPPLIERS list.
Each entry maps to a class in scraper/suppliers/<id>.py.
"""

import os
from dotenv import load_dotenv

load_dotenv()

SUPPLIERS: list[dict] = [
    {
        "id": "maxiconsumo",
        "class": "MaxiconsumoSupplier",
        "module": "scraper.suppliers.maxiconsumo",
        "base_url": "https://maxiconsumo.com/sucursal_moreno",
        "requires_login": True,
        "login_page_url": "https://maxiconsumo.com/sucursal_moreno/customer/account/login/",
        "login_post_url": "https://maxiconsumo.com/sucursal_moreno/customer/account/loginPost/",
        "credentials_env": {
            "username": "MAXICONSUMO_USER",
            "password": "MAXICONSUMO_PASS",
        },
        "selectors": {
            "product_item": ".product-item",
            "name":         ".product-item-link",
            "sku":          ".product-item-sku",
            "prices":       ".price-box .price",
            "stock":        ".stock",
            "next_page":    'a[title="Siguiente"]',
        },
        # Empty = auto-discover from homepage nav
        "category_urls": [],
        # Concurrent requests for this supplier
        "concurrency": 8,
    },
]


def get_supplier_config(supplier_id: str) -> dict:
    """Return config dict for a supplier id. Raises ValueError if not found."""
    for s in SUPPLIERS:
        if s["id"] == supplier_id:
            return s
    raise ValueError(f"Unknown supplier: '{supplier_id}'. Available: {[s['id'] for s in SUPPLIERS]}")


def load_supplier_class(config: dict):
    """Dynamically import and instantiate the supplier class from config."""
    import importlib
    module = importlib.import_module(config["module"])
    cls = getattr(module, config["class"])
    return cls(config)
```

### `scraper/db.py`

Stub only — full implementation in prompt 02.

```python
"""
Database layer.
PostgreSQL connection pool, schema init, and upsert logic.
Full implementation: see prompts/02_scraper_core.md
"""

import asyncpg
import os
import logging

logger = logging.getLogger(__name__)


async def get_pool() -> asyncpg.Pool:
    """Create and return an asyncpg connection pool."""
    return await asyncpg.create_pool(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 5432)),
        database=os.getenv("DB_NAME", "prices"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASS"),
    )
```

### `scraper/main.py`

Stub only — full implementation in prompt 02.

```python
"""
CLI entrypoint for the scraper.
Full implementation: see prompts/02_scraper_core.md

Usage:
    python scraper/main.py db init
    python scraper/main.py discover --supplier maxiconsumo
    python scraper/main.py scrape [--supplier <id>]
    python scraper/main.py export latest
    python scraper/main.py export comparison
    python scraper/main.py export history --sku <sku> --supplier <id>
"""

import argparse
import asyncio
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)


def main():
    parser = argparse.ArgumentParser(prog="scraper")
    subparsers = parser.add_subparsers(dest="command")

    # db subcommand
    db_parser = subparsers.add_parser("db")
    db_sub = db_parser.add_subparsers(dest="db_command")
    db_sub.add_parser("init")

    # discover subcommand
    discover_parser = subparsers.add_parser("discover")
    discover_parser.add_argument("--supplier", required=True)

    # scrape subcommand
    scrape_parser = subparsers.add_parser("scrape")
    scrape_parser.add_argument("--supplier", required=False)

    # export subcommand
    export_parser = subparsers.add_parser("export")
    export_sub = export_parser.add_subparsers(dest="export_command")
    export_sub.add_parser("latest")
    export_sub.add_parser("comparison")
    history_parser = export_sub.add_parser("history")
    history_parser.add_argument("--sku", required=True)
    history_parser.add_argument("--supplier", required=True)

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    print(f"[stub] command received: {args}")
    print("Full implementation coming in prompt 02.")


if __name__ == "__main__":
    main()
```

### `scraper/scraper.py` and `scraper/export.py`

Create as empty stubs with a module docstring only. Full implementation in prompt 02.

### `analysis/maxiconsumo/NOTES.md`

```markdown
# Maxiconsumo — Recon Notes

## Basic info
- URL: https://maxiconsumo.com/sucursal_moreno/
- Platform: Magento 2
- Login required: yes (supplier-tier "categorizado" pricing)

## Scraping approach
- httpx + BeautifulSoup (static HTML, no JS rendering needed)
- JS disabled warning on page is cosmetic — full HTML is served without JS

## Auth flow
- GET login page → extract input[name="form_key"] value
- POST to /customer/account/loginPost/ with form_key + credentials
- Success: redirects to account page
- Failure: redirects back to login page (no error thrown — must check URL)

## Price format
- Argentine: $1.234,56 (dot = thousands, comma = decimal)
- Two prices per product: bulk (closed box) and unit

## Category structure
- All leaf categories visible in nav menu on homepage
- Leaf URL pattern: /sucursal_moreno/<cat>/<subcat>/<leaf>.html (4+ slashes)
- Pagination: ?product_list_limit=96&p=N

## CSS selectors (to be verified)
- product_item:  .product-item
- name:          .product-item-link
- sku:           .product-item-sku  ← VERIFY THIS
- prices:        .price-box .price  → [0]=bulk, [1]=unit
- stock:         .stock
- next_page:     a[title="Siguiente"]

## Notes
- Branch: sucursal_moreno — prices are branch-specific
- Price tiers: guest / consumidor final / categorizado
  We scrape "categorizado" (authenticated supplier tier)
```

### `docs/decisions.md`

```markdown
# Architecture Decisions

| Date | Decision | Reason |
|---|---|---|
| — | PostgreSQL over SQLite | multi-user, production-ready |
| — | asyncpg, no SQLAlchemy | minimal deps, explicit SQL |
| — | JWT auth, no Supabase/Auth0 | no vendor dependency |
| — | admin/viewer roles for MVP | extend later if needed |
| — | shared data model | pricing data is public |
| — | price-change deduplication before insert | keeps snapshots table small |
| — | one file per supplier in suppliers/ | isolation without duplication |
| — | analysis/ folder separate from production | recon work is throwaway |
| — | Python over Rust | bottleneck is network I/O not CPU |
```

---

## Verification

After all files are created, run:

```bash
python scraper/main.py --help
python scraper/main.py db init
```

`--help` should print the usage. `db init` should print the stub message.

If either fails, fix before closing this session.

---

## End of session

Update root `CLAUDE.md` Status:
- Mark "Project setup" as done
- Mark "Scraper core engine" as in progress
