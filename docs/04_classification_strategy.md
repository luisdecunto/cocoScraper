# Classification Strategy — Manual Approval Workflow

How new products are classified, approved, and protected from re-processing.

---

## Overview

When a product is scraped for the first time, it contains only raw data:
- `name`, `url`, `category`, `price`, `stock`
- All classification fields (`brand`, `product_type`, `size`, `canonical_name`, etc.) = NULL
- `classification_status` = NULL (unclassified)

The classification pipeline extracts normalized fields using **priority-based strategy**,
then the product awaits **manual human approval** before classification is frozen forever.

---

## Classification Priority (First Match Wins)

### Level 1: Supplier-specific structured rules

Each supplier has unique naming conventions that allow specialized extraction:

| Supplier | Rule | Reliability |
|---|---|---|
| **Nini** | All-caps prefix heuristic: walk tokens left-to-right, accept tokens with uppercase chars until one fails → BRAND | Highest (strict naming) |
| **Vital** | OCR-correction dictionary applied first, then brand list match | Very high (OCR cleanup) |
| **Maxiconsumo** | Product type stripped first (longest-first), then brand list | High (type-first format) |
| **Luvik / Santa Maria** | Article heuristic: SAN, LA, DON, LAS, LOS, EL as brand indicators | Medium (multi-word detection) |

**When matched**: `confidence = 'high'`

### Level 2: Per-supplier brand list

Longest-first prefix match against supplier-specific brand file:

```
nini_brands.txt (1,067)        — numeric-start, multi-word, title-case
maxiconsumo_brands.txt (1,060) — private label + grocery brands
vital_brands.txt (2,026)       — merged from other suppliers
luvik_brands.txt (147)         — multi-word only (single-token via heuristic)
```

Example: name = "BOLETIN AGUA MINERAL 1.5 L"
- Try supplier rules → if fails
- Try brand list → longest-first match finds "BOLETIN" ✓

**When matched**: `confidence = 'high'`

### Level 3: Cross-supplier unified normalization

Applied **after** extracting raw brand from levels 1-2:

**`brand_aliases.txt`** — normalizes variants that appear across suppliers:
- Apostrophe variants: `HELLMANN S` → `HELLMANS`
- Typos: `KELLOGS` → `KELLOGGS`
- Truncations: `GLOUCESTE` → `GLOUCESTER`
- Space variants: `BULL DOG` → `BULLDOG`
- Merged names: `CAROLINA` → `MOLINOS ANA` (editorial decision)

If brand extract already matched levels 1-2, this step just ensures consistency.

**When matched**: `confidence = 'high'` (brand list already found it)

### Level 4: Heuristics (Last Resort — Flag for Review)

If a brand wasn't found in lists, fall back to heuristics:
- All-caps token detection
- Article-based patterns
- First word as brand (risky)

These are unreliable and the product should be **flagged for manual review**.

**When matched**: `confidence = 'low'` (human review recommended)

---

## Confidence Signal

| How brand was found | confidence | Notes |
|---|---|---|
| Level 1 rules or Level 2 list | `'high'` | Reliable, pre-approved patterns |
| Level 3 aliases (from list match) | `'high'` | Just a normalization step |
| Level 4 heuristics only | `'low'` | Human review recommended |

The dashboard **Revisar** page sorts by confidence, letting you approve high-confidence
batches quickly and review low-confidence products manually.

---

## States: Classification Lifecycle

```
NULL                            (unclassified)
  ↓
[pipeline runs]
  ↓
'pending'                       (auto-classified, awaiting review)
  ├─→ [admin approves]
  │     ↓
  │   'approved'               (frozen forever, pipeline won't touch)
  │
  └─→ [admin rejects / edits]
        ↓
      NULL                      (resets to unclassified for re-classification)
```

### Semantics

- **NULL**: Pipeline will process this product (unclassified)
- **'pending'**: Pipeline has classified it, but human approval is needed
- **'approved'**: Human approved; classification is frozen, used for matching
- **'rejected'**: Human rejected; classification is reset; product queued for retry

---

## Manual Approval Workflow

### Dashboard → Revisar page

**URL**: `/app/revisar` (Streamlit nav entry)

**Filters**:
- Supplier selector (dropdown)
- Confidence filter (All / High only / Low only)

**Table**:
```
SKU | Raw Name | Brand | Type | Variant | Size | Canonical Name | Confidence
```

**Row Actions**:

| Action | Effect |
|---|---|
| **✓ Aprobar** | `classification_status = 'approved'` — product is frozen |
| **✎ Editar** | Inline edit fields (brand, type, variant, size) + Guardar → approve & freeze |
| **✗ Rechazar** | Resets classification to NULL; product re-queued for classification |

**Bulk Actions**:
- **[Aprobar todos]** — approve all visible rows (e.g., all high-confidence)

### Workflow Example

```
1. Scrape 1000 new products
   → classification_status = NULL for all

2. Run pipeline
   python -m scraper.postprocess.pipeline --supplier maxiconsumo
   → classifies all 1000
   → sets classification_status = 'pending' for all
   → most get confidence='high', a few get 'low'

3. Open Revisar page in dashboard
   → shows 1000 pending products, sorted by confidence

4. Bulk-approve high-confidence products
   → [Aprobar todos] filters to confidence='high', approves 980
   → classification_status = 'approved' for those 980

5. Manually review low-confidence 20
   → for each one: ✓ Aprobar, ✎ Editar, or ✗ Rechazar
   → if edit: fix brand/type inline, Guardar approves
   → if reject: reset for retry

6. Final state
   → 1000 products approved
   → classification_status = 'approved' for all
   → brand, product_type, size, canonical_name frozen
```

---

## Protection: Write Guard

Once `classification_status = 'approved'`, no scraper or pipeline run will touch
the classification fields (`brand`, `product_type`, `size`, `canonical_name`).

Database-level guard in `batch_upsert_product_features`:

```sql
UPDATE products SET brand=$4, product_type=$5, ... canonical_name=$13
WHERE sku=$1 AND supplier=$2
  AND classification_status IS NULL    ← only write if unclassified
```

If you run the pipeline again, it **skips** products with `classification_status != NULL`.

---

## Reclassification (Deliberate Reset)

If you fix an extraction bug or want to re-classify all products of a supplier:

```bash
python -m scraper.postprocess.pipeline --supplier maxiconsumo --reclassify

# This:
# 1. Resets classification_status, canonical_name, brand, etc. to NULL
# 2. Runs full classification pipeline
# 3. Sets all to 'pending' again
# 4. Waits for manual re-approval
```

**Use case**: You improved the brand extraction logic and want to re-run on existing data.

---

## Integration with Price Updates

Price and classification are independent:

```
Scrape runs (daily)
  └─→ upsert_product: update price_unit, price_bulk, stock ONLY
      (classification fields untouched, even if product is 'approved')
  └─→ upsert_price_history: extend or open new price period

Classification pipeline (run explicitly)
  └─→ only processes products with classification_status = NULL
  └─→ sets classification_status = 'pending'
  └─→ admin approves → 'approved' → frozen forever

Result: Price tracking and classification are decoupled.
```

---

## Future: Supplier-Specific Confidence

In the future, individual postprocessors (nini.py, maxiconsumo.py, etc.) can return
`extraction_confidence` info:

```python
# example: nini.py extract_features
def extract_features(name, category):
    ...
    return {
        "brand": brand_found,
        "extraction_confidence": "high" if found_in_list else "low",
        ...
    }
```

The pipeline would then pass this through to `classification_confidence`.

---

## Monitoring

Track approval progress:

```sql
-- Pending queue
SELECT supplier, COUNT(*), classification_confidence
FROM products WHERE classification_status = 'pending'
GROUP BY supplier, classification_confidence;

-- Approval rate
SELECT supplier, COUNT(*) as approved
FROM products WHERE classification_status = 'approved'
GROUP BY supplier;

-- Rejection rate (waiting for re-classification)
SELECT supplier, COUNT(*) as rejected
FROM products WHERE classification_status IS NULL
  AND canonical_name IS NOT NULL;  -- had a classification, but reset
```
