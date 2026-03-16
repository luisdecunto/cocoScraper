# Update & Replacement Strategy

How cocoScraper treats repeated products, deduplication, and new products.

---

## Problem Statement

When running scrapers repeatedly:
- Products may appear in the DB already (SKU + supplier exists)
- New supplier feeds may discover the same physical product under different SKUs
- Prices change but postprocessed attributes (brand, type, size, category) might be refined
- We need rules to decide: overwrite, merge, or keep both?

---

## Current Behavior (as of 2026-03-16)

### Products Table (Upsert)
- **Key**: (sku, supplier)
- **Conflict handling**: `ON CONFLICT DO UPDATE SET`
  - `name`, `url`, `category`, `units_per_package`, `packs_per_pallet`, `updated_at`
  - Use `COALESCE()` for Nini-only fields so other suppliers don't overwrite NULL
- **Result**: One row per (sku, supplier) pair

### Price Snapshots (Dedup by value)
- **Rule**: Skip insert if `price_unit` AND `price_bulk` match the last recorded row
- **Key**: `(sku, supplier, scraped_at)` — UNIQUE
- **Result**: Only new price intervals written; same-price days are skipped
- **Why**: Keeps snapshots table small; price_history still tracks intervals correctly

### Postprocess Data (Brand, Type, Size, Category)
- **Run separately** after scrape: `python -m scraper.postprocess.<supplier>`
- **Idempotent**: Safe to re-run on same data
- **Updates**: Overwrites `brand`, `product_type`, `variant`, `size`, `category_dept`, `category_sub`
- **Coverage**: Logged per supplier (e.g., 99.8% brand match for maxiconsumo)

---

## When to Re-Scrape

### Full Re-Scrape (Delete then scrape)
**Conditions:**
- Supplier site structure changed (selectors broken)
- Login method broke
- Need to start fresh (data corruption)

**Process:**
1. `DELETE FROM products WHERE supplier = 'supplier_name'` (cascades to snapshots)
2. `DELETE FROM price_history WHERE supplier = 'supplier_name'`
3. Run scraper: `python -m scraper.main scrape --supplier supplier_name`
4. Run postprocess: `python -m scraper.postprocess.supplier_name`

### Incremental Re-Scrape (Keep existing data)
**Conditions:**
- Routine daily/weekly scrape (normal case)
- Some categories failed mid-run (SSL errors, network blips)
- Need to top-up partial collection

**Process:**
1. Run scraper: `python -m scraper.main scrape --supplier supplier_name`
   - Existing (sku, supplier) rows are UPDATEd (name, category, etc.)
   - New products are INSERTed
   - Old products not in feed remain in DB
2. New snapshots are written if prices differ
3. Run postprocess to refine attributes

---

## Deduplication: Same Product, Different Suppliers

### The Problem
Five suppliers may sell the same physical product (e.g., "Coca-Cola 2L bottle") under different SKUs:
- maxiconsumo: SKU 1001
- nini: SKU 5678
- luvik: SKU abc123
- vital: EAN 7790150000202
- santamaria: CATALOG_ID_999

### Current Solution: Canonical Matching
**File**: `scraper/postprocess/unify.py`

**Process:**
1. Extract normalized fields: `brand | product_type | size | category`
2. Hash them: `canonical_key = md5(brand + '|' + type + '|' + size)`
3. Store in `products.canonical_key`
4. Join on canonical_key to find matches

**Current State**:
- 774 canonical matches found across 16k products
- Not yet 100% coverage (missing brands, type ambiguity, etc.)

### Future: Unified Taxonomy
**Goal**: Canonical keys stable across all suppliers with same semantics

**Steps**:
1. Build master brand list (multi-supplier consensus)
2. Build master product type ontology
3. Standardize size parsing across all suppliers
4. Map supplier categories to common taxonomy
5. Publish unified data model for clients

---

## Price History: What Gets Tracked?

### price_history Table
- Tracks intervals where a price was active
- Populated after scrapers via price-change detection
- Keyed by: `(sku, supplier, first_seen, last_seen)`

### Snapshot vs. History
- **Snapshot**: Raw price recorded on scrape date (one per date max)
- **History**: Interval showing when a price was in effect

### Updating Rules
| Field | On New Price | On Same Price | On Stock Change |
|-------|--------------|---------------|-----------------|
| price_unit | Update history + new snapshot | No new snapshot | No change |
| price_bulk | Update history + new snapshot | No new snapshot | No change |
| stock | Update in products table | No new snapshot | Update, but NOT history |

**Rationale**: Stock is ephemeral (changes hourly); prices are durable (stay for days/weeks).

---

## Postprocessing: Refinement Loop

### When to Rerun Postprocessing
**Do rerun if:**
- Fixed a bug in brand extraction (e.g., "ACME" wasn't in the brand list)
- New supplier added similar product names (update data files)
- Coverage metrics improved (more brand/type matches)

**Safe to rerun?** Yes — idempotent. Rebuilds attributes from raw `name`.

### What Data Files Control Postprocessing?
- `maxiconsumo_brands.txt`, `maxiconsumo_product_types.txt`
- `nini_brands.txt`, `nini_product_type_aliases.txt`
- `luvik_brands.txt`, `luvik_product_types.txt`
- `vital_brands.txt`, `vital_product_types.txt`

**Process**:
1. Edit data files (add missing brands, expand aliases)
2. Rerun postprocessor: `python -m scraper.postprocess.supplier_name`
3. Verify coverage: Check updated `products` table
4. Commit data files to git

---

## Schema Assumptions

### products
- (sku, supplier) = unique, immutable key
- Other fields = mutable (name, category, postprocessed attributes)
- Nini-only fields (units_per_package, packs_per_pallet) = NULL for other suppliers

### price_snapshots
- (sku, supplier, scraped_at) = unique
- Dedup rule: skip if price_unit + price_bulk unchanged

### price_history
- (sku, supplier, first_seen, last_seen) = unique interval
- Populated by gap-and-islands migration

---

## Next Steps

1. **Monitor coverage**: Track brand/type extraction success rates per supplier
2. **Expand unification**: Grow canonical_key matches toward 90%+ coverage
3. **Client API**: Expose products via REST API (authenticated)
4. **Export workflows**: Automated CSV/XLSX exports per client use case
