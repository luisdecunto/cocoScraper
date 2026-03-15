# Santa Maria — Recon Notes

## Basic info
- URL: https://tienda.santamariasa.com.ar/comercio/
- Platform: osCommerce (classic — identified by index.php?cPath=, product_info.php, osCsid session param)
- Login required: yes — prices hidden without authentication
- Price tiers: two prices per product — s/IVA (net, without tax) and c/IVA (gross, with tax)
  → mapped to price_unit=s/IVA, price_bulk=c/IVA

## Scraping approach decision
[x] httpx + BeautifulSoup (static HTML)
[ ] Playwright
[ ] httpx JSON API
Reason: no XHR/Fetch requests observed, full HTML served server-side (PHP).
No JS rendering needed.

## Auth flow
- Login page: https://tienda.santamariasa.com.ar/comercio/login.php
- Form action: https://tienda.santamariasa.com.ar/comercio/login.php?action=process
- Method: POST
- Fields: email_address, password
- Hidden field: formid (CSRF token — extract fresh from login page HTML each time)
- Session is cookie-based (osCsid). httpx AsyncClient carries it automatically.
- Do NOT append osCsid manually to URLs — the session cookie handles it.
- Success check: after POST, verify redirect does NOT return to login.php
- formid value changes per session — always extract from the live page, never hardcode.

## URL structure
- Top-level category:  index.php?cPath=1
- Leaf category:       index.php?cPath=1_101
- Product page:        product_info.php?cPath=1_101&products_id=463
- Pagination:          index.php?cPath=1_101&page=2   (page=1 is default, omit for first page)
- Sort param present in pagination URLs (&sort=2a) — strip before adding page param

## Category discovery (VERIFIED 2026-03-10)
- cPath format: top-level = single int (cPath=1), leaf = two ints joined by _ (cPath=1_101)
- Homepage only shows top-level categories (cPath=N) — leaf links NOT present on homepage
- Two-level discovery required:
  1. Fetch homepage → collect top-level URLs (cPath=N, 16 found)
  2. Fetch each top-level page → collect leaf URLs (cPath=N_M)
- Full scrape result: 16 top-level categories, ~N leaf categories, 1782 products

## Price format (VERIFIED 2026-03-10)
- Format: $4446.270  (s/IVA) / $5379.987  (c/IVA)
- Dot is DECIMAL separator (NOT thousands separator — this is NOT Argentine $1.234,56 format)
- Three decimal places present.
- Parse: strip "$", strip label text "(s/IVA)" / "(c/IVA)", cast to float()
- price_unit = s/IVA price (net, without tax)
- price_bulk = c/IVA price (gross, with tax)

## Pagination (VERIFIED 2026-03-10)
- page=N parameter, starting from page=1 (or omit for first page)
- Next page detected by finding <a href="...page=N"> with text ">" in the pagination bar
- Strip sort= and page= params from base URL before appending &page=N

## CSS selectors (VERIFIED 2026-03-10)

```
product_item:   .productListTable tr
                (NOTE: .productListTable is a <div>, NOT a <table>)
                (60 tr elements total; ~30 are product rows, ~30 are nested form sub-rows)
                (filter: keep only tr that contain a[href*="product_info.php"])

name:           tds[1] (2nd td, index 1) → select_one("a") → get_text(strip=True)
                (tds[0] is the thumbnail image; its <a> has same href but empty text)

price:          tds[3] (4th td, index 3)
                contains "$N.NNN  (s/IVA)\n$N.NNN  (c/IVA)"
                extract with: re.findall(r'\$([\d.]+)', price_td.get_text())
                prices[0] = s/IVA = price_unit
                prices[1] = c/IVA = price_bulk

product_link:   a[href*="product_info.php"]
                (used for SKU extraction: products_id=(\d+) from href)

uxb_count:      tds[2].get_text(strip=True) — units per box (not currently stored)

next_page:      custom _find_next_page() method — scans a[href*='page='] for text '>'
```

## SKU (VERIFIED 2026-03-10)
- No barcode or EAN in listing HTML
- Use products_id from product URL as SKU (numeric string, e.g. "463")
- Product detail page shows only product name and price — no supplier reference code

## Notes
- osCsid is a PHP session ID — do not hardcode, changes per session
- httpx AsyncClient with cookies enabled handles session automatically
- After login, all subsequent requests in the same client session are authenticated
- If the site returns a 302 to login.php on any product page, session expired
- SSL: verify=False required (site has incomplete cert chain)
- Suppress InsecureRequestWarning via warnings.filterwarnings("ignore", ...)
