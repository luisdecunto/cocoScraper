"""
Santa Maria supplier.
URL: https://tienda.santamariasa.com.ar/comercio/
Platform: osCommerce (classic PHP, custom theme)
Approach: httpx + BeautifulSoup, static HTML
Auth: session-based form login (cookie carried automatically by httpx)
SSL: verify=False (site has incomplete cert chain)

Product listing structure (verified 2026-03-10):
  - Products are in a <div class="productListTable">, rendered as a table via nested <tr> rows.
  - Each product occupies one <tr> with 5 <td>: image | name | UxB | price | cart form.
  - Non-product <tr> rows (nested form rows) have no product_info.php link — skip them.
  - Price cell: "$4446.270  (s/IVA)\n$5379.987  (c/IVA)" — dot is decimal separator.
    price_unit = s/IVA (bulk net price, without tax)
    price_bulk = c/IVA (bulk gross price, with tax)
    Both prices are per-bulk-box. UxB count stored in stock="uxb:N" for post-processing.
    Per-unit price = price / uxb (compute in post-processing, not here).
  - SKU = products_id from URL (numeric, no barcode in listing HTML).
  - Pagination: <a href="...page=N"> with text ">" marks the next page.
"""

import asyncio
import logging
import os
import re
import warnings
from urllib.parse import parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup

from scraper.suppliers.base import BaseSupplier

warnings.filterwarnings("ignore", message="Unverified HTTPS request")
logger = logging.getLogger(__name__)

BASE = "https://tienda.santamariasa.com.ar/comercio"


class SantaMariaSupplier(BaseSupplier):
    """Supplier implementation for Santa Maria (tienda.santamariasa.com.ar)."""

    async def login(self, client: httpx.AsyncClient) -> None:
        """Authenticate via osCommerce form login."""
        r = await client.get(f"{BASE}/login.php")
        soup = BeautifulSoup(r.text, "lxml")

        formid_el = soup.select_one('input[name="formid"]')
        if not formid_el:
            raise RuntimeError("SantaMaria: formid CSRF token not found on login page")

        payload = {
            "formid":        formid_el["value"],
            "email_address": os.getenv(self.config["credentials_env"]["username"]),
            "password":      os.getenv(self.config["credentials_env"]["password"]),
        }

        resp = await client.post(f"{BASE}/login.php?action=process", data=payload)

        if "login.php" in str(resp.url):
            raise RuntimeError(
                "SantaMaria: login failed — check SANTAMARIA_USER and SANTAMARIA_PASS"
            )
        logger.info("SantaMaria: authenticated successfully")

    async def discover_categories(self, client: httpx.AsyncClient) -> list[str]:
        """
        Two-level category discovery:
        1. Homepage → top-level category URLs (cPath=N)
        2. Each top-level page → leaf category URLs (cPath=N_M)
        Leaf URLs only appear after navigating into a top-level category.
        """
        r = await client.get(f"{BASE}/index.php")
        soup = BeautifulSoup(r.text, "lxml")

        # Step 1: collect top-level category URLs (cPath=single int, no underscore)
        skip = set(self.config.get("skip_top_categories", []))
        top_urls: list[str] = []
        seen_top: set[str] = set()
        for a in soup.select("a[href*='cPath=']"):
            href = a.get("href", "")
            m = re.search(r"cPath=(\d+)$", href)
            if m and not re.search(r"cPath=\d+_\d+", href):
                if m.group(1) in skip:
                    continue
                clean = re.sub(r"[&?]?osCsid=[^&]*", "", href).strip("&?")
                full = clean if clean.startswith("http") else f"{BASE}/{clean.lstrip('/')}"
                if full not in seen_top:
                    seen_top.add(full)
                    top_urls.append(full)

        logger.info(f"SantaMaria: found {len(top_urls)} top-level categories, fetching subcategories...")

        # Step 2: visit each top-level category page to collect leaf URLs (cPath=N_M)
        leaf_urls: set[str] = set()
        for top_url in top_urls:
            await asyncio.sleep(0.3)
            try:
                r = await client.get(top_url)
                soup = BeautifulSoup(r.text, "lxml")
                for a in soup.select("a[href*='cPath=']"):
                    href = a.get("href", "")
                    if re.search(r"cPath=\d+_\d+", href):
                        clean = re.sub(r"[&?]?osCsid=[^&]*", "", href).strip("&?")
                        full = clean if clean.startswith("http") else f"{BASE}/{clean.lstrip('/')}"
                        leaf_urls.add(full)
            except httpx.HTTPError as e:
                logger.warning(f"SantaMaria: failed to fetch top-level category {top_url} — {e}")

        logger.info(f"SantaMaria: discovered {len(leaf_urls)} leaf categories")
        return sorted(leaf_urls)

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
        # Build base URL without any page/sort params so we control pagination cleanly
        base_url = re.sub(r"[&?](page|sort)=[^&]*", "", url).strip("&?")

        while True:
            page_url = base_url if page == 1 else f"{base_url}&page={page}"
            try:
                async with sem:
                    await asyncio.sleep(0.3)
                    r = await client.get(page_url)
                r.raise_for_status()

                if "login.php" in str(r.url):
                    logger.error("SantaMaria: session expired mid-scrape")
                    break

            except httpx.HTTPError as e:
                logger.warning(f"SantaMaria: HTTP error on {page_url} — {e}")
                break

            soup = BeautifulSoup(r.text, "lxml")
            sel = self.config["selectors"]
            rows = soup.select(sel["product_item"])

            # Filter to rows that are actual product rows (have a product_info.php link)
            product_rows = [tr for tr in rows if tr.select_one(sel["product_link"])]

            if not product_rows:
                break

            for row in product_rows:
                product = self._parse_product(row, category)
                if product:
                    results.append(product)

            if not self._find_next_page(soup):
                break
            page += 1

        return results

    def parse_price(self, raw: str) -> float | None:
        """
        Parse Santa Maria price format: '$4446.270' → 4446.27
        Dot is decimal separator; no thousands separator present.
        """
        try:
            cleaned = raw.replace("$", "").strip()
            return float(cleaned) if cleaned else None
        except (ValueError, AttributeError):
            return None

    # ------------------------------------------------------------------ #

    def _parse_product(self, row, category: str) -> dict | None:
        """Extract a product dict from a product listing <tr> element."""
        try:
            sel = self.config["selectors"]
            tds = row.select("td")
            if len(tds) < 4:
                return None

            link_el = row.select_one(sel["product_link"])
            if not link_el:
                return None

            # Name is in the 2nd td (index 1), the text link (not the image link)
            name_el = tds[1].select_one("a")
            name = name_el.get_text(strip=True) if name_el else link_el.get_text(strip=True)
            if not name:
                return None

            # Price is in the 4th td (index 3): "$N.NNN  (s/IVA)\n$N.NNN  (c/IVA)"
            price_td = tds[3]
            prices = re.findall(r'\$([\d.]+)', price_td.get_text())
            price_unit = self.parse_price(prices[0]) if len(prices) >= 1 else None
            price_bulk = self.parse_price(prices[1]) if len(prices) >= 2 else None

            # SKU = products_id from href (no barcode in listing HTML)
            href = link_el.get("href", "")
            sku_match = re.search(r"products_id=(\d+)", href)
            if not sku_match:
                return None
            sku = sku_match.group(1)

            full_url = href if href.startswith("http") else f"{BASE}/{href.lstrip('/')}"
            full_url = re.sub(r"[&?]?osCsid=[^&]*", "", full_url).strip("&?")

            # UxB (units per bulk box) is in the 3rd td — store for post-processing
            uxb = tds[2].get_text(strip=True) if len(tds) >= 3 else ""
            stock = f"uxb:{uxb}" if uxb.isdigit() else "unknown"

            return {
                "sku":        sku,
                "name":       name,
                "url":        full_url,
                "category":   category,
                "price_unit": price_unit,   # bulk price s/IVA (net)
                "price_bulk": price_bulk,   # bulk price c/IVA (gross)
                "stock":      stock,        # "uxb:N" — use to derive per-unit price later
            }

        except Exception as e:
            logger.warning(f"SantaMaria: failed to parse product — {e}")
            return None

    def _find_next_page(self, soup: BeautifulSoup) -> str | None:
        """Return the next-page URL if present (link with text '>'), else None."""
        for a in soup.select("a[href*='page=']"):
            if a.get_text(strip=True) == ">":
                return a["href"]
        return None

    def _extract_category(self, url: str) -> str:
        """Return the raw cPath value from a category URL."""
        qs = parse_qs(urlparse(url).query)
        return qs.get("cPath", [""])[0]
