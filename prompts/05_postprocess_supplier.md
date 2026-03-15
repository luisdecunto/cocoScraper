# Supplier Postprocessing Audit — Master Prompt

> **How to use this prompt:**
> Copy this entire file into a new chat. Replace every `[SUPPLIER]` placeholder with the
> supplier name (e.g. `nini`, `luvik`, `vital`, `santamaria`, `maxiconsumo`).
> The agent will read the supplier code, audit it, and propose or implement fixes.

---

## Context

You are working on **cocoScraper**, a multi-supplier price scraping system for a
wholesale purchasing client. The project is at:

```
c:\Users\luisd\Documents\Luis\cocoScraper\
```

The scraper fetches products from supplier websites and stores them in PostgreSQL.
The pipeline is: **scrape → upsert_product → upsert_snapshot → display in dashboard**.

There is **no postprocessing layer** between scraping and the database. Raw parsed values
are stored as-is. We need to audit and fix the data quality for each supplier.

---

## Your task: audit and fix the `[SUPPLIER]` supplier

Read these files before doing anything else:

1. `scraper/suppliers/[SUPPLIER].py` — the supplier implementation
2. `scraper/db.py` — upsert_product and upsert_snapshot
3. `scraper/suppliers/base.py` — the BaseSupplier contract
4. `CLAUDE.md` — project conventions and DB schema

---

## What we need to verify and fix

For **every product** returned by `scrape_category()`, we need confidence that the
following five things are correct. Audit each one and fix any issues you find.

---

### 1. Product identity — SKU and name

**Goal:** each product has a stable, unique identifier and a clean human-readable name.

Questions to answer:
- What field is used as the SKU? Is it truly unique per product, or can it collide?
- If the same physical product appears in multiple categories, will it get the same SKU?
- Are there known duplicate SKU situations? How are they handled?
- Is the name clean? Does it strip whitespace, collapse redundant spaces, or handle
  encoding artifacts (e.g. `\xa0`, HTML entities)?
- Does name construction ever produce empty strings or garbage?

Fix: ensure SKU is stable and deduplicated correctly. Ensure name is stripped and
non-empty. If a fallback SKU strategy exists (e.g. `product_id-variant_id`), document
when it triggers and whether that is acceptable.

---

### 2. Price — unit price and bulk price

**Goal:** `price_unit` and `price_bulk` are always correct floats or None, never 0,
never nonsensical, never overflowing.

Questions to answer:
- What does `price_unit` represent for this supplier? (per unit, per kg, per package?)
- What does `price_bulk` represent? (closed box total, pallet total, same as unit?)
- Are prices net (excl. IVA) or gross (incl. IVA)? Are they consistent?
- Does the supplier ever return `0`, `null`, or missing prices? How is that handled?
- Does `parse_price()` handle all edge cases? (currency symbols, dots vs commas,
  whitespace, empty strings, overflow values like 3e15?)
- Is there a max-price sanity clamp? Should there be?
- Could `price_bulk` ever be less than `price_unit`? Is that valid?

Fix: ensure price parsing is robust and documented. Add or tighten the overflow clamp
if needed. Store `None` instead of `0` when a price is unknown. Add a comment explaining
what each price field means for this specific supplier.

---

### 3. Sell unit — how is this product sold?

**Goal:** we know the minimum purchase quantity and the sell unit structure.

Questions to answer:
- What is the minimum sellable unit? (individual item, pack, box, pallet?)
- Does the supplier expose `units_per_package`? (units per closed box)
- Does the supplier expose `packs_per_pallet`? (boxes per pallet)
- Is this data already being captured? Where does it come from in the raw response?
- If NOT exposed by the supplier: is the information embedded in the product name?
  (e.g. "Fideo 500g x24" — the "x24" is units_per_package)
- For suppliers where this information does not exist at all: document that explicitly.

Fix: ensure `units_per_package` and `packs_per_pallet` are populated whenever the
supplier provides this data, either from explicit API fields or by parsing the product
name. If name-parsing is used, write a function with test cases in comments.

---

### 4. Stock / availability

**Goal:** `stock` reflects what the supplier actually says about availability.

Questions to answer:
- What values does the supplier return for stock?
- Is the stock field a boolean, a string, an integer quantity, or something else?
- How is it normalized to a string for storage?
- Are there cases where "in stock" is assumed by default (e.g. when the API only
  returns available products)? Is that assumption documented?
- Can stock be `None` or empty string? What does that mean?

Fix: normalize to a consistent string. The accepted values are:
  - `"disponible"` — product is available
  - `"sin stock"` — product is out of stock
  - `"unknown"` — stock status cannot be determined
Document any supplier-specific variations.

---

### 5. Category

**Goal:** categories are clean, consistent, and useful for filtering.

Questions to answer:
- How is the category string built? (leaf category, full path, department + sector?)
- Is it consistent across scrape runs? (i.e. will the same product always land in
  the same category string?)
- Are there encoding issues or leading/trailing slashes?
- Is the category hierarchy (if any) collapsed or preserved?

Fix: ensure the category string is stripped and stable. Document the format used.

---

## Deliverables

1. **Audit report** — a short list of issues found per section (1–5 above).
   For each issue: what it is, where in the code it occurs, and what the impact is.

2. **Fixed code** — apply all fixes directly to `scraper/suppliers/[SUPPLIER].py`.
   - Follow project conventions: async everywhere, type hints, docstrings on public methods,
     `logging` not `print`, no hardcoded credentials.
   - Do not change the method signatures of `login`, `discover_categories`,
     `scrape_category`, or `parse_price` — they are part of the BaseSupplier contract.
   - You may add private helper methods.
   - Add inline comments where the logic is non-obvious.

3. **Summary** — after fixing, write a one-paragraph summary of what this supplier
   provides in each field, so it can be added to project documentation.

---

## Constraints

- Do not change `scraper/db.py`, `scraper/scraper.py`, or `scraper/suppliers/base.py`.
- Do not change the DB schema.
- Do not add new dependencies.
- Keep changes minimal and focused — do not refactor working code.
- If a fix requires a judgment call (e.g. "should we parse units from the name?"),
  state your reasoning clearly before implementing.
