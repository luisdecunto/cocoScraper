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
