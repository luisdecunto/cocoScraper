"""
Luvik supplier.
URL: https://tiendaluvik.com.ar/
Platform: Shopify
Approach: httpx JSON API (/products.json) — no HTML parsing
Auth: none (public store — verified 2026-03-10: 30 products visible guest in girasol collection)
Price format: standard decimal string ("63480.00") — parse with float()
SKU: Shopify variant ID — unique per variant, stable over time.
     (Previously used v["sku"] 6-digit internal code, but supplier has duplicates causing overwrites.)
"""

import asyncio
import logging

import httpx
from bs4 import BeautifulSoup

from scraper.suppliers.base import BaseSupplier

logger = logging.getLogger(__name__)

BASE = "https://tiendaluvik.com.ar"
PAGE_SIZE = 250  # Shopify hard limit per page
# DB column is NUMERIC(12,2) — max 9_999_999_999.99. Some Luvik products have corrupt
# prices (e.g. 3e15) that would overflow. Treat any price >= this as invalid data.
_MAX_VALID_PRICE = 9_999_999_999.99


class LuvikSupplier(BaseSupplier):
    """Supplier implementation for Luvik (tiendaluvik.com.ar) — Shopify JSON API."""

    async def login(self, client: httpx.AsyncClient) -> None:
        """
        Luvik is a public store — no login required.
        All prices and products are visible to guest users.
        Credentials env vars are reserved for future use if the store goes private.
        """
        logger.info("Luvik: public store — no login required")

    async def discover_categories(self, client: httpx.AsyncClient) -> list[str]:
        """
        Parse all /collections/<slug> hrefs from the homepage nav.
        Returns collection products.json API URLs directly.
        Excludes generic Shopify pseudo-collections: all, vendors, types.
        """
        r = await client.get(f"{BASE}/")
        r.raise_for_status()

        soup = BeautifulSoup(r.text, "lxml")
        slugs: set[str] = set()
        for a in soup.select("a[href*='/collections/']"):
            href = a.get("href", "")
            parts = href.strip("/").split("/")
            if (
                len(parts) == 2
                and parts[0] == "collections"
                and parts[1] not in ("all", "vendors", "types")
            ):
                slugs.add(parts[1])

        urls = [f"{BASE}/collections/{slug}/products.json" for slug in sorted(slugs)]
        logger.info(f"Luvik: discovered {len(urls)} collections")
        return urls

    async def scrape_category(
        self,
        client: httpx.AsyncClient,
        url: str,
        sem: asyncio.Semaphore,
    ) -> list[dict]:
        """
        Fetch all pages from a collection's products.json endpoint.

        url format: https://tiendaluvik.com.ar/collections/<slug>/products.json
        Pagination: ?limit=250&page=N — stop when products array is empty or < PAGE_SIZE.
        """
        results: list[dict] = []
        page = 1
        # Derive human-readable category name from the slug
        slug = url.split("/collections/")[1].replace("/products.json", "")
        category = slug.replace("-", " ").title()

        while True:
            page_url = f"{url}?limit={PAGE_SIZE}&page={page}"
            try:
                async with sem:
                    await asyncio.sleep(0.3)
                    r = await client.get(page_url)
                r.raise_for_status()
            except httpx.HTTPError as e:
                logger.warning(f"Luvik: HTTP error on {page_url} — {e}")
                break

            products = r.json().get("products", [])
            if not products:
                break

            for p in products:
                results.extend(self._parse_product(p, category))

            if len(products) < PAGE_SIZE:
                break
            page += 1

        logger.debug(f"Luvik [{category}]: {len(results)} products across {page} page(s)")
        return results

    def parse_price(self, raw: str) -> float | None:
        """
        Standard decimal string — no Argentine formatting needed.
        Returns None for corrupt/overflow values (some Luvik catalog entries have
        prices in the quadrillions, e.g. SKU 61354).
        """
        try:
            value = float(raw) if raw else None
            if value is not None and value > _MAX_VALID_PRICE:
                logger.warning(f"Luvik: ignoring corrupt price {raw!r} (exceeds NUMERIC(12,2))")
                return None
            return value
        except (ValueError, TypeError):
            return None

    # ------------------------------------------------------------------ #

    def _parse_product(self, p: dict, category: str) -> list[dict]:
        """
        Expand all variants into separate product rows.
        Each variant has its own SKU, price, and availability.

        Confirmed JSON structure (verified 2026-03-10):
          p["title"]              → product name
          p["handle"]             → URL slug
          p["vendor"]             → brand name (not stored — not in schema)
          v["id"]                 → Shopify variant ID (unique, stable) — used as SKU
          v["sku"]                → 6-digit internal supplier code (has duplicates, not used)
          v["price"]              → decimal string e.g. "63480.00"
          v["compare_at_price"]   → same as price when no discount
          v["available"]          → bool — inventory_quantity not present in API response
          v["title"]              → "Default Title" for single-variant products
        """
        results: list[dict] = []
        base_name = p.get("title", "")
        handle = p.get("handle", "")

        for v in p.get("variants", []):
            try:
                variant_title = v.get("title", "")
                # "Default Title" is a Shopify placeholder — don't append it
                if variant_title and variant_title != "Default Title":
                    name = f"{base_name} — {variant_title}"
                else:
                    name = base_name

                # Use Shopify variant ID as SKU (unique per variant, stable over time)
                sku = str(v["id"])
                price_unit = self.parse_price(v.get("price"))
                stock = "disponible" if v.get("available") else "sin stock"

                results.append({
                    "sku":        sku,
                    "name":       name,
                    "url":        f"{BASE}/products/{handle}",
                    "category":   category,
                    "price_unit": price_unit,
                    "price_bulk": None,   # Luvik has no bulk/retail price split
                    "stock":      stock,
                })
            except Exception as e:
                logger.warning(
                    f"Luvik: failed to parse variant {v.get('id')} "
                    f"of product {p.get('id')} — {e}"
                )

        return results
