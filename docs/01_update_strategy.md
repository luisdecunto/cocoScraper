# Update & Replacement Strategy

How cocoScraper scrapes, classifies, approves, and protects product data.

---

## Principle: Immutable Classification

Once a product is classified and approved, its classification fields (`brand`, `product_type`,
`size`, `canonical_name`, etc.) are **frozen forever**. Price updates happen independently
and never touch classification.

---

## Current Behavior (as of 2026-03-19)

### Product Upsert (Scrape Only)

On every scrape, `upsert_product` runs:

| Scenario | Action |
|---|---|
| **New product** (not in DB) | INSERT with raw data: name, url, category, price, stock. Classification fields = NULL. |
| **Existing product** (sku, supplier exists) | UPDATE only: price_unit, price_bulk, stock, last_scraped_at. Name, category, classification fields untouched. |

**Why**: Protects frozen classifications from being overwritten by supplier data changes.

### Price Tracking (Decoupled from Classification)

- Every scrape writes current price/stock to the `products` row
- `upsert_price_history` manages price periods (extend or open new)
- Price changes are tracked independently; classification never depends on price

### Classification (Manual Approval Required)

See [docs/04_classification_strategy.md](04_classification_strategy.md) for full details.

**Quick summary**:
1. New products arrive with `classification_status = NULL`
2. Classification pipeline extracts features → sets `classification_status = 'pending'`
3. Admin approves in dashboard → `classification_status = 'approved'` (frozen)
4. Pipeline never modifies products with `classification_status != NULL`

**States**:
```
NULL → [pipeline] → 'pending' → [admin approves] → 'approved' (frozen)
                                    ↓ [admin rejects]
                                  NULL (re-queue)
```

---

## Workflow: Daily Operations

### Routine Scrape (Daily)

```bash
python -m scraper.main scrape --supplier maxiconsumo
```

**What happens**:
- New products → inserted (name, price, classification_status=NULL)
- Existing products → price/stock updated, classification untouched
- Price history extended or new periods opened

### Classification (Explicit, As-Needed)

```bash
# Process new (unclassified) products
python -m scraper.postprocess.pipeline --supplier maxiconsumo
```

**What happens**:
- Fetches products where `classification_status IS NULL`
- Runs classifier (4-level priority)
- Sets `brand`, `product_type`, `size`, `canonical_name`
- Sets `classification_status = 'pending'` (awaits approval)
- Sets `classification_confidence = 'high' | 'low'`

### Approval (Manual, Via Dashboard)

```
Dashboard → Revisar page → see pending classifications → Aprobar / Editar / Rechazar
```

**Results**:
- Approve → `classification_status = 'approved'` (frozen)
- Edit → update fields + approve
- Reject → reset `classification_status = NULL` for retry

---

## When to Full-Reclassify

**Rare scenario**: You improved the brand extraction logic and want to re-process.

```bash
python -m scraper.postprocess.pipeline --supplier maxiconsumo --reclassify
```

**What happens**:
1. Resets `classification_status = NULL` for all products of that supplier
2. Resets `brand`, `product_type`, `size`, `canonical_name` to NULL
3. Runs full classification pipeline
4. All products go back to `classification_status = 'pending'`
5. Requires manual re-approval (dashboard again)

---

## Cross-Supplier Matching (Canonical Keys)

### The Problem
Five suppliers may sell the same physical product under different SKUs:
- maxiconsumo: SKU 1001
- nini: SKU 5678
- luvik: SKU abc123
- vital: EAN 7790150000202
- santamaria: CATALOG_ID_999

### Solution: Canonical Keys (From Approved Classifications)

Once a product is approved (`classification_status = 'approved'`), its `canonical_key` is used for matching:

```
canonical_key = BRAND | PRODUCT_TYPE | VARIANT | WEIGHT/VOLUME/COUNT
```

**Key rule**: Only products with `classification_status = 'approved'` contribute to cross-supplier matching.
Pending/rejected products are excluded (might be inaccurate).

**File**: `scraper/postprocess/unify.py` — joins products on canonical_key

**Current state**: 774+ matches found across 16k products (growing as more are approved).

### Future Work

Once 90%+ of products are approved, canonical matching coverage will improve dramatically
(currently bottlenecked by pending/low-confidence classifications).

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

## Improving Classifications

### Update Brand/Type Data Files

The classification pipeline uses supplier-specific data files. To improve accuracy:

1. **Edit data files** (add missing brands, expand aliases):
   - `scraper/postprocess/data/nini_brands.txt`
   - `scraper/postprocess/data/maxiconsumo_brands.txt`
   - `scraper/postprocess/data/vital_brands.txt`
   - `scraper/postprocess/data/luvik_brands.txt`
   - `scraper/postprocess/data/brand_aliases.txt`

2. **Reclassify** to use new data:
   ```bash
   python -m scraper.postprocess.pipeline --supplier maxiconsumo --reclassify
   ```

3. **Review in dashboard** → approve new classifications

4. **Commit** data files to git

### When Classifications Lock

Once `classification_status = 'approved'`, even reclassification with `--reclassify` won't
update that product. You would need to manually edit it in the Revisar page or run a
targeted reset in SQL:

```sql
UPDATE products SET classification_status = NULL WHERE brand = 'OLD_VALUE' AND classification_status = 'approved';
```

**Safer approach**: Always review new classifications before approving them.

---

## Schema Changes (2026-03-19)

### products table
Added columns for classification workflow:
- `classification_status TEXT` — NULL / 'pending' / 'approved' / 'rejected'
- `classification_confidence TEXT` — 'high' / 'low'

**Immutability rules** (new):
- Once `classification_status = 'approved'`, classification fields (`brand`, `product_type`, etc.) are frozen
- `upsert_product` (scraper) never touches classification fields
- `upsert_price_history` updates only current price/stock, never classification

### price_history table
- Unchanged: still tracks stable price periods
- Independent from classification (price updates don't require approval)

---

## Next Steps

1. **Dashboard → Revisar page**: Build UI for approving pending classifications
2. **Monitor approval queue**: Track pending vs. approved per supplier
3. **Expand canonical matching**: Once 90%+ approved, canonical_key coverage will improve
4. **Client API**: Expose only approved products to external clients
5. **Automation**: Add `--auto-approve-high-confidence` flag for trusted suppliers
