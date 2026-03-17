# Client Delivery & Sharing Strategy

How to host cocoScraper as a SaaS product for wholesale purchasing clients.

---

## Vision

Transform cocoScraper from a single-user tool into a **multi-tenant, role-based SaaS platform** where clients can:
- Log in securely (per-client auth)
- View their own product catalog and price comparisons
- Create shopping lists
- Export data for procurement workflows
- Receive alerts on price changes

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    Clients (Web + Mobile)                        │
│                    FastAPI + Streamlit                           │
└──────────────────────────────────┬──────────────────────────────┘
                                   │
                    ┌──────────────┴──────────────┐
                    │                             │
         ┌──────────▼─────────┐        ┌────────▼─────────┐
         │  FastAPI REST API  │        │  Streamlit Dashboard │
         │   (Backend)        │        │   (Analysis UI)      │
         └──────────┬─────────┘        └────────┬─────────┘
                    │                             │
                    └──────────────┬──────────────┘
                                   │
                    ┌──────────────▼──────────────┐
                    │    PostgreSQL (Multi-DB)    │
                    │  One schema per client OR   │
                    │  Shared schema + row-level  │
                    │  security policies (RLS)    │
                    └─────────────────────────────┘
```

---

## Phase 1: Backend API (FastAPI)

### Goal
Replace manual exports with a secure REST API. Clients can query data programmatically.

### Endpoints (MVP)

```
POST   /api/auth/login              — JWT auth (email + password)
POST   /api/auth/logout             — Clear token
GET    /api/auth/me                 — Current user info
GET    /api/products                — List products (filtered)
GET    /api/products/:id            — Product detail + history
GET    /api/comparison              — Cross-supplier comparison matrix
GET    /api/price-history           — Historical intervals for product(s)
GET    /api/exports/csv             — Export filtered results as CSV
POST   /api/shopping-lists          — Create/update shopping list
GET    /api/shopping-lists/:id      — Retrieve list with totals
POST   /api/alerts/subscribe        — Alert on price movement
GET    /api/runs                    — Scrape run logs (admin only)
```

### Authentication
- **Method**: JWT (python-jose + passlib)
- **Flow**:
  1. Client POST `/api/auth/login` with email + password
  2. Server returns `{access_token, refresh_token, expires_in}`
  3. Client includes `Authorization: Bearer <token>` on subsequent requests
  4. Token expires after 480 minutes (8 hours); refresh token for new access token

### Authorization (Role-Based Access Control)
- **Roles**: `admin`, `viewer`
  - `admin`: Can trigger scrapes, manage users, view all products
  - `viewer`: Read-only access to assigned products/catalogs
- **Row-level filtering**: Viewer sees only products assigned to their account

### Database Connection in API
- Single PostgreSQL instance
- Use row-level security (RLS) policies OR tenant_id column + filters
- Each query adds `WHERE tenant_id = current_user.tenant_id`

---

## Phase 2: Authentication & User Management

### User Schema Extension
```sql
CREATE TABLE users (
    id            UUID        PRIMARY KEY,
    email         TEXT        UNIQUE NOT NULL,
    password_hash TEXT        NOT NULL,
    role          TEXT        NOT NULL DEFAULT 'viewer',
    is_active     BOOLEAN     DEFAULT TRUE,
    tenant_id     UUID        FOREIGN KEY REFERENCES tenants(id),
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE tenants (
    id        UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name      TEXT        NOT NULL,
    plan      TEXT        DEFAULT 'basic',  -- basic, pro, enterprise
    is_active BOOLEAN     DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE user_invites (
    id          UUID        PRIMARY KEY,
    tenant_id   UUID        FOREIGN KEY REFERENCES tenants(id),
    email       TEXT        NOT NULL,
    invited_by  UUID        FOREIGN KEY REFERENCES users(id),
    expires_at  TIMESTAMPTZ NOT NULL,
    accepted_at TIMESTAMPTZ
);
```

### Sign-Up Flow
1. **Pre-launch**: Admin manually creates tenant + admin user
2. **Self-service** (future): Client signs up, gets free trial tenant
3. **Invite flow**: Admin invites team members via email

### Password Reset
- Client requests reset → email with signed token (1-hour expiry)
- Click link → enter new password → token validated, password updated

---

## Phase 3: Multi-Tenancy & Data Isolation

### Strategy: Tenant ID Column + Filters

**Why not separate databases?**
- Higher maintenance cost
- Schema drift between tenants
- Hard to aggregate metrics

**Why tenant_id column?**
- Single database, easier backups
- Shared infrastructure = lower cost
- Can aggregate insights across clients

### Implementation
1. Add `tenant_id` to key tables:
   - `products.tenant_id`
   - `price_history.tenant_id`
   - `run_log.tenant_id`

2. Create SQL function to add tenant context:
   ```sql
   CREATE FUNCTION set_current_tenant(tenant_uuid UUID) RETURNS void AS $$
   BEGIN
       SET app.current_tenant_id = tenant_uuid;
   END;
   $$ LANGUAGE plpgsql;
   ```

3. Wrap all queries:
   ```python
   # In FastAPI middleware:
   current_user = get_jwt_claims(token)
   db.execute("SELECT set_current_tenant(%s)", current_user.tenant_id)
   # All subsequent queries filtered by tenant_id
   ```

4. Add RLS policy (defense-in-depth):
   ```sql
   ALTER TABLE products ENABLE ROW LEVEL SECURITY;
   CREATE POLICY tenant_isolation ON products
       USING (tenant_id = current_setting('app.current_tenant_id')::uuid);
   ```

---

## Phase 0 (Pre-MVP): Automated Scraping via GitHub Actions

**Status: planned — blocked on scraper optimization first.**

### Architecture

```
GitHub Actions (cron)          Neon PostgreSQL           Streamlit Community Cloud
┌──────────────────┐           ┌──────────────┐          ┌─────────────────────────┐
│  scrape.yml      │──writes──▶│  prices DB   │◀─reads───│  dashboard/app.py       │
│  (daily cron)    │           │  (cloud)     │          │  (public URL for client) │
└──────────────────┘           └──────────────┘          └─────────────────────────┘
```

Client only ever sees the Streamlit URL — the scraper is invisible.

### Why GitHub Actions

- Free for public repos (2,000 min/month on free tier)
- GitHub Secrets for credentials — no secrets in code
- `workflow_dispatch` for manual re-runs
- Logs visible in Actions tab — easy debugging
- No server to maintain for the scraper

### Implementation plan (when scraper optimization is complete)

1. Create `.github/workflows/scrape.yml`:
   ```yaml
   on:
     schedule:
       - cron: "0 6 * * *"   # 6 AM UTC daily
     workflow_dispatch:        # manual trigger
   jobs:
     scrape:
       runs-on: ubuntu-latest
       steps:
         - uses: actions/checkout@v4
         - uses: actions/setup-python@v5
           with: { python-version: "3.11" }
         - run: pip install -r requirements.txt
         - run: python -m scraper.main scrape
           env:
             DATABASE_URL: ${{ secrets.DATABASE_URL }}
             MAXICONSUMO_USER: ${{ secrets.MAXICONSUMO_USER }}
             MAXICONSUMO_PASS: ${{ secrets.MAXICONSUMO_PASS }}
             VITAL_USER: ${{ secrets.VITAL_USER }}
             VITAL_PASS: ${{ secrets.VITAL_PASS }}
             NINI_USER: ${{ secrets.NINI_USER }}
             NINI_PASS: ${{ secrets.NINI_PASS }}
   ```

2. Add all secrets to GitHub repo Settings → Secrets → Actions

3. Add `DATABASE_URL` support to `scraper/db.py` (currently uses individual `DB_*` vars)

### Secrets required (GitHub repo Settings → Secrets → Actions)

| Secret | Value |
|--------|-------|
| `DATABASE_URL` | Neon connection string (`postgresql://...?sslmode=require`) |
| `MAXICONSUMO_USER` | Maxiconsumo account email |
| `MAXICONSUMO_PASS` | Maxiconsumo account password |
| `VITAL_USER` | Vital account email |
| `VITAL_PASS` | Vital account password |
| `NINI_USER` | Nini account email |
| `NINI_PASS` | Nini account password |

Luvik and Santa Maria need no credentials (public stores).

### Blockers before implementing

- [ ] Scraper optimization pass — all 5 suppliers must run cleanly and reliably
- [ ] Validate each supplier produces 0 errors on a clean run
- [ ] `DATABASE_URL` support in `scraper/db.py` (replaces individual `DB_*` vars)
- [ ] Error notifications (email/Slack alert if a run fails or produces 0 snapshots)

---

## Phase 4: Hosting & Deployment

### Option A: Self-Hosted (Recommended for MVP)
- **Server**: DigitalOcean App Platform OR AWS EC2
- **Database**: AWS RDS (PostgreSQL) OR DigitalOcean Managed DB
- **Frontend**: Streamlit on same server OR separate static hosting (Vercel)
- **Cost**: ~$50–200/month
- **Maintenance**: You manage updates, backups

### Option B: Serverless (Future)
- **API**: AWS Lambda + API Gateway (pay-per-request)
- **Database**: Aurora Serverless (auto-scale)
- **Frontend**: Vercel (Next.js)
- **Cost**: $0–100/month depending on usage
- **Maintenance**: Minimal (AWS manages infra)

### Deployment Checklist
- [ ] SSL certificate (Let's Encrypt)
- [ ] HTTPS only (redirect HTTP → HTTPS)
- [ ] Environment variables (DB creds, JWT secret, API keys)
- [ ] Backups (daily, 30-day retention)
- [ ] Monitoring (error logs, uptime alerts)
- [ ] Rate limiting (prevent abuse)
- [ ] CORS policy (restrict to client domains)

---

## Phase 5: Client Onboarding

### Setup Process
1. **Admin creates tenant**: `INSERT INTO tenants (name) VALUES ('Acme Corp')`
2. **Admin creates users**: Batch invite emails
3. **Configure suppliers**: Which suppliers this client cares about
4. **First scrape**: Run all suppliers, populate their view
5. **Data calibration**: Review & approve taxonomy (brands, types, categories)

### Client Training
- **Video walkthrough**: Browse, compare, export, alerts
- **API documentation**: For developers integrating with their systems
- **Slack/Email support**: Tier-based (basic/pro/enterprise)

---

## Phase 6: Client Features (Post-MVP)

### Feature Roadmap
| Feature | Phase | Effort | Value |
|---------|-------|--------|-------|
| Shopping lists | 2 | Med | High |
| Price alerts | 2 | Med | High |
| Supplier scorecards | 3 | High | Med |
| Bulk order calculator | 3 | High | High |
| Invoice reconciliation | 4 | Very High | High |
| Demand forecasting | 5 | Very High | Medium |

### Shopping Lists
- Client saves products to a list with target quantities
- System calculates bulk pricing (units_per_package, packs_per_pallet)
- Compares total cost across suppliers
- Exports to procurement system (CSV, EDI, API)

### Price Alerts
- Client sets price threshold: "Alert me if Coca-Cola 2L drops below $1.50"
- Nightly job checks price_history
- Email/SMS notification on breach
- Link to comparison view for that product

---

## Pricing Model

### Tier 1: Basic ($0–50/month)
- Up to 5 users
- Read-only access
- 30-day history
- Email support

### Tier 2: Pro ($50–200/month)
- Up to 20 users
- Shopping lists + alerts
- 90-day history
- Priority email support

### Tier 3: Enterprise (Custom)
- Unlimited users
- Custom integrations
- API webhooks for events
- Dedicated support + onboarding

---

## Security Considerations

### Checklist
- [ ] Passwords hashed with bcrypt (cost factor ≥ 12)
- [ ] JWT tokens signed with strong secret (≥ 32 bytes)
- [ ] Refresh tokens stored in httpOnly cookies (no JS access)
- [ ] SQL injection prevention (use parameterized queries)
- [ ] Rate limiting on login (prevent brute force)
- [ ] Audit log all data access (who, what, when)
- [ ] GDPR compliance (data deletion, export requests)
- [ ] PCI-DSS not required (no payment cards stored)

---

## Implementation Timeline

| Phase | Duration | Effort | Start |
|-------|----------|--------|-------|
| Phase 1: API core | 2–3 weeks | High | Now |
| Phase 2: Auth | 1–2 weeks | Medium | After Phase 1 |
| Phase 3: Multi-tenancy | 1 week | Medium | After Phase 2 |
| Phase 4: Hosting | 1 week | Low | After Phase 3 |
| Phase 5: Onboarding | 1 week | Low | Parallel |
| Phase 6: Features | 2–4 weeks | High | After Phase 5 |

**Total to MVP**: 6–9 weeks

---

## Next Steps

1. **Start Phase 1**: Build FastAPI app with `/api/products` and `/api/comparison` endpoints
2. **Create test client**: Set up local test tenant + user for end-to-end testing
3. **Database migration**: Add tenant_id column, migrate existing data
4. **API docs**: OpenAPI (Swagger) for clients to read
5. **Deploy MVP**: Single-client production instance
