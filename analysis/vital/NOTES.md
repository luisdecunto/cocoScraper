# Vital — Recon Notes

## Basic info
- URL: https://tiendaonline.vital.com.ar/
- Platform: VTEX (confirmed: arvital.vtexassets.com CDN, VTEX URL patterns)
- Login required: NO — prices visible on homepage without auth
- Price format: Argentine display ($899.01, $2.599,00) — verify raw API format

## Scraping approach decision
[x] httpx JSON API — VTEX standard catalog/search API
[ ] httpx + BeautifulSoup
[ ] Playwright
Reason: VTEX exposes a public product search API on all stores.
No login, no HTML parsing needed.

## Auth
Required — site redirects all routes to /login without auth.

### Login flow (two-step VTEX standard)

Step 1 — startlogin:
  POST /api/vtexid/pub/authentication/startlogin
  Content-Type: multipart/form-data
  Fields:
    accountName = "arvital"
    scope       = "arvital"
    returnUrl   = "https://tiendaonline.vital.com.ar/"
    callbackUrl = "https://tiendaonline.vital.com.ar/api/vtexid/oauth/finish?popup=false"
    user        = <email>
  Response: 200, sets _vss session cookie (10-min expiry)

Step 2 — validate:
  POST /api/vtexid/pub/authentication/classic/validate
  Content-Type: multipart/form-data
  Fields:
    login       = <email>
    password    = <password>
    recaptcha   = ""   ← not enforced, send empty
    fingerprint = ""   ← send empty
    recaptchaToken = "" ← send empty
  Response: 200, sets VtexIdclientAutCookie_arvital (24h expiry)

The VtexIdclientAutCookie_arvital cookie is carried automatically by httpx AsyncClient
for all subsequent API requests. No need to set Authorization headers manually.

Success check: validate response body contains authStatus = "Success"
  {"authStatus": "Success"} → authenticated
  {"authStatus": "WrongCredentials"} → check email/password

Recaptcha: NOT enforced on this store (field empty in observed requests).

## VTEX API endpoints

### Product search (primary)
GET /api/catalog_system/pub/products/search?_from=N&_to=M&fq=C:/category-id/

- _from/_to: 0-based range, max 50 per request (VTEX hard limit)
- fq=C:/id/: filter by category tree ID
- Returns: JSON array of product objects

### Category tree
GET /api/catalog_system/pub/category/tree/N
- N = depth (3 covers top → subcategory → leaf)
- Returns full category tree with IDs and names
- Use to discover all leaf category IDs for scraping

### Product count per category
GET /api/catalog_system/pub/products/search?_from=0&_to=0&fq=C:/category-id/
- Check X-VTEX-Resources-Info response header → "resources: 0-0/TOTAL"
- Use total to calculate number of pages needed

### Alternative: search by department slug
GET /api/catalog_system/pub/products/search?_from=0&_to=49&map=c&q=*
- Searches all products — may need category filter to avoid hitting limits

## VTEX product JSON structure (standard)
{
  "productId": "100694",
  "productName": "Atun S&P desmenuzado al natural 170 gr",
  "link": "https://tiendaonline.vital.com.ar/atun-s-p-desmenuzado-al-natural-170-gr-100694/p",
  "categories": ["/Almacen/Conservas/"],
  "items": [                          ← SKUs (variants)
    {
      "itemId": "410577",             ← internal item ID
      "name": "Atun S&P 170gr",
      "referenceId": [{"Key": "RefId", "Value": "7790000123456"}],  ← EAN if present
      "sellers": [
        {
          "commertialOffer": {
            "Price": 899.01,          ← float, not string
            "ListPrice": 899.01,
            "IsAvailable": true,
            "AvailableQuantity": 100
          }
        }
      ]
    }
  ]
}

Key fields:
- Price: float (not string, not Argentine format) — use directly
- referenceId[0].Value: EAN/barcode if store provides it — check on live response
- IsAvailable: bool
- categories[0]: full category path string

## Category structure
- Top level: /almacen, /limpieza, /perfumeria, /bebidas, /kiosco, /bebes-y-ninos, /electro
- Category tree API returns IDs needed for search filtering

## Pagination
- Max 50 products per request (_from=0&_to=49, _from=50&_to=99, etc.)
- Check total from X-VTEX-Resources-Info header or first response length
- Stop when returned array length < 50

## Price format
- API returns float directly: 899.01, 2599.00
- No string parsing needed — use value directly
- Display on site shows Argentine format but API is clean

## VTEX Intelligent Search API (confirmed working — no auth)

GET /_v/api/intelligent-search/product_search
  params: count=50 (max), page=1..50 (hard cap), no auth needed
  Total products: 6753
  Pagination cap: page 50 → max 2500 accessible per unfiltered call

Product structure in IS API (differs slightly from catalog API docs):
  item.ean: string (e.g. "7798013100697") — REAL EAN, directly on item ← USE THIS for SKU
  item.referenceId[0].Value: "0146255" — internal reference, same as productId with leading zero
  item.itemId: int → str — same as productId (single-variant products)
  commertialOffer.Price: int (not float) — still safe to cast with float()
  commertialOffer.ListPrice: int
  commertialOffer.IsAvailable: None — NOT present in IS API
  commertialOffer.AvailableQuantity: int — use > 0 for stock check
  product.link: "/salame-tipo-fuet-cagnoli-x150gr-146255/p" — relative, prepend BASE

IS API selectedFacets filtering:
  DOES NOT WORK without a text query — always returns 6753
  DOES work with query="<text>" + selectedFacets=category-N,<slug>
  Not usable for category-based scraping without auth

## Scraping approach — SUPERSEDED (HTML __STATE__, 2026-03-10)

> Replaced by IS API + regionId. Problems:
> - Used retail IS simulation prices — wrong for ~70% of products
> - ~28% of products appeared with phantom prices that showed $0.00 in browser
> HTML __STATE__ parsing from VTEX VTIO category pages worked for product discovery
> but price data was unreliable.

## Scraping approach — FINAL (IS API + regionId, confirmed working 2026-03-10)

VTEX Intelligent Search API with business regionId.
  - regionId = U1cjYXJ2aXRhbGxw (decodes to "SW#arvitallp" — fixed regional pickup point)
  - Without regionId: IS API returns 6752 products with retail/simulation prices
  - With regionId: IS API returns 4858 products with correct business prices
  - Products with no business price are excluded automatically ($0.00 ghost products gone)
  - count=100, pages 1-49 → covers all 4858 products in 49 requests
  - vtex_segment cookie must be injected after login (base64 JSON with regionId set)
  - Homepage GET required before login to establish vtex_session cookie

IS API endpoint:
  GET /_v/api/intelligent-search/product_search
  params: count=100, page=1..49, regionId=U1cjYXJ2aXRhbGxw

Product counts (verified 2026-03-10):
  Total available (business): 4858 of 6752 retail products

Category per product:
  product.categories[0].strip("/").split("/")[-1]
  e.g. ["/Kiosco/Salame/", "/Kiosco/"] → "Salame"

Pricing:
  price_unit = commertialOffer.Price (per-unit, int in response, cast to float)
  price_bulk = Installments[0].Value (bulk box total = Price × unitMultiplier)
  When unitMultiplier=1, price_bulk == price_unit (correct, some products sell by the unit)

Apollo cache key patterns (pid = numeric product ID):
  Product:sp-{pid}                       → productName, link, categories
  Product:sp-{pid}.items({"filter":"ALL"}).0  → itemId, ean, unitMultiplier, measurementUnit
  $Product:sp-{pid}.items({"filter":"ALL"}).0.sellers.0.commertialOffer
                                          → Price, AvailableQuantity
  $Product:sp-{pid}.items({"filter":"ALL"}).0.sellers.0.commertialOffer
    .Installments({"criteria":"MAX_WITHOUT_INTEREST"}).0  → Value (bulk total)

## Notes
- Product URL: BASE + product.link (relative) e.g. https://tiendaonline.vital.com.ar/slug-id/p
- EAN is clean on most products — use as SKU; fallback to itemId
- VTEX rate limits: generous, concurrency=8 safe
- No SSL issues
- Login required (business account) — sets VtexIdclientAutCookie_arvital
