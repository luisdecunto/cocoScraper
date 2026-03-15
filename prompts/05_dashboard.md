# Prompt 05 — Exploration Dashboard (Streamlit)

> Run this after prompt 03 (at least one supplier scraping successfully).
> Goal: a simple local dashboard to explore scraped data.
> This is an exploration tool, not a production frontend.
> Keep it simple — the value is in seeing the data, not in polish.

---

## Context

Read CLAUDE.md before starting.
Data is in PostgreSQL. Connection details from `.env` (already loaded via `python-dotenv`).

---

## Setup

Add to `requirements.txt`:
```
streamlit==1.35.0
pandas==2.2.2
plotly==5.22.0
```

Install:
```bash
pip install streamlit pandas plotly
```

Create file: `dashboard/app.py`

Add `dashboard/` to the project structure in `scraper/CLAUDE.md`.

Run with:
```bash
streamlit run dashboard/app.py
```

---

## What to build

Four tabs. Keep each tab simple — a few filters and a table or chart.

### Tab 1 — Latest Prices

Shows the most recent scraped price for every product.

- Filter by: supplier (multiselect), category (multiselect), search by name (text input)
- Table columns: name, sku, category, supplier, price_unit, price_bulk, stock, scraped_at
- Sort by price_unit ascending by default
- Show row count above the table

Query:
```sql
SELECT p.name, p.sku, p.category, p.supplier,
       s.price_unit, s.price_bulk, s.stock, s.scraped_at
FROM products p
JOIN price_snapshots s ON s.sku = p.sku AND s.supplier = p.supplier
WHERE s.scraped_at = (
    SELECT MAX(scraped_at) FROM price_snapshots
    WHERE sku = p.sku AND supplier = p.supplier
)
ORDER BY s.price_unit ASC NULLS LAST;
```

---

### Tab 2 — Price Comparison

Cross-supplier view. Only useful once there are 2+ suppliers.
Until then show a message: "Add more suppliers to enable comparison."

- Filter by category
- Table: one row per product, one column per supplier showing price_unit
- Highlight cheapest price per row in green (use pandas Styler)
- Show % difference between cheapest and most expensive

---

### Tab 3 — Price History

Track how a product's price changed over time.

- Search box: type product name or SKU
- Dropdown: select from matching products
- Line chart (Plotly): price_unit over time, one line per supplier
- Table below chart: raw snapshot data

Query:
```sql
SELECT s.scraped_at, s.price_unit, s.price_bulk, s.supplier
FROM price_snapshots s
WHERE s.sku = %s
ORDER BY s.scraped_at ASC;
```

---

### Tab 4 — Run Log

Shows scrape run history so you can see if everything is working.

- Table: supplier, started_at, finished_at, status, products_scraped, snapshots_written, error_message
- Last 20 runs, newest first
- Color status column: green for success, red for failed, yellow for running
- If any run has snapshots_written = 0 and status = success: show a warning banner

---

## Database connection

Use a cached connection via `st.cache_resource`:

```python
import streamlit as st
import asyncpg
import asyncio
import pandas as pd
import os
from dotenv import load_dotenv

load_dotenv()

@st.cache_resource
def get_connection():
    """Synchronous wrapper — Streamlit doesn't support async natively."""
    async def _connect():
        return await asyncpg.create_pool(
            host=os.getenv("DB_HOST", "localhost"),
            port=int(os.getenv("DB_PORT", 5432)),
            database=os.getenv("DB_NAME", "prices"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASS"),
        )
    return asyncio.get_event_loop().run_until_complete(_connect())


def query(sql: str, *args) -> pd.DataFrame:
    """Run a query and return a DataFrame."""
    pool = get_connection()
    async def _run():
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *args)
            return rows
    rows = asyncio.get_event_loop().run_until_complete(_run())
    return pd.DataFrame([dict(r) for r in rows])
```

---

## App structure

```python
st.set_page_config(page_title="cocoScraper", layout="wide")
st.title("🥥 cocoScraper — Price Explorer")

tab1, tab2, tab3, tab4 = st.tabs([
    "Latest Prices", "Comparison", "Price History", "Run Log"
])

with tab1:
    # ... latest prices implementation

with tab2:
    # ... comparison implementation

with tab3:
    # ... price history implementation

with tab4:
    # ... run log implementation
```

---

## Verification

```bash
streamlit run dashboard/app.py
```

Should open in browser at `http://localhost:8501`.
All four tabs should load without errors, even if tables are empty.

---

## End of session

Update root `CLAUDE.md` Status:
- Mark "Exploration dashboard" as done
- Add run command to the "How to run" section:
  `streamlit run dashboard/app.py`
