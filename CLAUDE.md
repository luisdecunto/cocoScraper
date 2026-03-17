# CLAUDE.md — cocoScraper

Read this file at the start of every session. Update the **Status** section before closing.

---

## What this project is

A multi-supplier price scraping and comparison system for a wholesale purchasing client.
Scrapes product prices from supplier websites, stores history in PostgreSQL, and produces
cross-supplier price comparison reports.

Built to be sold as a SaaS product. Multi-user, role-based access, multiple suppliers.

---

## Project structure

```
cocoScraper/
├── CLAUDE.md                        # this file
├── .env                             # never commit
├── .env.example                     # commit this
├── .gitignore
├── requirements.txt                 # core deps
├── requirements_playwright.txt      # only if a supplier needs JS rendering
│
├── scraper/                         # unified scraping engine
│   ├── CLAUDE.md
│   ├── main.py                      # CLI entrypoint
│   ├── db.py                        # PostgreSQL: pool, schema, upsert, run_log
│   ├── scraper.py                   # orchestrator: loops suppliers/categories/pages
│   ├── export.py                    # CSV + XLSX exports
│   ├── config.py                    # supplier registry
│   └── suppliers/
│       ├── base.py                  # abstract base class — the supplier contract
│       ├── maxiconsumo.py           # supplier implementation
│       └── <new_supplier>.py        # one file per supplier
│
├── analysis/                        # recon workspace — never imported by production code
│   └── maxiconsumo/
│       ├── NOTES.md                 # platform, selectors, quirks, approach decision
│       ├── selector_debug.py        # throwaway debug scripts
│       └── sample_html/             # saved HTML for offline testing
│
├── api/                             # FastAPI multi-user layer (planned)
│   └── CLAUDE.md
│
├── prompts/                         # Claude Code task prompts — one per major task
│   ├── 01_project_setup.md
│   ├── 02_scraper_core.md
│   ├── 03_supplier_maxiconsumo.md
│   └── 04_new_supplier_template.md
│
└── docs/
    ├── 01_update_strategy.md          # How to handle product updates, dedup, re-scraping
    ├── 02_client_delivery.md          # SaaS architecture, FastAPI API, multi-tenancy roadmap
    ├── 03_unification_strategy.md     # Cross-supplier matching, unified taxonomy, 7-10 week plan
    ├── adding_a_supplier.md
    ├── decisions.md
    ├── categories.md
    └── flujo_de_datos.md
```

---

## Tech stack

| Layer | Library | Notes |
|---|---|---|
| HTTP (static sites) | `httpx` + `asyncio` | default for all suppliers |
| HTTP (JS-rendered) | `playwright` | only install if a supplier needs it |
| HTML parsing | `beautifulsoup4` + `lxml` | always use lxml, never html.parser |
| JSON APIs | `httpx` directly | no HTML parsing needed |
| Database | `asyncpg` + PostgreSQL | |
| API layer | `fastapi` + `uvicorn` | planned |
| Auth | `python-jose` + `passlib[bcrypt]` | JWT |
| Exports | `csv` (stdlib) + `openpyxl` | |
| Scheduling | cron (prod) / `APScheduler` (dev) | |
| Secrets | `python-dotenv` | |

**Why Python and not Rust:** bottleneck is network I/O, not CPU.
Python async saturates that bottleneck identically to Rust at our scale.

---

## Database schema

```sql
CREATE TABLE products (
    sku                 TEXT        NOT NULL,
    supplier            TEXT        NOT NULL,
    name                TEXT,
    url                 TEXT,
    category            TEXT,
    units_per_package   INT,        -- units per closed box (Nini only; NULL for other suppliers)
    packs_per_pallet    INT,        -- boxes per pallet (Nini only; NULL for other suppliers)
    updated_at          TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (sku, supplier)
);

CREATE TABLE price_snapshots (
    id          BIGSERIAL   PRIMARY KEY,
    sku         TEXT        NOT NULL,
    supplier    TEXT        NOT NULL,
    scraped_at  DATE        NOT NULL DEFAULT CURRENT_DATE,
    price_unit  NUMERIC(12,2),
    price_bulk  NUMERIC(12,2),
    stock       TEXT,
    UNIQUE (sku, supplier, scraped_at),
    FOREIGN KEY (sku, supplier) REFERENCES products(sku, supplier)
);

CREATE TABLE run_log (
    id                BIGSERIAL    PRIMARY KEY,
    supplier          TEXT         NOT NULL,
    started_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    finished_at       TIMESTAMPTZ,
    status            TEXT         NOT NULL DEFAULT 'running',
    categories_total  INT          DEFAULT 0,
    categories_done   INT          DEFAULT 0,
    products_scraped  INT          DEFAULT 0,
    snapshots_written INT          DEFAULT 0,
    error_message     TEXT
);

CREATE TABLE users (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    email         TEXT        UNIQUE NOT NULL,
    password_hash TEXT        NOT NULL,
    role          TEXT        NOT NULL DEFAULT 'viewer',
    is_active     BOOLEAN     DEFAULT TRUE,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);
```

**Price deduplication rule:** skip snapshot insert if price_unit and price_bulk are
identical to the last recorded row for that (sku, supplier).

**Alert rule:** if a run finishes with `snapshots_written = 0`, log at ERROR level.

---

## Supplier contract

Every supplier extends `BaseSupplier` in `scraper/suppliers/base.py`.

```python
class BaseSupplier(ABC):
    async def login(self, client) -> None: ...
    async def discover_categories(self, client) -> list[str]: ...
    async def scrape_category(self, client, url: str, sem) -> list[dict]: ...
    def parse_price(self, raw: str) -> float | None: ...
```

`scrape_category` returns dicts with keys:
`sku`, `name`, `url`, `category`, `price_unit`, `price_bulk`, `stock`

**Each supplier can use completely different internals** — httpx, Playwright, direct
JSON API, different selectors, different price formats. The contract only defines the
interface. Implementation is unconstrained.

---

## Concurrency rules

- `asyncio.Semaphore(N)` per supplier run. Default N=10. Start at 5 for new suppliers.
- `await asyncio.sleep(0.3)` inside the semaphore on every page fetch.
- `await asyncio.sleep(2)` between categories.
- Semaphore is created in `scraper.py` and passed into `scrape_category`.

---

## Coding conventions

- **Async everywhere.** No blocking calls in async context.
- **`logging` not `print`.** Log to stdout AND `logs/scraper.log`.
- **No hardcoded credentials.** `os.getenv()` only. Loaded via `python-dotenv`.
- **Never commit `.env`.** Only `.env.example` is committed.
- **Type hints on all signatures. Docstrings on all public classes and functions.**
- **Fail at task level, not run level.** Broken page → warning + continue.
  Broken supplier → error + continue to next supplier. Never abort the full run.
- **`argparse` for all CLIs.**
- **`analysis/` is throwaway.** Never imported by production code.

---

## Environment variables

```
DB_HOST=localhost
DB_PORT=5432
DB_NAME=prices
DB_USER=scraper
DB_PASS=

MAXICONSUMO_USER=
MAXICONSUMO_PASS=

JWT_SECRET=
JWT_ALGORITHM=HS256
JWT_EXPIRE_MINUTES=480
```

---

## How to run

Always run from `cocoScraper/` root.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python -m scraper.main db init
python -m scraper.main discover --supplier maxiconsumo
python -m scraper.main scrape --supplier maxiconsumo
python -m scraper.main scrape
python -m scraper.main export latest
python -m scraper.main export comparison
python -m scraper.main export history --sku 328 --supplier maxiconsumo

streamlit run dashboard/app.py
```

---

## User roles (API — planned)

| Role | Access |
|---|---|
| `admin` | manage users, configure suppliers, trigger scrapes, all exports |
| `viewer` | read-only: query prices, download exports |

---

## Status

### Done
- [x] Project setup
- [x] Scraper core engine
- [x] Supplier: Maxiconsumo (code complete — 8,918 products scraped)
- [x] Exploration dashboard (Streamlit)
- [x] Supplier: Santa Maria (code complete, selectors verified, 1,782 products)
- [x] Postprocess: Santa Maria (scraper/postprocess/santamaria.py — 100% brand, 99.6% type, category map)
- [x] Supplier: Luvik (code complete, Shopify API discovery via sitemap.xml, 5,194 products; SSL verify fix applied)
- [x] Supplier: Vital (code complete, VTEX IS API, 4,858 products)
- [x] Supplier: Nini (code complete, custom API, 7,717 products)
- [x] Postprocess: Nini (scraper/postprocess/nini.py — 100% brand, 100% type, 99.9% size, category normalization)
- [x] Postprocess: Luvik (scraper/postprocess/luvik.py — 100% brand, 99.6% size, category normalization)
- [x] Postprocess: Vital (scraper/postprocess/vital.py — 100.0% brand lookup, OCR artifact cleanup, category normalization)
- [x] Unify: cross-supplier matcher (scraper/postprocess/unify.py — initial 774 matches from 16k products)
- [x] Price history schema (price_history table with first_seen/last_seen periods, gap-and-islands migration)
- [x] Dashboard: stock filter (Hide out of stock + Disponibilidad crítica), show products without prices
- [x] Export: CSV + XLSX comparison reports (scraper/export.py)
- [x] Dashboard redesign: DanSpil-inspired UI, advanced filtering, professional sidebar navigation
- [x] Strategic plans: Update strategy, client delivery, unification strategy (3 docs in docs/)
- [x] i18n: English / Español / Conurbano language switcher (dashboard/i18n.py, sidebar selectbox)
- [x] Mobile sidebar: slide-in nav, persistent hamburger button injected into document.body (CSS transform workaround)
- [x] Streamlit Community Cloud deployment (dashboard/requirements.txt, dashboard/db/connection.py, Neon PostgreSQL)
- [x] Data migration: 28k products + 23k snapshots + 20k price_history rows migrated to Neon

### In Progress

### Planned
- [ ] Phase 0: Automated scraping via GitHub Actions (blocked on scraper optimization — see docs/02_client_delivery.md)
- [ ] Scraper optimization: speed, reliability, error recovery across all 5 suppliers
- [ ] DATABASE_URL support in scraper/db.py (needed for GitHub Actions)
- [ ] Phase 1: Postprocessing optimization (Santa Maria brand extraction, size standardization)
- [ ] Phase 2: Unified taxonomy (master brand/type/category lists, 90%+ canonical matching)
- [ ] Phase 3: API foundation (FastAPI + JWT auth)
- [ ] Phase 4: Multi-tenancy (tenant_id, RLS, user management)
- [ ] Phase 5: Client features (shopping lists, price alerts)
- [ ] Phase 6: Production hosting (self-hosted or serverless deployment)

---

## Decisions log

| Date | Decision | Reason |
|---|---|---|
| — | PostgreSQL | multi-user, production-ready |
| — | asyncpg, no SQLAlchemy | minimal deps, explicit SQL |
| — | JWT, no Supabase/Auth0 | no vendor dependency |
| — | admin/viewer only for MVP | extend later if needed |
| — | shared data model | pricing data is public |
| — | price-change deduplication | keeps snapshots table small |
| — | one file per supplier | isolation without duplication |
| — | analysis/ separate from production | recon is throwaway |
