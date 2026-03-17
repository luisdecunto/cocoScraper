# Canonicalization Guide — Brand-by-Brand Cleanup

## What we're doing

Each supplier names products differently. We extract structured fields
(`brand`, `product_type`, `variant`) from raw names so that the **same product
from different suppliers gets the same `canonical_key`** and can be matched for
price comparison.

`canonical_key` format: `BRAND|TYPE|VARIANT_SORTED|MEASUREMENT`

- Accent-insensitive, uppercase
- Variant words are **sorted alphabetically** → order doesn't matter
- Measurement: `W<grams>`, `V<ml>`, `U<count>`, or `?`

---

## Files to edit

### Per-supplier postprocessors
| Supplier | Postprocessor | Alias data files |
|---|---|---|
| Maxiconsumo | `scraper/postprocess/maxiconsumo.py` | `data/maxiconsumo_brands.txt`, `data/maxiconsumo_product_type_aliases.txt` |
| Santa Maria | `scraper/postprocess/santamaria.py` | (inline dicts: `_PRODUCT_TYPE_ALIASES`, `_VARIANT_NORMALIZATIONS`, `_BRAND_ALIASES`) |
| Vital | `scraper/postprocess/vital.py` | `data/vital_brands.txt`, `data/vital_product_type_aliases.txt` |
| Nini | `scraper/postprocess/nini.py` | `data/nini_brands.txt`, `data/nini_product_type_aliases.txt` |
| Luvik | `scraper/postprocess/luvik.py` | `data/luvik_brands.txt` |

### Pipeline (canonical_key logic)
`scraper/postprocess/pipeline.py` — contains `_canonical_key()` and `FEATURES_VERSION`

---

## Where to add each type of fix

### 1. Product type abbreviation → canonical  (e.g. `Caram. Mast.` → `Caramelos`)

**Santa Maria** — inline dict in `santamaria.py`:
```python
_PRODUCT_TYPE_ALIASES: dict[str, str | tuple] = {
    "past.":        "Gomitas",
    "past. goma":   "Gomitas",
    "caram. mast.": ("Caramelos", "Masticables"),  # tuple = (type, variant_prefix)
}
```
Key = `product_type.lower()` after title-casing.
Tuple form injects the second element as a variant prefix.

**Nini** — data file `data/nini_product_type_aliases.txt`:
```
Pastillitas=Gomitas
Bomb.=Bombones
Caram.=Caramelos
```
Key = first word of description after brand, ascii-folded + uppercased.

**Maxiconsumo** — data file `data/maxiconsumo_product_type_aliases.txt`:
```
PASTILL=GOMITAS
PASTILLITAS=GOMITAS
LAVAVAJ=LAVAVAJILLA
```

### 2. Variant abbreviation expansions  (e.g. `Frutal` → `Frutales`, `Yog.` → `Yogur`)

**Santa Maria** — inline list in `santamaria.py`:
```python
_VARIANT_NORMALIZATIONS: list[tuple] = [
    (re.compile(r"\bFrutal\b", re.IGNORECASE), "Frutales"),
    (re.compile(r"\bD/[Mm]ani\b", re.IGNORECASE), "De Mani"),
    (re.compile(r"\bYog\.", re.IGNORECASE), "Yogur"),
    (re.compile(r"\bSurt\.", re.IGNORECASE), "Surtidos"),
    ...
]
```

**Nini** — inline list in `nini.py`:
```python
_VARIANT_NORMALIZATIONS: list[tuple] = [
    (re.compile(r"\bYog\.(\S)", re.IGNORECASE), r"Yogur \1"),
    (re.compile(r"\bYog\.", re.IGNORECASE), "Yogur"),
    (re.compile(r"\bSurt\.(\S)", re.IGNORECASE), r"Surtidos \1"),
    ...
]
```
Note: when an abbreviation ends in `.` with no space before the next word,
use `(\S)` capture + `r"Expansion \1"` to insert a space.

**Vital** — inline in `extract_features()` in `vital.py`, after brand/type extraction:
```python
# "Frutal" → "Frutales" only in candy contexts
if pt_upper in _CANDY_TYPES:
    variant = re.sub(r"\bFrutal\b", "Frutales", variant, flags=re.IGNORECASE)
# "Roll" (not "Roll On") → "Rollo"
variant = re.sub(r"\bRoll\b(?!\s+[Oo]n)", "Rollo", variant)
```

### 3. Brand corrections

**Simple rename** (any supplier) — add to brand aliases dict:
- Santa Maria: `_BRAND_ALIASES` in `santamaria.py`
- Luvik: `_BRAND_NORMALIZATIONS` in `luvik.py`
- Vital: `_BRAND_CORRECTIONS` in `vital.py`

**Context-aware brand** (brand depends on product type):
Add code after brand extraction in `extract_features()`:
```python
# "ALA" alone = detergent, but on rice products = "Molinos Ala"
if _ascii_fold(brand).upper() == "ALA":
    if "ARROZ" in _ascii_fold(product_type or "").upper():
        brand = "Molinos Ala"
```

**Add to brand list** — add to `data/<supplier>_brands.txt` for multi-word brands
the heuristic misses (e.g. "MOLINOS ALA").

### 4. One-off product overrides  (e.g. Turron Billiken has hidden "De Mani")

Add after variant extraction in `extract_features()` in the supplier's `.py`:
```python
if brand == "Billiken" and product_type == "Turron" and variant is None:
    variant = "De Mani"
```

---

## Workflow for each brand session

```
1. grep all suppliers for the brand:
   grep -i "BRAND" exports/*_products.txt

2. Identify mismatches:
   - Same product, different product_type / variant spelling
   - Abbreviations not expanded
   - Wrong brand (e.g. "Ala" should be "Molinos Ala")

3. Add rules to the appropriate file(s) (see section above)

4. Test locally:
   PYTHONPATH=. python - << 'EOF'
   from scraper.postprocess.<supplier> import extract_features
   print(extract_features("RAW NAME HERE"))
   EOF

5. Bump FEATURES_VERSION in pipeline.py  (+1 each time rules change)
   FEATURES_VERSION = 7   # was 6

6. Run the pipeline (no --force needed when version is bumped):
   python -m scraper.postprocess.pipeline

7. Check DB result:
   PYTHONPATH=. python -c "
   import asyncio; from dotenv import load_dotenv; load_dotenv()
   async def q():
       from scraper.db import get_pool
       pool = await get_pool()
       async with pool.acquire() as conn:
           rows = await conn.fetch(\"SELECT supplier, name, brand, product_type, variant, canonical_key FROM products WHERE name ILIKE '%BRAND%' ORDER BY supplier\")
           for r in rows: print(dict(r))
       await pool.close()
   asyncio.run(q())
   "

8. Clear Streamlit cache (hamburger menu → Clear cache) or wait 2 min
```

---

## Rules added so far

### Cross-supplier (pipeline.py)
- `_canonical_key` sorts variant words → "Masticables Frutales" == "Frutales Masticables"
- Accent-insensitive, case-insensitive (all fields)
- `_canonical_name` format: `Title Case type UPPERCASE BRAND lowercase variant size` — no accents (via `_ascii_fold`)

### Maxiconsumo
- `PASTILL` → `GOMITAS`
- `PASTILLITAS` → `GOMITAS`
- `TURRON BILLIKEN` with no variant → variant = "De Mani" (hardcoded one-off)

### Santa Maria
- `Past.` → `Gomitas`
- `Past. Goma` → `Gomitas`
- `Caram. Mast.` → type=`Caramelos`, variant prefix=`Masticables`
- Variant expansions: `Frutal`→`Frutales`, `D/Mani`→`De Mani`, `Yog.`→`Yogur`, `Surt.`→`Surtidos`
- Variant expansions: `Tutti Fr.` / `Tuti Fruti` / `Tuttti Frutti` → `Tutti Frutti`
- Brand aliases: `luccheti`→`Lucchetti`, `rindedos`→`Rinde 2`, `salzano`→`F. Salzano`

### Nini
- `Pastillitas` → `Gomitas` (product type alias)
- Variant: `Yog.` → `Yogur`, `Surt.` → `Surtidos`, `Surtida` → `Surtidos`
- Variant: `Frutal` → `Frutales` (candy types only)

### Vital
- Variant: `Roll` (not Roll On) → `Rollo`
- Variant: `Frutal` → `Frutales` (candy types only)
- Variant: trailing orphan `X` stripped (artifact of `x<N>unit` size parsing)
- Brand: `Ala` on `Arroz`/`Crackers`/`Tostadas` products → `Molinos Ala`

### Luvik
- Brand: `ALA` on `Arroz`/`Crackers`/`Tostadas` products → `Molinos Ala`
- Brand normalizations: `Cañuelas`, `Mr.Músculo`, `Gentleman`, `Dolce Gusto`, `C. Creciente`, `Las Tres Niñas`

---

## Starting prompt for next session

```
We are doing brand-by-brand canonicalization cleanup on the cocoScraper project.
Read docs/canonicalization_guide.md for the full workflow and which files to edit.

The current FEATURES_VERSION in scraper/postprocess/pipeline.py is 6.
Each time we change extraction logic, bump it by 1 so the pipeline re-processes everything.

Today's brand: [BRAND NAME]

Start by running:
  grep -i "BRAND" exports/*_products.txt
to show me all products from that brand across all suppliers, then we'll identify
mismatches and add rules.
```
