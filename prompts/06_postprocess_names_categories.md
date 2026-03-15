# Supplier Postprocessing — Names & Categories
# Master prompt — replace [SUPPLIER] before using

> Fill in [SUPPLIER] with one of: nini, luvik, vital, santamaria, maxiconsumo
> Run this in a new chat with access to the project and a live DB connection.

---

## Context

Project: cocoScraper — multi-supplier wholesale price scraper.
Root: `c:\Users\luisd\Documents\Luis\cocoScraper\`

The DB has two main tables:
- `products` — one row per (sku, supplier): name, url, category
- `price_snapshots` — one row per (sku, supplier, date): price_unit, price_bulk, stock

**SKU is a supplier-internal number.** It is used only for deduplication within a
single supplier's data. It cannot be used to identify or match products across suppliers.

**Product name is the primary human-readable identifier.** But raw names come from
the supplier as-is — they may have inconsistent casing, redundant whitespace, embedded
quantity info (e.g. "x24", "500g"), abbreviations, or encoding artifacts.

**Category** is also supplier-specific and not yet normalized.

---

## Goal

Build a postprocessing layer for the `[SUPPLIER]` supplier, iteratively.

We are NOT trying to match products across suppliers yet.
We ARE trying to extract clean, structured data from raw names and categories so that:
- Names are human-readable and searchable
- Quantity/packaging info (units per pack, weight, volume) is extracted when present
- Categories are clean and consistent

---

## Workflow — follow this loop for each step

**Before writing any code:**
1. Query the DB and print 10–15 raw product names and categories for `[SUPPLIER]`
   to understand what you're working with.
2. Identify patterns, problems, and opportunities.
3. Propose what you plan to do and wait for confirmation before implementing.

**After each transformation you implement:**
- Run a query that applies the transformation to 5 random products
- Print: raw name → transformed name (and raw category → transformed category if applicable)
- This is the feedback loop. Do not move to the next step until the output looks correct.

Use this query pattern to sample:
```sql
SELECT sku, name, category
FROM products
WHERE supplier = '[SUPPLIER]'
ORDER BY RANDOM()
LIMIT 5;
```

---

## Steps to work through

Work through these one at a time. After each step, show the 5-product sample output.

### Step 1 — Explore raw data
Query 15 products. Observe and report:
- Name format: casing, typical length, common abbreviations
- What extra info is embedded in names (weight, volume, units, brand)?
- Category format: how many distinct categories? Are they clean or messy?
- Any encoding issues (accents, special chars, `\xa0`, etc.)?

No code yet — just observation and a summary of what needs to be done.

### Step 2 — Name cleaning
Implement a `clean_name(raw: str) -> str` function that:
- Strips leading/trailing whitespace
- Collapses internal multiple spaces into one
- Normalizes encoding artifacts (e.g. `\xa0` → space)
- Standardizes casing (title case, or preserve supplier style — decide based on what you see)
- Does NOT remove any meaningful content

Show 5 samples: raw → cleaned.

### Step 3 — Feature extraction from name
Implement an `extract_features(name: str) -> dict` function that extracts:
- `brand` — leading brand/trademark token if detectable
- `weight_g` — weight in grams if present (e.g. "500G", "1KG", "250 gr")
- `volume_ml` — volume in ml if present (e.g. "1L", "500cc", "2LT")
- `units_in_name` — quantity embedded in name (e.g. "x24", "X 12", "24UN")
- `clean_name` — name with the above tokens removed and re-stripped

Return `None` for fields that are not present. Do not guess.

Show 5 samples: name → extracted dict.

### Step 4 — Category normalization
Look at all distinct categories for `[SUPPLIER]`:
```sql
SELECT category, COUNT(*) as n
FROM products
WHERE supplier = '[SUPPLIER]'
GROUP BY category
ORDER BY n DESC;
```

Propose a normalized category mapping. Goals:
- Consistent casing and spelling
- Remove path separators or prefixes that are artifacts of the scrape
- Collapse near-duplicates (e.g. "Limpieza" vs "LIMPIEZA" vs "Prod. Limpieza")
- Preserve meaningful subcategory where it exists (e.g. "Bebidas / Gaseosas")

Implement `normalize_category(raw: str) -> str` and show 5 samples.

### Step 5 — Integration
Decide together where this postprocessing should live:
- Option A: in the supplier file itself (applied at scrape time, before DB insert)
- Option B: in a new `scraper/postprocess/[SUPPLIER].py` module (applied as a separate pass)
- Option C: both — clean at scrape time, re-run postprocessing independently

Implement whichever option is chosen. The functions must be importable and testable
independently of a live scrape.

---

## Constraints

- All functions must have type hints and a one-line docstring.
- Use `logging` not `print` for anything that runs in production.
- Do not change `db.py`, `scraper.py`, or `base.py`.
- Do not change the DB schema.
- Do not add new dependencies.
- Keep regex patterns simple and commented — prefer readable over clever.
- If a rule only covers 80% of cases, say so. Partial coverage with known gaps is
  better than a fragile rule that claims to cover everything.
