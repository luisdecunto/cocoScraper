# cocoScraper — Project Status & Roadmap

Last updated: 2026-03-17

---

## Scraper Engine

- [x] Project setup, DB schema, run_log
- [x] Supplier: **Maxiconsumo** — 8,918 products (JSON API + login)
- [x] Supplier: **Santa Maria** — 2,233 products (HTML scrape)
- [x] Supplier: **Luvik** — 4,436 products (Shopify JSON API)
- [x] Supplier: **Vital** — 4,886 products (VTEX IS API + login)
- [x] Supplier: **Nini** — 7,646 products (ASP.NET custom API + login)
- [x] Parallel scraping — all 5 suppliers run simultaneously (`asyncio.gather`)
- [x] Price history — `price_history` table with first_seen/last_seen periods (no duplicate rows)
- [x] `DATABASE_URL` support — scraper writes to Neon (cloud) or local PostgreSQL

---

## Postprocessing Pipeline

- [x] Feature extraction for all 5 suppliers (brand, product_type, variant, size)
- [x] Canonical key — 4-part `BRAND|TYPE|VARIANT|MEASUREMENT` for cross-supplier matching
- [x] Canonical name — human-readable label (e.g. "Harina Blancaflor Leudante 1 kg")
- [x] Unit normalization for matching (kg→g, l→ml, cc→ml, sobres→U{n})
- [x] Original unit preserved in display ("500 cc", "1 lt", "10 sobres")
- [x] Parallel pipeline execution (all suppliers processed simultaneously)
- [x] Batch DB writes (`executemany`) — from ~2 products/s to seconds total
- [x] Progress logging with speed + ETA
- [x] Category taxonomy — 212 mappings in `unified_categories.txt`
- [ ] Extend taxonomy — run `--list-unmapped`, add missing product_types
- [ ] Verify cross-supplier match count in comparison page (target: 1000+ matches)

---

## Dashboard (Streamlit Cloud)

- [x] Browse page — full catalog with advanced filters
- [x] Comparison page — cross-supplier price matrix, highlighted cheapest
- [x] Price History page — timeline chart per product
- [x] `canonical_name` column in browse + comparison
- [x] Stock filters (hide out of stock / critical availability)
- [x] Export CSV from any view
- [x] Deployed on Streamlit Community Cloud (public URL)
- [x] Reads from Neon (cloud PostgreSQL)
- [ ] Login screen — wrap dashboard in `streamlit-authenticator`
- [ ] "My List" tab — per-user watchlist of saved products
- [ ] Price alert page — notify when product drops below threshold

---

## Auth & User Management

- [x] `users` table in DB schema (id, email, password_hash, role, is_active)
- [ ] `user_watchlist` table (user_id, product_id, added_at)
- [ ] `dashboard/auth.py` — load credentials from DB, bcrypt verify
- [ ] `dashboard/app.py` — wrap in login screen
- [ ] CLI: `python -m scraper.main users add/list/deactivate/reset-password`
- [ ] Add `streamlit-authenticator` + `passlib[bcrypt]` to `requirements.txt`
- [ ] Create first admin user on production

---

## Automation

- [ ] GitHub Actions daily scrape (`.github/workflows/scrape.yml`, cron 6 AM UTC)
- [ ] Error notification if run produces 0 snapshots (email or Slack)
- [ ] `workflow_dispatch` for manual re-runs from GitHub UI

---

## Hosting / Deploy

- [ ] Decide: stay on Streamlit Cloud + GH Actions, or move to self-hosted VPS
- [ ] If VPS: nginx config, systemd unit, cron job, Let's Encrypt SSL
- [ ] `deploy/SETUP.md` — step-by-step provisioning guide

---

## Phase Order (recommended)

1. **Now** — Extend taxonomy (`--list-unmapped` → edit `unified_categories.txt`)
2. **Next** — Auth + watchlist (login screen + "My List" tab)
3. **Then** — GitHub Actions automation (daily scrape without manual runs)
4. **Later** — VPS hosting decision + deploy configs

---

## Nice-to-Have (post-MVP)

- [ ] Shopping list — select products + quantities, compare total cost across suppliers
- [ ] Bulk order calculator — factor in units_per_package / packs_per_pallet
- [ ] Supplier scorecard — availability %, average price trend, scrape reliability
- [ ] FastAPI REST layer — programmatic access for clients integrating with their systems
- [ ] Multi-tenancy — tenant_id + RLS for fully isolated client views
