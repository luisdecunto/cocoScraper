# Postprocessing — Name Decomposition for SUPPLIER=<name>

## Context

cocoScraper lives at: c:\Users\luisd\Documents\Luis\cocoScraper\

The postprocessing system decomposes raw supplier product names into structured fields:
`product_type`, `brand`, `variant`, and measurements (weight/volume/units).

Reference implementation: `scraper/postprocess/maxiconsumo.py`
Shared utilities:         `scraper/postprocess/_utils.py`  (if it exists; else copy from maxiconsumo.py)
Data files:               `scraper/postprocess/data/`

The supplier you are implementing is: **SUPPLIER**

---

## Step 1 — Read before doing anything

1. `scraper/postprocess/maxiconsumo.py` — full reference implementation
2. `scraper/postprocess/_utils.py` — shared utilities (if it exists)
3. `scraper/postprocess/data/maxiconsumo_brands.txt` — ~1057 brands (bootstrap source)
4. `scraper/postprocess/data/maxiconsumo_product_types.txt` — ~169 types (bootstrap source)
5. `scraper/suppliers/SUPPLIER.py` — to understand the raw name format

---

## Step 2 — Analyze product names from the DB

Run a script to fetch all unique product names for SUPPLIER from the DB and print:
- Total product count
- 30 random samples
- Any obvious format pattern (does brand lead or trail? are measurements in the name?
  is brand available as a separate field from the API?)

---

## Step 3 — Bootstrap data files

Use the maxiconsumo files as a head start (Argentine FMCG brands and product types
overlap heavily across suppliers):

- Copy `data/maxiconsumo_brands.txt`        → `data/SUPPLIER_brands.txt`
- Copy `data/maxiconsumo_product_types.txt` → `data/SUPPLIER_product_types.txt`
- Create empty `data/SUPPLIER_product_type_aliases.txt` with a header comment block

Do not strip entries from the bootstrapped files at this stage — missing entries will
surface during the coverage check in Step 5.

---

## Step 4 — Implement `scraper/postprocess/SUPPLIER.py`

Follow the exact structure of `maxiconsumo.py`:

```python
from scraper.postprocess._utils import _ascii_fold, _load_lines, _load_aliases, clean_name, _DATA_DIR

_KNOWN_PRODUCT_TYPES    = sorted(_load_lines("SUPPLIER_product_types.txt"), key=len, reverse=True)
_KNOWN_BRANDS_RAW       = _load_lines("SUPPLIER_brands.txt")
_PRODUCT_TYPE_ALIAS_MAP = _load_aliases("SUPPLIER_product_type_aliases.txt")
# ... fold maps, _BRAND_CORRECTIONS dict ...

def extract_features(name: str) -> dict: ...
def parse_category(raw: str) -> dict: ...

if __name__ == "__main__":
    # CLI dry-run: fetch 20 random products, print structured table
    ...
```

Key adaptation rules per supplier name format:

| Supplier     | Format                              | Adaptation                                              |
|--------------|-------------------------------------|---------------------------------------------------------|
| Santa Maria  | `PRODUCTO MARCA MODELO`             | Standard tokenization, same as Maxiconsumo              |
| Luvik        | `Base Product — Variant Title`      | Split on ` — `; variant goes directly into variant field|
| Vital        | `MARCA PRODUCTO VARIANT`            | Brand often leads; adjust token order                   |
| Nini         | `TRADEMARK  description` (two APIs) | `trademark` = brand (use directly); extract product_type from description only |

If the supplier API provides brand as a separate field, use it directly instead of
extracting it from the concatenated name string.

---

## Step 5 — First-pass coverage check

Run a coverage script:
- Call `extract_features()` for every product in the DB for SUPPLIER
- Report: total products, brand matched via lookup table, brand via heuristic only, brand=Generico
- Print the 50 products where brand=Generico or brand came from heuristic only

**Stop here and show the list to the user before continuing.**

---

## Step 6 — Iterate until ≥95% lookup-matched brand coverage

For each round:
1. User reviews the unbranched/heuristic list and provides instructions
2. Add missing brands to `SUPPLIER_brands.txt`
3. Add missing product types to `SUPPLIER_product_types.txt` (exposes brands that were consumed)
4. Add alias rules to `SUPPLIER_product_type_aliases.txt` for typos/variants
5. Add `_BRAND_CORRECTIONS` entries in code for non-obvious cases (brand embedded in name token)
6. Re-run coverage check and show delta

Target: **≥95% of products have brand matched via lookup table** (not heuristic/Generico).

---

## Step 7 — Final verification

Run the CLI dry-run:
```bash
PYTHONPATH=c:/Users/luisd/Documents/Luis/cocoScraper python -m scraper.postprocess.SUPPLIER
```

Spot-check 10 products from different categories. Confirm:
- `product_type` is sensible
- `brand` is correct
- `variant` contains only descriptive text (no measurements, no product type, no brand repeated)
- measurements (`weight`/`volume`/`units_in_name`) are populated where the name contains them

---

## Constraints

- Do not modify `scraper/postprocess/maxiconsumo.py` or `_utils.py`
- Do not modify the DB schema, scraper files, or `scraper/db.py`
- Do not add new Python dependencies
- Keep `_BRAND_CORRECTIONS` minimal — prefer adding entries to the TXT files
- Data file format: one entry per line, `#` for comments, UPPERCASE entries
- Alias file format: `VARIANT=CANONICAL`, both sides UPPERCASE
