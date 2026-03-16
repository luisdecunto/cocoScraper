# Unification & Cross-Supplier Matching Strategy

How to identify and unify the same physical product across all five suppliers.

---

## Problem Statement

Five suppliers sell mostly the same products but under different SKUs:
- **maxiconsumo**: "Coca Cola 2L Plastic Bottle" (SKU 10234)
- **nini**: "Coke 2 Lts" (SKU 8891)
- **luvik**: "COCA COLA 2 L" (SKU abc123)
- **vital**: "Coca-Cola Pet 2 Lts" (SKU EAN_123456)
- **santamaria**: "Coca Cola 2L" (SKU cat_999)

**Goal**: Link all 5 rows so a client sees them as one product available from 5 suppliers.

---

## Current State (as of 2026-03-16)

### Data Before Unification
```
products table: 16,918 rows (5 suppliers × ~3,400 avg products each)
canonical_key: 774 matches found
Coverage: 4.6% of products matched across suppliers
```

### Matching Algorithm (scraper/postprocess/unify.py)
```python
canonical_key = md5(brand + "|" + product_type + "|" + size)
```

**Example matches:**
- maxiconsumo.10234 + nini.8891 + luvik.abc123 → Same canonical_key
- Stored in `products.canonical_key` for comparison queries

**Why so low coverage?**
- Missing/inconsistent brand extraction (esp. Santa Maria, Vital)
- Size parsing not standardized (e.g., "2L" vs "2Lts" vs "2000ml")
- Product type ambiguity (e.g., "AGUA" could be bottled water, purified water, spring water)
- Different category hierarchies per supplier

---

## Phase 1: Improve Postprocessing (Weeks 1–2)

### Goal: 85%+ brand + type extraction per supplier

**Current Coverage:**
- maxiconsumo: 99.8% brand, partial type
- nini: 100% brand, 100% type
- luvik: 100% brand, 99.6% type
- vital: 100% brand, partial type
- santamaria: Low brand coverage (~60%), type unclear

### Actions

#### 1.1 Maxiconsumo
- **Status**: Brand extraction at 99.8% (21 irreducible misses)
- **Action**: Expand `maxiconsumo_product_types.txt` with categories → types mapping
- **Effort**: 1 day (analyze remaining 21, categorize)

#### 1.2 Santa Maria (Priority — lowest coverage)
- **Analysis**: Read names, extract brands manually
- **Build data file**: `santamaria_brands.txt` (~500 brands expected)
- **Extract type**: From product description or category path
- **File**: `santamaria_product_types.txt`
- **Effort**: 3–4 days (manual curation)
- **Success metric**: 85%+ brand, 75%+ type

#### 1.3 Vital
- **Status**: 100% brand, but many OCR artifacts (ne gro, u ltra, etc.)
- **Action**: Expand `_BRAND_CORRECTIONS` dict to catch common splits
- **Rerun postprocessor**, verify coverage improves
- **Effort**: 1 day

#### 1.4 Luvik
- **Status**: 99.6% brand, 99.6% type — nearly complete
- **Action**: Investigate 4 type misses, 8 size misses
- **Likely causes**: Unusual products (e.g., "Gazebo 3x3 meters" has no type, no size)
- **Effort**: 0.5 day

#### 1.5 Nini
- **Status**: 100% brand, 100% type, 99.9% size — already excellent
- **Action**: None needed

### Result
- All suppliers: 85–100% brand extraction
- All suppliers: 75–100% type extraction
- Ready for canonical matching

---

## Phase 2: Standardize Size Parsing (Weeks 2–3)

### Goal: Consistent size representation across suppliers

### Problem
Different suppliers format sizes differently:
- "2L" vs "2 L" vs "2Lts" vs "2000ml"
- "12 x 350ml" vs "12pack 350ml"
- "1kg" vs "1000g" vs "1 Kg"
- "6 bottles 2L each" (need to extract unit AND multiplier)

### Solution: Size Canonicalization Function

```python
# In scraper/postprocess/size_parser.py

def parse_size(raw_text: str) -> tuple[float, str]:
    """
    Parse size from product name.
    Returns (value_in_base_unit, canonical_unit).

    Examples:
        "2L" → (2.0, "L")
        "12 x 350ml" → (4.2, "L")  # 12 × 350ml = 4200ml = 4.2L
        "1kg" → (1.0, "kg")
        "6 pack 500ml" → (3.0, "L")  # 6 × 500ml = 3L
    """
```

**Canonical units:**
- Liquids: `L` (liters)
- Dry goods: `kg` (kilograms)
- Count items: `unit` (each, pieces, etc.)
- Multi-packs: Extract total quantity + base unit

### Implementation
1. Write `size_parser.py` with regex patterns per supplier
2. Test against sample products from each supplier
3. Update postprocessing scripts to use it
4. Store parsed result in products: `(size_value, size_unit)`
5. Rebuild canonical_key to use standardized size

**Effort**: 2–3 days

---

## Phase 3: Build Unified Taxonomy (Weeks 3–4)

### Goal: Master list of brands, types, categories — agreed-upon across all suppliers

### Step 1: Extract Unique Values per Supplier
```sql
SELECT DISTINCT brand FROM products WHERE supplier = 'maxiconsumo' ORDER BY brand;
SELECT DISTINCT product_type FROM products WHERE supplier = 'nini' ORDER BY product_type;
SELECT DISTINCT category_dept FROM products ORDER BY supplier, category_dept;
```

**Expected output:**
- ~1,200 unique brands across all suppliers
- ~200 unique product types
- ~30 unique departments (top-level categories)
- ~500 unique subcategories

### Step 2: Merge & Deduplicate
**Tool**: Spreadsheet (Excel/Google Sheets)

1. **Brands**: Remove accents, case-normalize, merge near-duplicates
   - "Coca Cola" = "CocaCola" = "COCA-COLA" → Unified: "Coca-Cola"
   - "NESTLE" = "Nestlé" → Unified: "Nestlé"
   - Result: ~900 canonical brands

2. **Product Types**: Group by semantic meaning
   - "Agua" = "Water" = "Mineral Water" → Category: "Water"
   - "Jugo" = "Juice" = "Fruit Juice" → Category: "Juice"
   - Result: ~150 canonical types

3. **Categories**: Build hierarchy
   - Department: "Beverages"
     - Subcategory: "Soft Drinks"
     - Subcategory: "Juices"
     - Subcategory: "Water"
   - Result: ~30 departments, ~350–400 subcategories

### Step 3: Create Master Tables
```sql
CREATE TABLE taxonomy_brands (
    id           SERIAL      PRIMARY KEY,
    canonical    TEXT        UNIQUE NOT NULL,
    aliases      TEXT[]      -- ["coca cola", "cocacola", "coke"]
);

CREATE TABLE taxonomy_product_types (
    id           SERIAL      PRIMARY KEY,
    canonical    TEXT        UNIQUE NOT NULL,
    aliases      TEXT[]      -- ["agua", "water", "mineral water"]
);

CREATE TABLE taxonomy_categories (
    id           SERIAL      PRIMARY KEY,
    department   TEXT        NOT NULL,
    subcategory  TEXT        NOT NULL,
    UNIQUE(department, subcategory)
);
```

### Step 4: Map Supplier Values → Master Taxonomy
```sql
-- For each supplier, map their brands to canonical
INSERT INTO product_brand_mapping (supplier, supplier_brand, canonical_brand_id)
SELECT 'maxiconsumo', brand, taxonomy_brands.id
FROM products p
JOIN taxonomy_brands ON taxonomy_brands.aliases @> ARRAY[LOWER(p.brand)]
WHERE supplier = 'maxiconsumo' AND NOT EXISTS (...)
```

**Effort**: 1–2 weeks (significant manual work)

---

## Phase 4: Rebuild Canonical Keys (Week 4)

### Using Unified Taxonomy

```python
def build_canonical_key(product: dict) -> str:
    """
    Build canonical key using unified taxonomy.

    Args:
        product: {
            'brand': (raw brand name),
            'product_type': (raw type),
            'size_value': (float),
            'size_unit': (str),
        }

    Returns:
        canonical_key: (standardized string for matching)
    """
    # Map raw brand → canonical brand
    canonical_brand = lookup_canonical_brand(product['brand'])

    # Map raw type → canonical type
    canonical_type = lookup_canonical_type(product['product_type'])

    # Normalize size
    size_str = f"{product['size_value']}{product['size_unit']}"

    # Build key
    canonical_key = f"{canonical_brand}|{canonical_type}|{size_str}".lower()
    return md5(canonical_key).hexdigest()
```

### Update Database
```sql
-- Rebuild canonical_key for all products using new unification logic
UPDATE products p
SET canonical_key = <new_key_logic>
WHERE canonical_key IS NULL OR canonical_key = '?|?|?';
```

### Expected Results
- Canonical key coverage: 90%+ (up from 4.6%)
- Comparison table: Much denser (more matches visible)

**Effort**: 1 day (mostly testing)

---

## Phase 5: Validation & Refinement (Week 5+)

### Spot Checks
1. Pick 10 random products from each supplier
2. Manually verify their canonical matches are correct
3. Debug false negatives (should match but don't)
4. Debug false positives (shouldn't match but do)

### Automated Testing
```python
# Test: High-confidence matches
assert canonical_key_count > 0.90 * num_products

# Test: Brand extraction
assert brand_extraction_rate > 0.85

# Test: Type extraction
assert type_extraction_rate > 0.75

# Test: Size parsing
assert size_parse_rate > 0.80
```

### Client Feedback
- Show comparison matrix to client
- Ask: "Do these look like the same products?"
- Collect feedback on false positives/negatives
- Iterate taxonomy if needed

---

## Phase 6: Client-Facing Features (Week 6+)

### Unified Product View
**API Endpoint**: `GET /api/products/{canonical_key}`

Response:
```json
{
  "canonical_key": "abc123...",
  "brand": "Coca-Cola",
  "product_type": "Soft Drink",
  "size": "2.0L",
  "category": "Beverages > Soft Drinks",
  "suppliers": [
    {
      "supplier": "maxiconsumo",
      "sku": "10234",
      "price_unit": 1.99,
      "price_bulk": 23.50,
      "stock": "disponible"
    },
    {
      "supplier": "nini",
      "sku": "8891",
      "price_unit": 2.10,
      "price_bulk": 24.00,
      "stock": "disponible"
    }
    // ... 3 more suppliers
  ]
}
```

### Comparison Table (Enhanced)
- **Row**: One canonical product
- **Columns**: Supplier prices
- **Highlight**: Cheapest supplier per row
- **Export**: Single canonical product list for procurement

### Shopping List (with Unification)
```
Client adds: "Coca-Cola 2L"
System finds all 5 SKUs (canonical_key matches)
User picks suppliers → System compares bulk pricing
Export: List with 5 rows (one per supplier) or 1 row (best price)
```

---

## Effort & Timeline

| Phase | Duration | Effort | Blocker |
|-------|----------|--------|---------|
| Phase 1: Postprocessing | 1–2 weeks | High | Data curation (Santa Maria) |
| Phase 2: Size parsing | 1 week | Medium | Regex patterns for all suppliers |
| Phase 3: Unified taxonomy | 2–3 weeks | **Very High** | Manual brand/type/category mapping |
| Phase 4: Rebuild keys | 1 day | Low | Code only |
| Phase 5: Validation | 1–2 weeks | Medium | Client feedback loop |
| Phase 6: Client features | 1–2 weeks | High | API + UI integration |

**Total**: 7–10 weeks (dependent on Phase 3 effort)

---

## Risk Mitigation

### Risk: Taxonomy becomes outdated
**Mitigation**: Version control, auto-update on new suppliers/products

### Risk: False positives (different products matched)
**Mitigation**: Show confidence score, allow client to mark "not the same"

### Risk: Manual taxonomy work is expensive
**Mitigation**: Start with high-confidence matches (90%+ brand + type overlap), grow incrementally

### Risk: Clients disagree on what "same product" means
**Mitigation**: Support multiple grouping strategies (by brand only, by type only, strict match)

---

## Next Steps

1. **Week 1**: Audit Santa Maria extraction, build data files
2. **Week 2**: Expand all postprocessing coverage to 85%+
3. **Week 3**: Build size parser, standardize across suppliers
4. **Week 4**: Start taxonomy curation (spreadsheet)
5. **Week 5**: Implement taxonomy lookup, rebuild canonical keys
6. **Week 6+**: Validate, refine, expose via API
