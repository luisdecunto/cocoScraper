# Luvik — Recon Notes

## Basic info
- URL: https://tiendaluvik.com.ar/
- Platform: Shopify (confirmed: /cdn/shop/ CDN path, /collections/<slug> URL structure)
- Login required: likely no — verify by browsing a product page without login
- Price tiers: unknown — verify if prices differ when logged in vs guest

## Scraping approach decision
[ ] httpx + BeautifulSoup (static HTML)
[ ] Playwright
[x] httpx JSON API — Shopify built-in /products.json endpoint
Reason: Shopify exposes a standard JSON API on every collection.
No HTML parsing needed. No JS rendering needed.

## Auth
- **Verified 2026-03-10: no login required.** Prices visible to guest.
  30 products returned from /collections/girasol/products.json without credentials.
- config uses requires_login: False

## Shopify JSON API — confirmed structure

Every collection has a built-in products endpoint:
  GET /collections/<slug>/products.json?limit=250&page=N

Confirmed from live API response:
{
  "products": [
    {
      "id": 7413779431510,
      "title": "ACEITE CAÑUELAS GIRASOL 1.5 Lt.",
      "handle": "aceite-canuelas-girasol-1-5-lt",
      "vendor": "CAÑUELAS",
      "variants": [
        {
          "id": 44151622991958,
          "title": "Default Title",       ← single-variant placeholder
          "sku": "270038",                ← internal supplier code (not EAN)
          "price": "63480.00",            ← standard decimal string
          "compare_at_price": "63480.00", ← same as price when no discount
          "available": true               ← use for stock — inventory_quantity absent
        }
      ]
    }
  ]
}

- price: standard decimal string — parse with float(), no Argentine formatting
- sku: internal supplier code — usable for matching within this supplier
- available: bool — use instead of inventory_quantity (not present in response)
- vendor: brand name — extra field, not stored currently
- "Default Title" variant = single-variant product, don't append to name

## All products endpoint (alternative)
  GET /products.json?limit=250&page=N
Returns ALL products across all collections.
Could do the entire catalog in a few requests — verify total product count first.

## Category discovery
All leaf category URLs visible in homepage nav HTML without login:
  /collections/<slug>
No discovery logic needed — parse nav hrefs once, filter for /collections/ pattern.

## Pagination
  /collections/<slug>/products.json?limit=250&page=N
Stop when products array is empty or has fewer than 250 items.

## Price format
Standard decimal string: "1500.00"
Parse with float(price_str) — no Argentine format handling needed.

## SKU / product matching
Shopify variants[0].sku often contains EAN/barcode.
This is the best SKU source for cross-supplier comparison.
If sku field is empty: fall back to variant id or product handle.

## Notes
- Shopify enforces rate limits: ~2 req/s for unauthenticated. Use Semaphore(4) max.
- /products/count.json returns 404 — not enabled on this store.
- /products.json page 1 returns 250 items — catalog is large.
- Category discovery finds 367 collections. Many are subcategory views with overlapping
  products (e.g. aceites, aceites-para-gastronomia). DB upsert (sku, supplier) handles dedup.
- No SSL issues (standard Shopify hosting).
- SKU field: 6-digit internal code (e.g. "270038") — NOT an EAN barcode.
  Duplicate SKUs can appear across different product handles — supplier catalog data issue.
- stock field: "disponible" / "sin stock" (from v["available"] bool).
- Implementation complete: scraper/suppliers/luvik.py verified 2026-03-10.
