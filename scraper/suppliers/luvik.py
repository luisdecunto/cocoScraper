"""
Luvik supplier.
URL: https://tiendaluvik.com.ar/
Platform: Shopify
Approach: HTML collection pages — scrapes per-unit prices and bulk sizes directly.
  Each product card shows:
    - "$X.XXX,xx x UN"  → price per individual unit  (stored as price_unit)
    - "N | Unidades por bulto"  → units per closed box  (stored as units_per_package)
    - "$XX.XXX,xx" at end of card  → bulk/case price  (stored as price_bulk)
  Previously used JSON API (/products.json) which only provided the bulk price with
  no way to derive the per-unit price without fetching each product page separately.
Auth: none (public store — prices visible to guests)
SKU: Shopify variant ID extracted from ?variant=ID in card links.
"""

import asyncio
import logging
import re

import httpx
from bs4 import BeautifulSoup

from scraper.suppliers.base import BaseSupplier

logger = logging.getLogger(__name__)

BASE = "https://tiendaluvik.com.ar"
_MAX_VALID_PRICE = 9_999_999_999.99
_PAGE_SIZE = 12  # Luvik collection pages show 12 products per page

# Collections to always exclude — these are UI/marketing pages, not product lists.
_EXCLUDE_SLUGS = {"all", "frontpage", "nuevos-ingresos", "ofertas", "liquidacion"}


class LuvikSupplier(BaseSupplier):
    """Supplier implementation for Luvik (tiendaluvik.com.ar) — HTML collection pages."""

    async def login(self, client: httpx.AsyncClient) -> None:
        """Luvik is a public store — no login required."""
        logger.info("Luvik: public store — no login required")

    async def discover_categories(self, client: httpx.AsyncClient) -> list[str]:
        """
        Discover all collection URLs by scraping the sitemap.
        Uses /sitemap.xml to get a complete, deduplicated list of collection slugs.
        Falls back to the nav if sitemap is unavailable.
        Excludes known non-product collections (all, frontpage, ofertas, etc.).
        DB upsert handles any overlapping products across sub-collections.
        """
        slugs: set[str] = set()

        # Primary: sitemap.xml lists every collection
        try:
            r = await client.get(f"{BASE}/sitemap.xml")
            r.raise_for_status()
            # Collection URLs appear as https://tiendaluvik.com.ar/collections/<slug>
            found = re.findall(r"/collections/([a-z0-9][a-z0-9\-]*[a-z0-9])", r.text)
            slugs.update(found)
            logger.info(f"Luvik: found {len(slugs)} slugs in sitemap")
        except httpx.HTTPError as e:
            logger.warning(f"Luvik: sitemap fetch failed ({e}), falling back to nav")

        # Fallback: scrape the nav links
        if not slugs:
            try:
                r = await client.get(BASE)
                r.raise_for_status()
                soup = BeautifulSoup(r.text, "lxml")
                for a in soup.select('a[href*="/collections/"]'):
                    m = re.search(r"/collections/([a-z0-9][a-z0-9\-]*[a-z0-9])", a["href"])
                    if m:
                        slugs.add(m.group(1))
                logger.info(f"Luvik: found {len(slugs)} slugs in nav")
            except httpx.HTTPError as e:
                logger.error(f"Luvik: nav fallback also failed — {e}")

        slugs -= _EXCLUDE_SLUGS
        urls = sorted(f"{BASE}/collections/{slug}" for slug in slugs)
        logger.info(f"Luvik: {len(urls)} collections to scrape")
        return urls

    async def scrape_category(
        self,
        client: httpx.AsyncClient,
        url: str,
        sem: asyncio.Semaphore,
    ) -> list[dict]:
        """
        Fetch all pages from a collection HTML page.

        url format: https://tiendaluvik.com.ar/collections/<slug>
        Pagination: ?page=N — stop when no li.grid__item cards are found.
        Per-unit price, units_per_bulk, and case price are all visible in each card.
        """
        results: list[dict] = []
        page = 1
        slug = url.rstrip("/").split("/collections/")[-1]
        category = slug.replace("-", " ").title()

        while True:
            page_url = f"{url}?page={page}" if page > 1 else url
            r = None
            for attempt in range(3):
                try:
                    async with sem:
                        await asyncio.sleep(1.5)
                        r = await client.get(page_url)
                    if r.status_code == 429:
                        wait = 10 * (attempt + 1)
                        logger.warning(f"Luvik: 429 on {page_url} — waiting {wait}s (attempt {attempt+1}/3)")
                        await asyncio.sleep(wait)
                        r = None
                        continue
                    r.raise_for_status()
                    break
                except httpx.HTTPError as e:
                    logger.warning(f"Luvik: HTTP error on {page_url} — {e}")
                    r = None
                    break
            if r is None or r.status_code != 200:
                logger.warning(f"Luvik: giving up on {page_url}")
                break

            soup = BeautifulSoup(r.text, "lxml")
            cards = soup.select("li.grid__item")
            if not cards:
                break

            for card in cards:
                product = self._parse_card(card, category)
                if product:
                    results.append(product)

            if len(cards) < _PAGE_SIZE:
                break
            page += 1

        logger.debug(f"Luvik [{category}]: {len(results)} products across {page - 1} page(s)")
        return results

    def parse_price(self, raw: str) -> float | None:
        """
        Parse Argentine price format: "$1.299,00" → 1299.0
        Dot is thousands separator, comma is decimal.
        Returns None for corrupt/overflow values.
        """
        if not raw:
            return None
        try:
            cleaned = (
                raw.replace("$", "")
                   .replace("\xa0", "")
                   .replace(".", "")
                   .replace(",", ".")
                   .strip()
            )
            value = float(cleaned) if cleaned else None
            if value is not None and value > _MAX_VALID_PRICE:
                logger.warning(f"Luvik: ignoring corrupt price {raw!r}")
                return None
            return value
        except (ValueError, TypeError):
            return None

    # ------------------------------------------------------------------ #

    def _parse_card(self, card: BeautifulSoup, category: str) -> dict | None:
        """
        Extract a product from a collection listing card (li.grid__item).

        Data sources within the card:
          - variant ID (SKU):   a[href*='variant='] → ?variant=(digits)
          - product name:       a[title] on the variant link
          - per-unit price:     first text matching "x UN" pattern
          - units_per_package:  text matching "N | Unidades por bulto" or "xN | Unidades"
          - price_bulk:         price_unit × units_per_package
          - stock:              "Agotado" text present → "sin stock"
        """
        try:
            # SKU = Shopify variant ID from link
            a = card.select_one('a[href*="variant="]')
            if not a:
                return None
            m = re.search(r"variant=(\d+)", a["href"])
            if not m:
                return None
            sku = m.group(1)

            # Product name and URL
            name = (a.get("title") or "").strip()
            if not name:
                name = a.get_text(strip=True)
            product_url = BASE + a["href"].split("?")[0]

            # Card text for pattern matching
            text = card.get_text(separator="|", strip=True)

            # Per-unit price from "x UN" label
            price_unit: float | None = None
            for el in card.find_all(string=re.compile(r"x\s*UN", re.IGNORECASE)):
                raw = re.sub(r"\s*x\s*UN.*", "", str(el), flags=re.IGNORECASE).strip()
                price_unit = self.parse_price(raw)
                if price_unit:
                    break

            # Units per bulk package
            units_per_package: int | None = None
            mu = re.search(r"\|(\d+)\|Unidades por bulto", text, re.IGNORECASE)
            if mu:
                units_per_package = int(mu.group(1))
            else:
                mu2 = re.search(r"x(\d+)\|Unidades\b", text, re.IGNORECASE)
                if mu2:
                    units_per_package = int(mu2.group(1))

            # Bulk price = per-unit × units_per_package
            price_bulk: float | None = None
            if price_unit and units_per_package and units_per_package > 1:
                price_bulk = round(price_unit * units_per_package, 2)

            # Stock status
            stock = "sin stock" if "Agotado" in text else "disponible"

            return {
                "sku":              sku,
                "name":             name,
                "url":              product_url,
                "category":         category,
                "price_unit":       price_unit,
                "price_bulk":       price_bulk,
                "stock":            stock,
                "units_per_package": units_per_package,
            }

        except Exception as e:
            logger.warning(f"Luvik: failed to parse card — {e}")
            return None
