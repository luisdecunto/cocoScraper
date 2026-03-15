# Maxiconsumo — Recon Notes

## Basic info
- URL: https://maxiconsumo.com/sucursal_moreno/
- Platform: Magento 2
- Login required: yes (supplier-tier "categorizado" pricing)

## Scraping approach
- httpx + BeautifulSoup (static HTML, no JS rendering needed)
- JS disabled warning on page is cosmetic — full HTML is served without JS

## Auth flow
- GET login page → extract input[name="form_key"] value
- POST to /customer/account/loginPost/ with form_key + credentials
- Success: redirects to account page
- Failure: redirects back to login page (no error thrown — must check URL)

## Price format
- Argentine: $1.234,56 (dot = thousands, comma = decimal)
- Two prices per product: bulk (closed box) and unit

## Category structure
- All leaf categories visible in nav menu on homepage
- Leaf URL pattern: /sucursal_moreno/<cat>/<subcat>/<leaf>.html (4+ slashes)
- Pagination: ?product_list_limit=96&p=N

## CSS selectors (verified via selector_debug.py)
- product_item:  .product-item
- name:          .product-item-link
- sku:           .product-sku  (NOT .product-item-sku — that class doesn't exist)
- prices:        .price-including-tax .price  → [0]=bulk con IVA, [1]=unit con IVA
                 (.price-box .price returns 3 spans: bulk-IVA, bulk-no-IVA hidden, unit)
- stock:         .stock-status  (NOT .stock — that class doesn't exist)
- next_page:     a[title="Siguiente"]

## Notes
- Branch: sucursal_moreno — prices are branch-specific
- Price tiers: guest / consumidor final / categorizado
  We scrape "categorizado" (authenticated supplier tier)
