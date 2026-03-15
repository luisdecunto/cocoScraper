"""
Vital supplier.
URL: https://tiendaonline.vital.com.ar/
Platform: VTEX (VTIO storefront)
Approach: VTEX Intelligent Search API with regionId for business pricing.
  regionId = U1cjYXJ2aXRhbGxw  (decodes to "SW#arvitallp" — fixed regional pickup point)
  With regionId, IS API returns only the ~4858 products that have a business price.
  Products with no business price (would show $0.00 in browser) are excluded automatically.
  Pagination: count=100, pages 1-49 (covers all ~4858 products).
Auth: Required — VTEX two-step login (business account).
  Homepage GET required first to establish vtex_session cookie.
  vtex_segment cookie is constructed with regionId and injected after login.
Price:
  price_unit = commertialOffer.Price       (per-unit business price)
  price_bulk = Installments[0].Value       (bulk box total = Price × unitMultiplier)
             fallback: price_unit when Installments absent (unitMultiplier=1 assumed)
SKU: item.ean (EAN barcode). Fallback: item.itemId.
Category: derived from product.categories[-1] (top-level), e.g. ["/Kiosco/Salame/", "/Almacen/"] → "Almacen".
"""

import asyncio
import base64
import json
import logging
import os

import httpx

from scraper.suppliers.base import BaseSupplier

logger = logging.getLogger(__name__)

BASE      = "https://tiendaonline.vital.com.ar"
IS_URL    = f"{BASE}/_v/api/intelligent-search/product_search"
REGION_ID = "U1cjYXJ2aXRhbGxw"   # SW#arvitallp — business account regional pickup point
IS_COUNT  = 100                    # IS API max per page
IS_MAX_PAGES = 50                  # IS API hard page cap

# vtex_segment cookie with business regionId — constructed once at module level
_SEGMENT_PAYLOAD = {
    "campaigns": None, "channel": "1", "priceTables": None,
    "regionId": REGION_ID,
    "utm_campaign": None, "utm_source": None, "utmi_campaign": None,
    "currencyCode": "ARS", "currencySymbol": "$", "countryCode": "ARG",
    "cultureInfo": "es-AR", "admin_cultureInfo": "es-AR",
    "channelPrivacy": "private",
}
_VTEX_SEGMENT = base64.b64encode(
    json.dumps(_SEGMENT_PAYLOAD, separators=(",", ":")).encode()
).decode()


class VitalSupplier(BaseSupplier):
    """Scrapes tiendaonline.vital.com.ar via VTEX IS API with business regionId."""

    async def login(self, client: httpx.AsyncClient) -> None:
        """
        VTEX login sequence:
        Step 1: GET homepage     — establishes vtex_session cookie.
        Step 2: startlogin POST  — sets _vss cookie.
        Step 3: validate POST    — authenticates, sets VtexIdclientAutCookie_arvital.
        Step 4: inject vtex_segment with business regionId so IS API returns business prices.
        """
        email    = os.getenv(self.config["credentials_env"]["username"])
        password = os.getenv(self.config["credentials_env"]["password"])

        await client.get(BASE)

        await client.post(
            f"{BASE}/api/vtexid/pub/authentication/startlogin",
            data={
                "accountName": "arvital",
                "scope":       "arvital",
                "returnUrl":   f"{BASE}/",
                "callbackUrl": f"{BASE}/api/vtexid/oauth/finish?popup=false",
                "user":        email,
            },
        )

        resp = await client.post(
            f"{BASE}/api/vtexid/pub/authentication/classic/validate",
            data={
                "login":          email,
                "password":       password,
                "recaptcha":      "",
                "fingerprint":    "",
                "recaptchaToken": "",
            },
        )

        body = resp.json()
        if body.get("authStatus") != "Success":
            raise RuntimeError(
                f"Vital: login failed — authStatus={body.get('authStatus')}. "
                "Check VITAL_USER and VITAL_PASS."
            )

        client.cookies.set("vtex_segment", _VTEX_SEGMENT, domain="tiendaonline.vital.com.ar")
        logger.info("Vital: authenticated and regionId injected")

    async def discover_categories(self, client: httpx.AsyncClient) -> list[str]:
        """
        Returns a single sentinel URL — all products are fetched in one IS API pass.
        Category is derived per-product from the IS API response.
        """
        return ["_api_"]

    async def scrape_category(
        self,
        client: httpx.AsyncClient,
        url: str,
        sem: asyncio.Semaphore,
    ) -> list[dict]:
        """
        Fetch all business-priced products from the IS API using regionId.
        Paginates pages 1–49 at count=100, covering all ~4858 available products.
        The url parameter is ignored (sentinel "_api_").
        """
        results = []

        for page in range(1, IS_MAX_PAGES):
            try:
                async with sem:
                    await asyncio.sleep(0.3)
                    r = await client.get(IS_URL, params={
                        "count":    IS_COUNT,
                        "page":     page,
                        "regionId": REGION_ID,
                    })
                r.raise_for_status()
            except httpx.HTTPError as exc:
                logger.warning(f"Vital: HTTP error on IS API page {page} — {exc}")
                break

            products = r.json().get("products", [])
            if not products:
                break

            for p in products:
                try:
                    row = self._extract_product(p)
                    if row:
                        results.append(row)
                except Exception as exc:
                    logger.warning(f"Vital: failed to parse product {p.get('productId')} — {exc}")

            logger.info(f"Vital: page {page} done — {len(products)} fetched, {len(results)} total")

        logger.info(f"Vital: IS API scrape complete — {len(results)} products")
        return results

    def parse_price(self, raw) -> float | None:
        """VTEX IS API returns prices as int/float — cast to float."""
        try:
            return float(raw) if raw is not None else None
        except (ValueError, TypeError):
            return None

    # ------------------------------------------------------------------ #
    # Internal helpers

    def _extract_product(self, p: dict) -> dict | None:
        """
        Extract a single product row from an IS API product object.
        Returns None if critical fields are missing.
        """
        item  = p.get("items", [{}])[0]
        offer = item.get("sellers", [{}])[0].get("commertialOffer", {})

        sku = item.get("ean") or item.get("itemId")
        if not sku:
            return None

        cats     = p.get("categories", [])
        cat_name = cats[-1].strip("/").split("/")[-1] if cats else ""

        price_unit = self.parse_price(offer.get("Price"))
        insts      = offer.get("Installments", [])
        price_bulk = self.parse_price(insts[0].get("Value")) if insts else None
        if price_bulk is None:
            price_bulk = price_unit

        return {
            "sku":        str(sku),
            "name":       p.get("productName", ""),
            "url":        BASE + p.get("link", ""),
            "category":   cat_name,
            "price_unit": price_unit,
            "price_bulk": price_bulk,
            "stock":      "disponible",  # regionId excludes price=0 products automatically
        }
