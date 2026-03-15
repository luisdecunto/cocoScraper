# cocoScraper

A multi-supplier price scraping and comparison system for wholesale purchasing. Scrapes product prices from supplier websites, stores historical data in PostgreSQL, and produces cross-supplier price comparison reports.

Built as a SaaS product with multi-user, role-based access control.

---

## Features

- **Multi-supplier scraping** — automatically scrape product prices from multiple suppliers concurrently
- **Price history tracking** — store and compare prices over time with automatic deduplication
- **Cross-supplier matching** — unified product matching across different supplier catalogs
- **CSV & XLSX exports** — generate comparison and historical price reports
- **Streamlit dashboard** — interactive data exploration and visualization
- **Role-based access** — admin and viewer roles for multi-user access (planned)
- **PostgreSQL backend** — production-ready database with full schema management

---

## Tech Stack

| Layer | Library | Notes |
|---|---|---|
| **HTTP (static sites)** | `httpx` + `asyncio` | default for all suppliers |
| **HTTP (JS-rendered)** | `playwright` | only if a supplier needs it |
| **HTML parsing** | `beautifulsoup4` + `lxml` | always lxml, never html.parser |
| **JSON APIs** | `httpx` directly | no HTML parsing needed |
| **Database** | `asyncpg` + PostgreSQL | async connection pool |
| **API layer** | `fastapi` + `uvicorn` | multi-user API (planned) |
| **Auth** | `python-jose` + `passlib[bcrypt]` | JWT tokens |
| **Exports** | `csv` (stdlib) + `openpyxl` | CSV and XLSX formats |
| **Scheduling** | cron (prod) / `APScheduler` (dev) | automated scraping |
| **Dashboard** | `streamlit` | data exploration UI |

---

## Project Structure

```
cocoScraper/
├── README.md                        # this file
├── CLAUDE.md                        # development notes & decisions
├── SETUP.md                         # detailed setup instructions
├── requirements.txt                 # core dependencies
├── requirements_playwright.txt       # optional: JS-rendered suppliers
│
├── scraper/                         # unified scraping engine
│   ├── main.py                      # CLI entrypoint
│   ├── db.py                        # PostgreSQL connection pool & schema
│   ├── scraper.py                   # orchestrator: loops suppliers/categories/pages
│   ├── config.py                    # supplier registry & configuration
│   ├── export.py                    # CSV + XLSX export functions
│   │
│   ├── suppliers/                   # supplier implementations
│   │   ├── base.py                  # abstract BaseSupplier class
│   │   ├── maxiconsumo.py           # Maxiconsumo supplier
│   │   ├── santamaria.py            # Santa María supplier
│   │   ├── luvik.py                 # Luvik (Shopify) supplier
│   │   ├── vital.py                 # Vital (VTEX) supplier
│   │   └── nini.py                  # Nini supplier
│   │
│   └── postprocess/                 # data normalization
│       ├── maxiconsumo.py           # brand, type, size normalization
│       ├── santamaria.py            # category normalization
│       ├── luvik.py                 # brand, type, size, category normalization
│       ├── vital.py                 # OCR cleanup, brand, type, size normalization
│       ├── nini.py                  # brand, type, size, category normalization
│       ├── unify.py                 # cross-supplier product matching
│       └── data/                    # lookup files (brands, product types, etc.)
│
├── analysis/                        # exploration & debugging workspace
│   ├── maxiconsumo/
│   ├── santamaria/
│   ├── luvik/
│   ├── vital/
│   └── nini/
│
├── api/                             # FastAPI multi-user layer (planned)
│   └── CLAUDE.md
│
├── dashboard/                       # Streamlit exploration dashboard
│   └── app.py
│
├── exports/                         # output files
│   ├── latest_prices.csv
│   ├── comparison.csv
│   ├── unified_prices.csv
│   └── <supplier>_products.txt
│
├── docs/
│   ├── adding_a_supplier.md         # how to add a new supplier
│   └── decisions.md                 # architecture & design decisions
│
└── logs/
    └── scraper.log                  # application logs
```

---

## Quick Start

### 1. Prerequisites

- Python 3.9+
- PostgreSQL 12+
- Git

### 2. Setup

Clone and install:
```bash
git clone https://github.com/luisdecunto/cocoScraper.git
cd cocoScraper
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure environment

Copy the example and fill in your credentials:
```bash
cp .env.example .env
```

Edit `.env` with:
```
DB_HOST=localhost
DB_PORT=5432
DB_NAME=prices
DB_USER=scraper
DB_PASS=your_password

MAXICONSUMO_USER=your_username
MAXICONSUMO_PASS=your_password

# ... other supplier credentials
```

**Never commit `.env`** — it's in `.gitignore`.

### 4. Initialize database

```bash
python -m scraper.main db init
```

Creates tables: `products`, `price_snapshots`, `run_log`, `users`.

### 5. Run commands

```bash
# Discover categories for a supplier
python -m scraper.main discover --supplier maxiconsumo

# Scrape one supplier
python -m scraper.main scrape --supplier maxiconsumo

# Scrape all suppliers
python -m scraper.main scrape

# Export latest prices
python -m scraper.main export latest

# Export price comparisons
python -m scraper.main export comparison

# View specific product history
python -m scraper.main export history --sku 328 --supplier maxiconsumo
```

### 6. Run dashboard

```bash
streamlit run dashboard/app.py
```

Opens at `http://localhost:8501`

---

## Supported Suppliers

| Supplier | Platform | Status | Products |
|---|---|---|---|
| **Maxiconsumo** | Custom e-commerce | ✅ Complete | 8,918 |
| **Santa María** | osCommerce | ✅ Complete | 1,782 |
| **Luvik** | Shopify | ✅ Complete | 4,380 |
| **Vital** | VTEX | ✅ Complete | 4,858 |
| **Nini** | ASP.NET + Node.js | ✅ Complete | 7,717 |

Each supplier has:
- Independent HTML/API parsing logic
- Automatic login & session handling
- Category discovery
- Price extraction & normalization
- Postprocessing (brand, type, size, category extraction)

---

## Database Schema

### `products`
Stores product metadata. Primary key: `(sku, supplier)`.

```sql
CREATE TABLE products (
    sku                 TEXT        NOT NULL,
    supplier            TEXT        NOT NULL,
    name                TEXT,
    url                 TEXT,
    category            TEXT,
    units_per_package   INT,        -- Nini only
    packs_per_pallet    INT,        -- Nini only
    updated_at          TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (sku, supplier)
);
```

### `price_snapshots`
Historical price data. Unique constraint: `(sku, supplier, scraped_at)`.

```sql
CREATE TABLE price_snapshots (
    id          BIGSERIAL   PRIMARY KEY,
    sku         TEXT        NOT NULL,
    supplier    TEXT        NOT NULL,
    scraped_at  DATE        NOT NULL DEFAULT CURRENT_DATE,
    price_unit  NUMERIC(12,2),       -- per-unit price
    price_bulk  NUMERIC(12,2),       -- bulk/box price
    stock       TEXT,                 -- availability
    UNIQUE (sku, supplier, scraped_at),
    FOREIGN KEY (sku, supplier) REFERENCES products(sku, supplier)
);
```

### `run_log`
Scraping execution history.

```sql
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
```

### `users`
Multi-user access control (planned API).

```sql
CREATE TABLE users (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    email         TEXT        UNIQUE NOT NULL,
    password_hash TEXT        NOT NULL,
    role          TEXT        NOT NULL DEFAULT 'viewer',
    is_active     BOOLEAN     DEFAULT TRUE,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);
```

---

## Key Design Decisions

- **PostgreSQL over NoSQL** — structured data requires ACID guarantees for price accuracy
- **asyncpg over SQLAlchemy** — minimal dependencies, explicit SQL for control
- **One file per supplier** — isolation without code duplication
- **JWT auth, no vendor lock-in** — portable, self-hosted credentials
- **Price deduplication** — skip snapshots if price unchanged from prior row
- **Fail at task level, not run level** — broken page continues, broken supplier continues

See [docs/decisions.md](docs/decisions.md) for full rationale.

---

## Development

### Adding a new supplier

See [docs/adding_a_supplier.md](docs/adding_a_supplier.md) for detailed guide.

Quick outline:
1. Create `scraper/suppliers/your_supplier.py` extending `BaseSupplier`
2. Implement: `login()`, `discover_categories()`, `scrape_category()`, `parse_price()`
3. Register in `scraper/config.py`
4. Add postprocessing in `scraper/postprocess/your_supplier.py`
5. Test with `python -m scraper.main discover --supplier your_supplier`

### Coding conventions

- **Async everywhere** — use `async`/`await`, never block
- **Type hints on all signatures** — enables IDE autocomplete
- **Docstrings on public classes/functions**
- **Logging, not print** — use `logging` module
- **No hardcoded secrets** — use `os.getenv()` only
- **`.env` never committed** — only `.env.example`

### Running tests

```bash
PYTHONPATH=. python -m pytest tests/
```

---

## Troubleshooting

**Database connection fails:**
```bash
# Check PostgreSQL is running
# Check .env has correct DB_HOST, DB_PORT, DB_USER, DB_PASS
# Verify database exists: createdb prices
```

**Supplier login fails:**
- Check credentials in `.env` are correct
- Visit supplier site manually to confirm you can log in
- Check for rate limiting or IP bans

**Missing products:**
- Verify supplier website structure hasn't changed
- Check selectors in `analysis/<supplier>/NOTES.md`
- Run analysis scripts in `analysis/<supplier>/` to debug

---

## Contributing

1. Fork the repo
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Make changes, test thoroughly
4. Commit with clear messages
5. Push and open a pull request

See [CLAUDE.md](CLAUDE.md) for project context and status.

---

## License

Proprietary — not open source.

---

## Support

For issues, questions, or contributions: create an issue on GitHub or contact the team.

---

## Status

- ✅ Core scraper engine
- ✅ 5 suppliers (Maxiconsumo, Santa María, Luvik, Vital, Nini)
- ✅ Price history & deduplication
- ✅ CSV & XLSX exports
- ✅ Cross-supplier matching
- ✅ Streamlit dashboard
- 🔄 FastAPI multi-user layer (in progress)
- 🔄 User management & auth (planned)

Last updated: March 15, 2026
