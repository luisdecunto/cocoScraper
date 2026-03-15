"""
Maxiconsumo supplier.
URL: https://maxiconsumo.com/sucursal_moreno/
Platform: Magento 2
Approach: httpx + BeautifulSoup, static HTML
Auth: Magento form login (supplier-tier "categorizado" pricing)
Price format: Argentine ($1.234,56 -> 1234.56)
"""

import asyncio
import logging
import os
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from scraper.suppliers.base import BaseSupplier

logger = logging.getLogger(__name__)


class MaxiconsumoSupplier(BaseSupplier):
    """Scraper for maxiconsumo.com/sucursal_moreno — Magento 2, auth required."""

    async def login(self, client: httpx.AsyncClient) -> None:
        """Authenticate via Magento form login. Skips login if credentials are not set."""
        username = os.getenv(self.config["credentials_env"]["username"])
        password = os.getenv(self.config["credentials_env"]["password"])

        if not username or not password:
            logger.warning(
                "Maxiconsumo: credentials not set — scraping as guest. "
                "Prices will be public (non-categorizado). "
                "Set MAXICONSUMO_USER and MAXICONSUMO_PASS in .env for supplier-tier pricing."
            )
            return

        r = await client.get(self.config["login_page_url"])
        soup = BeautifulSoup(r.text, "lxml")

        form_key_el = soup.select_one('input[name="form_key"]')
        if not form_key_el:
            raise RuntimeError("Maxiconsumo: could not find form_key on login page")
        form_key = form_key_el["value"]

        resp = await client.post(
            self.config["login_post_url"],
            data={
                "form_key": form_key,
                "login[username]": username,
                "login[password]": password,
            },
            follow_redirects=True,
        )

        if "customer/account/login" in str(resp.url):
            raise RuntimeError(
                "Maxiconsumo: login failed — check MAXICONSUMO_USER and MAXICONSUMO_PASS in .env"
            )
        logger.info("Maxiconsumo: authenticated successfully")

    async def discover_categories(self, client: httpx.AsyncClient) -> list[str]:
        """Parse all leaf category URLs from the homepage nav."""
        r = await client.get(self.config["base_url"])
        soup = BeautifulSoup(r.text, "lxml")

        urls = set()
        for a in soup.select("nav a[href], .navigation a[href]"):
            href = a.get("href", "")
            path = urlparse(href).path
            # Leaf categories: end in .html and have 4+ path segments
            if path.endswith(".html") and path.count("/") >= 4:
                urls.add(href)

        logger.info(f"Maxiconsumo: discovered {len(urls)} category URLs")
        return sorted(urls)

    async def scrape_category(
        self,
        client: httpx.AsyncClient,
        url: str,
        sem: asyncio.Semaphore,
    ) -> list[dict]:
        """Scrape all products from a category, following pagination."""
        results = []
        page = 1
        category = self._extract_category(url)

        while True:
            page_url = f"{url}?product_list_limit=96&p={page}"
            try:
                async with sem:
                    await asyncio.sleep(0.3)
                    r = await client.get(page_url)
                r.raise_for_status()
            except httpx.HTTPError as e:
                logger.warning(f"Maxiconsumo: HTTP error on {page_url} — {e}")
                break

            soup = BeautifulSoup(r.text, "lxml")
            items = soup.select(self.config["selectors"]["product_item"])

            if not items:
                break

            for item in items:
                product = self._parse_product(item, category)
                if product:
                    results.append(product)

            if not soup.select_one(self.config["selectors"]["next_page"]):
                break
            page += 1

        return results

    def parse_price(self, raw: str) -> float | None:
        """Delegate to Argentine price parser."""
        return self._parse_argentine_price(raw)

    # ------------------------------------------------------------------ #
    # Private helpers                                                      #
    # ------------------------------------------------------------------ #

    def _parse_product(self, item, category: str) -> dict | None:
        """Extract product data from a .product-item element."""
        try:
            sel = self.config["selectors"]

            name_el   = item.select_one(sel["name"])
            sku_el    = item.select_one(sel["sku"])
            price_els = item.select(sel["prices"])
            stock_el  = item.select_one(sel["stock"])

            if not name_el or not sku_el:
                return None

            name = name_el.get_text(strip=True)
            # SKU element contains "SKU 304" — strip the label
            sku  = sku_el.get_text(strip=True).replace("SKU", "").strip()
            url  = name_el.get("href", "")

            # prices selector returns only .price-including-tax .price:
            # [0] = bulk price (precio por bulto cerrado, con IVA)
            # [1] = unit price (precio unitario, con IVA)
            price_bulk = self.parse_price(price_els[0].get_text()) if len(price_els) > 0 else None
            price_unit = self.parse_price(price_els[1].get_text()) if len(price_els) > 1 else None

            stock = stock_el.get_text(strip=True) if stock_el else "unknown"

            return {
                "sku":        sku,
                "name":       name,
                "url":        url,
                "category":   category,
                "price_unit": price_unit,
                "price_bulk": price_bulk,
                "stock":      stock,
            }

        except Exception as e:
            logger.warning(f"Maxiconsumo: failed to parse product — {e}")
            return None

    def _extract_category(self, url: str) -> str:
        """Extract a readable category string from a category URL."""
        path = urlparse(url).path
        parts = [p for p in path.split("/") if p and p != "sucursal_moreno"]
        return " > ".join(parts).replace(".html", "").replace("-", " ").title()
