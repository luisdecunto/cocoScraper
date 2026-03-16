# Migration Plan: Streamlit → Next.js + FastAPI

**Goal**: Replace the Streamlit dashboard with a proper React/Next.js frontend + FastAPI backend,
deployed on Vercel (frontend) + Railway (backend + PostgreSQL).

---

## What Changes vs. What Stays

### Stays (don't touch)
- `scraper/` — all scraping engine code
- `scraper/suppliers/` — all 5 supplier implementations
- `scraper/postprocess/` — all 5 postprocessors
- `scraper/db.py` — database schema and upsert logic
- PostgreSQL schema — products, price_history, run_log, users
- `.env` structure — same env vars, different target host
- All data files — `*_brands.txt`, `*_product_types.txt`, etc.
- `docs/` — strategic plans

### Changes
- `dashboard/` — replaced by `web/` (Next.js app)
- Database host — local PostgreSQL → Railway managed PostgreSQL
- API layer — direct psycopg2 calls → FastAPI REST API
- Hosting — local process → Vercel (frontend) + Railway (backend)

---

## Target Architecture

```
Vercel (free)                Railway (free tier)
┌──────────────────┐         ┌─────────────────────────────────┐
│  Next.js 14      │  HTTPS  │  FastAPI (Python)               │
│  (App Router)    │◄───────►│  /api/products                  │
│  TypeScript      │         │  /api/comparison                │
│  Tailwind CSS    │         │  /api/history                   │
│                  │         │  /api/runs                      │
└──────────────────┘         │                                 │
                             │  PostgreSQL (managed)           │
                             │  (migrated from local)          │
                             │                                 │
                             │  Scraper (Python cron)          │
                             │  runs on Railway schedule       │
                             └─────────────────────────────────┘
```

---

## Tech Stack

| Layer | Technology | Hosting | Free tier |
|-------|-----------|---------|-----------|
| Frontend | Next.js 14 (App Router) + TypeScript | Vercel | Yes |
| Styling | Tailwind CSS | — | — |
| UI components | shadcn/ui (headless, unstyled base) | — | — |
| Tables | TanStack Table v8 | — | — |
| Charts | Recharts | — | — |
| Backend | FastAPI + uvicorn | Railway | Yes (500h/month) |
| Database | PostgreSQL 15 | Railway | Yes (1GB) |
| Auth (Phase 2) | JWT via FastAPI | Railway | — |
| Scraper | Python cron via Railway | Railway | Yes |

**Why shadcn/ui?** Headless components you own (not a library), styled with Tailwind, same design
language as DanSpil. You can port DanSpil patterns directly.

---

## Pages to Build (mirrors current Streamlit pages)

### 1. Dashboard (Browse Products)
- Full product table with all columns
- Filters: supplier multiselect, text search, stock toggle, department, price range
- Column visibility toggle
- Export CSV button
- Product count badge
- Mirrors: `render_browse_page()` in `dashboard/app.py`

### 2. Comparison (Cross-supplier matrix)
- Pivot table: canonical products × suppliers
- Cheapest price highlighted green per row
- Department filter + text search
- Sorted by diff_pct descending
- Export CSV
- Mirrors: `render_comparison_page()` in `dashboard/app.py`

### 3. History (Price history)
- Search products
- Multiselect picker
- Line chart (Recharts, step-interpolated)
- Raw intervals table
- Export CSV
- Mirrors: `render_history_page()` in `dashboard/app.py`

### 4. Logs (Scrape run health)
- Run log table with status coloring
- Metric row: success rate, zero-snapshot count, last activity
- Warning banner for zero-snapshot runs
- Mirrors: `render_logs_page()` in `dashboard/app.py`

---

## FastAPI Endpoints (MVP)

```
GET  /api/products            ?supplier=&dept=&search=&stock=&page=&limit=
GET  /api/comparison          ?dept=&search=
GET  /api/history/products    ?search=&limit=200
GET  /api/history/intervals   ?product_ids[]=&...
GET  /api/runs                (last 20 run_log rows)
GET  /api/snapshot            (products count, supplier count, last updated)
```

All read-only for Phase 1. Auth added in Phase 2.

---

## Phase 1: Demo (target: deployable for client)

### Step 1 — Railway setup
1. Create Railway project
2. Add PostgreSQL plugin (managed DB)
3. Get connection string: `postgresql://user:pass@host:port/dbname`

### Step 2 — Database migration
1. `pg_dump` local DB → SQL file
2. `psql` into Railway PostgreSQL → restore
3. Verify row counts match

### Step 3 — FastAPI backend
```
api/
├── main.py          # FastAPI app, CORS, routes
├── db.py            # asyncpg pool from env DATABASE_URL
├── routers/
│   ├── products.py  # GET /api/products
│   ├── comparison.py
│   ├── history.py
│   ├── runs.py
│   └── snapshot.py
├── models.py        # Pydantic response models
└── requirements.txt
```

Deploy to Railway as a Python web service.

### Step 4 — Next.js frontend
```
web/
├── app/
│   ├── layout.tsx           # Root layout with sidebar
│   ├── page.tsx             # Redirect to /dashboard
│   ├── dashboard/page.tsx   # Browse products
│   ├── comparison/page.tsx  # Cross-supplier matrix
│   ├── history/page.tsx     # Price history
│   └── logs/page.tsx        # Run log
├── components/
│   ├── layout/
│   │   ├── Sidebar.tsx      # DanSpil-inspired sidebar
│   │   └── PageHeader.tsx
│   ├── table/
│   │   ├── DataTable.tsx    # TanStack Table wrapper
│   │   └── ColumnFilter.tsx
│   ├── filters/
│   │   ├── FilterBar.tsx
│   │   ├── SupplierFilter.tsx
│   │   └── StockToggle.tsx
│   └── charts/
│       └── PriceHistoryChart.tsx
├── lib/
│   ├── api.ts               # fetch wrappers for FastAPI
│   └── types.ts             # TypeScript types matching API responses
└── package.json
```

Deploy to Vercel. Set `NEXT_PUBLIC_API_URL=https://your-railway-app.up.railway.app`.

### Step 5 — Connect and test
- Vercel env var: `NEXT_PUBLIC_API_URL`
- Railway env var: `DATABASE_URL` (auto-injected by Railway)
- Test all 4 pages end-to-end

---

## Phase 2: Auth (after demo feedback)

- Add `POST /api/auth/login` → returns JWT
- Add `Authorization: Bearer` header validation to all endpoints
- Add login page to Next.js
- Add protected routes middleware
- Create viewer credentials for client

---

## Phase 3: SaaS features (after Phase 2)

- Multi-tenancy (tenant_id column, RLS)
- Shopping lists
- Price alerts
- User management

---

## UI/UX Reference

Copy patterns from `C:\Users\luisd\Documents\Luis\DanSpil\apps\web\src`:
- **Sidebar**: `components/Sidebar.jsx` + `styles/layout.css`
- **Variables**: `styles/variables.css` (color tokens, spacing, typography)
- **Typography**: `styles/typography.css`
- **Components**: `styles/components.css`

Key tokens to port to Tailwind config:
```js
// tailwind.config.ts
colors: {
  bg: '#f5f5f3',
  surface: '#ffffff',
  border: '#e5e5e5',
  text: { primary: '#1c1c1c', secondary: '#6f6f6f' },
  accent: '#b3873f',
  success: '#1f7a3c',
  error: '#b4232f',
}
```

---

## Environment Variables

### Railway (FastAPI + PostgreSQL)
```
DATABASE_URL=postgresql://...   # auto-injected by Railway
```

### Vercel (Next.js)
```
NEXT_PUBLIC_API_URL=https://<railway-app>.up.railway.app
```

---

## Effort Estimate

| Step | Effort | Who |
|------|--------|-----|
| Railway + DB migration | 1–2h | Manual (user) |
| FastAPI backend | 1 day | Agent |
| Next.js layout + sidebar | 2–4h | Agent |
| Dashboard page (table + filters) | 4–6h | Agent |
| Comparison page | 2–3h | Agent |
| History page (chart) | 2–3h | Agent |
| Logs page | 1h | Agent |
| Deploy + wire together | 1–2h | Manual (user) + Agent |

**Total**: 2–3 days of agent work, ~3–4h of manual steps.
