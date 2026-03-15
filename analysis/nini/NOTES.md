# Nini — Recon Notes

## Basic info
- URL: http://ecommerce.nini.com.ar:8081/ventas.online/
- Platform: Custom ASP.NET + Node.js (Java/Wicket-style frontend URLs)
- Login required: yes
- Non-standard port: 8081
- HTTP only (no HTTPS)

## Scraping approach decision
[x] httpx JSON API — custom /nodejs/ DAO endpoint
Reason: all data served via XHR POST requests to /nodejs/<dao>/<method>.
No HTML parsing needed.

## Auth flow

### Step 1 — ValidateUser (sets .ASPXAUTH cookie)
GET http://ecommerce.nini.com.ar:8081/ventas.administracion/Account/ValidateUser
Query params:
  userName     = <username>     e.g. "12511"
  password     = <password>
  callback     = _jqjsp         literal JSONP callback name
  _<timestamp> =                cache-buster: current epoch ms as key, empty value

Response: JSONP — _jqjsp({"Rol":"3","Zone":10000002})
  Parse Zone: re.search(r'\((.+)\)', text) → json.loads → data["Zone"]
  set-cookie: .ASPXAUTH (24h expiry) — carried automatically by httpx

### Step 2 — getUnique (fetch sellerId)
POST http://ecommerce.nini.com.ar:8081/nodejs/onlineUserDao/getUnique
Content-Type: application/x-www-form-urlencoded
Payload:
  daoName  = onlineUserDao
  method   = getUnique
  params[] = <username>

Response: [{userName, sellerId, lastLogin, roleType_id}]
  sellerId → required for all product requests
  userName → same as login username

### Step 3 — fetch active order ID
POST http://ecommerce.nini.com.ar:8081/nodejs/onlineOrderDao/findByClientId
Content-Type: application/x-www-form-urlencoded
Payload:
  daoName              = onlineOrderDao
  method               = findByClientId
  params[clientId]     = <username>
  params[sellerId]     = <username>      (same as clientId for this account)
  params[isClient]     = true
  params[userName]     = <username>
  params[zone]         = <zone>          (from ValidateUser response)
  params[quotaSellerId]= <username>      (same as sellerId)

Response: array of order objects, full order history, newest first.
Active order = first element where orderEndDate is None (and status == 1).
Filter: next(o for o in orders if o["orderEndDate"] is None)
Extract: order["id"]  → string e.g. "3616162"

## API endpoints

### Departments (hardcoded — confirmed from UI)
| Department    | departamentId |
|---------------|---------------|
| Almacen       | 210           |
| Anexo         | 220           |
| Bebidas       | 230           |
| Golosinas     | 240           |
| Limpieza      | 250           |
| Mascotas      | 260           |
| Perfumeria    | 270           |
| Refrigerados  | 290           |

### Subcategories (sectors) per department
POST http://ecommerce.nini.com.ar:8081/nodejs/onlineSectorDao/findFacets
Content-Type: application/x-www-form-urlencoded
Key params:
  daoName = onlineSectorDao
  method  = findFacets
  params[filter][departamentId] = <departamentId>
  params[filter][sectorId] = null
  params[filter][onlypaquete] = true
  params[filter][currentOrder][id] = <orderId>
  params[sellerId] = 336
  params[isClient] = true
  params[userName] = 12511
  params[zone] = 10000002
  params[quotaSellerId] = 12511

Response: [{id, description, cant}]
  id → sectorId for product requests

### Products per subcategory (paginated)
POST http://ecommerce.nini.com.ar:8081/nodejs/onlineProductDao/findAllWithOrder
Content-Type: application/x-www-form-urlencoded
Key params:
  daoName = onlineProductDao
  method  = findAllWithOrder
  params[filter][departamentId] = <departamentId>
  params[filter][sectorId] = <sectorId>
  params[filter][onlypaquete] = true
  params[filter][currentOrder][id] = <orderId>
  params[filter][offsetProducts] = <offset>   ← 0, 50, 100, ...
  params[filter][limit] = 50
  params[filter][withStock] = true
  params[filter][buyArticles][] = -1
  params[sellerId] = 336
  params[isClient] = true
  params[userName] = 12511
  params[zone] = 10000002
  params[quotaSellerId] = 12511

Pagination: offsetProducts += 50 until len(response) < 50
Total: products[0]["totalProducts"] tells you the full count upfront

## Product JSON structure (confirmed)
{
  "totalProducts": 70,
  "id": "4978862",              ← internal product ID — use as SKU
  "smallDescription": "OLIOVITA  Aceite Oliva E/V Pet",
  "largeDescription": "OLIOVITA  Aceite Oliva E/V Pet 500 Ml",
  "price": 9069.09,             ← float, use directly
  "stock": "125",               ← string, parse to int
  "unidsPerPackage": "12",      ← units per box
  "trademark": "OLIOVITA",      ← brand
  "departamentId": "210",
  "sectorId": "210010",
  "lineId": "210010040",
  "RowNum": "51"                ← 1-based row number across all pages
}

Key fields:
- id: internal product ID — no EAN available, use as SKU
- price: float, no Argentine formatting needed
- stock: string integer
- unidsPerPackage: units per closed box — useful as bulk info
- priceWithTax: obfuscated string — ignore, use price directly

## Session parameters (from observed requests)
sellerId: 336
userName: 12511
zone: 10000002
quotaSellerId: 12511
These come from the getUnique response. Must be captured dynamically after login.

## Notes
- HTTP (not HTTPS) — no SSL issues
- Non-standard port 8081 — make sure httpx doesn't strip it
- orderId changes per session — must be fetched after login, not hardcoded
- onlypaquete=true → returns only package/bulk products (what the client wants)
- withStock=true → filters to in-stock items only (use for efficiency)
- The complex browser navigation (create order, continue, etc.) is frontend-only
  The API works with just .ASPXAUTH cookie + correct params — no need to simulate clicks
