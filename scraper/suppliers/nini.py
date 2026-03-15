"""
Nini supplier.
URL: http://ecommerce.nini.com.ar:8081/ventas.online/
Platform: Custom ASP.NET + Node.js
Approach: httpx JSON API — POST /nodejs/<dao>/<method>
Auth: .ASPXAUTH cookie (ASP.NET forms auth) + session params
Price: float, no parsing needed
SKU: internal product id field (no EAN available)
"""

import asyncio
import json
import logging
import os
import re
import time

import httpx

from scraper.suppliers.base import BaseSupplier

logger = logging.getLogger(__name__)

BASE = "http://ecommerce.nini.com.ar:8081"

# Confirmed department IDs (from UI observation)
DEPARTMENTS = {
    "Almacen":      "210",
    "Anexo":        "220",
    "Bebidas":      "230",
    "Golosinas":    "240",
    "Limpieza":     "250",
    "Mascotas":     "260",
    "Perfumeria":   "270",
    "Refrigerados": "290",
}


class NiniSupplier(BaseSupplier):
    """Supplier implementation for Nini wholesale (ecommerce.nini.com.ar:8081)."""

    def __init__(self, config: dict):
        super().__init__(config)
        # Session state — populated during login()
        self._seller_id: str | None = None
        self._user_name: str | None = None
        self._order_id:  str | None = None
        self._zone:      str = "10000002"

    async def login(self, client: httpx.AsyncClient) -> None:
        """
        Three-step login:
        1. GET ValidateUser → sets .ASPXAUTH cookie, returns Zone
        2. POST getUnique   → gets sellerId and userName
        3. POST findByClientId → gets active orderId
        """
        username = os.getenv(self.config["credentials_env"]["username"])
        password = os.getenv(self.config["credentials_env"]["password"])

        # Step 1 — ValidateUser (JSONP GET, credentials in query string)
        ts = int(time.time() * 1000)
        r = await client.get(
            f"{BASE}/ventas.administracion/Account/ValidateUser",
            params={
                "userName": username,
                "password": password,
                "callback": "_jqjsp",
                f"_{ts}": "",
            },
        )
        if ".ASPXAUTH" not in client.cookies:
            raise RuntimeError(
                "Nini: .ASPXAUTH cookie not set after ValidateUser — "
                "check NINI_USER and NINI_PASS."
            )

        # Parse Zone from JSONP: _jqjsp({"Rol":"3","Zone":10000002})
        match = re.search(r'\((.+)\)', r.text)
        if match:
            data = json.loads(match.group(1))
            self._zone = str(data.get("Zone", "10000002"))

        # Step 2 — getUnique → sellerId, userName
        r2 = await client.post(
            f"{BASE}/nodejs/onlineUserDao/getUnique",
            data={
                "daoName":  "onlineUserDao",
                "method":   "getUnique",
                "params[]": username,
            },
        )
        r2.raise_for_status()
        user_data = r2.json()[0]
        self._seller_id = user_data["sellerId"]
        self._user_name = user_data["userName"]

        # Step 3 — fetch active orderId
        self._order_id = await self._fetch_order_id(client)
        logger.info(
            f"Nini: authenticated userName={self._user_name} "
            f"sellerId={self._seller_id} zone={self._zone} "
            f"orderId={self._order_id}"
        )

    async def discover_categories(self, client: httpx.AsyncClient) -> list[str]:
        """
        For each department, fetch sectors via onlineSectorDao/findFacets.
        Returns pseudo-URLs: "nini-sector:<departamentId>:<sectorId>:<description>"
        """
        categories: list[str] = []
        for dept_name, dept_id in DEPARTMENTS.items():
            sectors = await self._fetch_sectors(client, dept_id)
            for s in sectors:
                categories.append(
                    f"nini-sector:{dept_id}:{s['id']}:{s['description']}"
                )
            logger.info(f"Nini: {dept_name} → {len(sectors)} sectors")
        return categories

    async def scrape_category(
        self,
        client: httpx.AsyncClient,
        url: str,
        sem: asyncio.Semaphore,
    ) -> list[dict]:
        """
        Fetch all products for one sector, paginated by 50.
        url format: "nini-sector:<departamentId>:<sectorId>:<description>"
        """
        _, dept_id, sector_id, description = url.split(":", 3)
        results: list[dict] = []
        offset = 0

        while True:
            payload = self._build_product_payload(dept_id, sector_id, offset)
            try:
                async with sem:
                    await asyncio.sleep(0.3)
                    r = await client.post(
                        f"{BASE}/nodejs/onlineProductDao/findAllWithOrder",
                        data=payload,
                    )
                r.raise_for_status()
            except httpx.HTTPError as e:
                logger.warning(
                    f"Nini: HTTP error on sector {sector_id} offset {offset} — {e}"
                )
                break

            products = r.json()
            if not products:
                break

            for p in products:
                parsed = self._parse_product(p, description)
                if parsed:
                    results.append(parsed)

            if len(products) < 50:
                break
            offset += 50

        return results

    def parse_price(self, raw) -> float | None:
        """Parse price — Nini returns floats directly, no Argentine formatting needed."""
        try:
            return float(raw) if raw is not None else None
        except (ValueError, TypeError):
            return None

    def decode_price_with_tax(self, encoded: str) -> float | None:
        """
        Decode Nini's obfuscated priceWithTax field.
        Encoding: each character of the price string is stored as (ASCII value + 10),
        zero-padded to 3 digits. E.g. '1' (ASCII 49) → '059'.
        '059064067058059056060064066' → '16901.268'
        """
        try:
            if not encoded or len(encoded) % 3 != 0:
                return None
            chars = [chr(int(encoded[i:i+3]) - 10) for i in range(0, len(encoded), 3)]
            return float("".join(chars))
        except (ValueError, TypeError):
            return None

    # ------------------------------------------------------------------ #
    # Private helpers
    # ------------------------------------------------------------------ #

    async def _fetch_order_id(self, client: httpx.AsyncClient) -> str:
        """
        Fetch the active order ID via onlineOrderDao/findByClientId.
        Active order = first element where orderEndDate is None.
        """
        r = await client.post(
            f"{BASE}/nodejs/onlineOrderDao/findByClientId",
            data={
                "daoName":               "onlineOrderDao",
                "method":                "findByClientId",
                "params[clientId]":      self._user_name,
                "params[sellerId]":      self._user_name,
                "params[isClient]":      "true",
                "params[userName]":      self._user_name,
                "params[zone]":          self._zone,
                "params[quotaSellerId]": self._user_name,
            },
        )
        r.raise_for_status()
        orders = r.json()
        active = next((o for o in orders if o.get("orderEndDate") is None), None)
        if active is None:
            raise RuntimeError(
                "Nini: no active order found (all orders have orderEndDate set). "
                "Client may need to create a new order in the browser."
            )
        return str(active["id"])

    async def _fetch_sectors(
        self, client: httpx.AsyncClient, dept_id: str
    ) -> list[dict]:
        """Fetch subcategories (sectors) for a department."""
        r = await client.post(
            f"{BASE}/nodejs/onlineSectorDao/findFacets",
            data=self._build_filter_payload(
                dao_name="onlineSectorDao",
                method="findFacets",
                dept_id=dept_id,
                sector_id="null",
            ),
        )
        r.raise_for_status()
        return r.json()

    def _build_filter_payload(
        self,
        dao_name: str,
        method: str,
        dept_id: str,
        sector_id: str = "null",
        offset: int = 0,
    ) -> dict:
        """Build the common filter payload used by both sector and product endpoints."""
        return {
            "daoName": dao_name,
            "method":  method,
            "params[filter][where]":               "",
            "params[filter][staticWhere]":         "",
            "params[filter][departamentId]":       dept_id,
            "params[filter][sectorId]":            sector_id,
            "params[filter][lineId]":              "null",
            "params[filter][sublineId]":           "null",
            "params[filter][catalogId]":           "null",
            "params[filter][orderId]":             "null",
            "params[filter][onlypaquete]":         "true",
            "params[filter][onlyrelated]":         "null",
            "params[filter][trademarkId]":         "null",
            "params[filter][supplierId]":          "null",
            "params[filter][presentation]":        "null",
            "params[filter][selectedPopular]":     "null",
            "params[filter][showMostPopular]":     "false",
            "params[filter][currentOrder][id]":    self._order_id or "null",
            "params[filter][articlesInCatalog]":   "false",
            "params[filter][offsetPromotions]":    "0",
            "params[filter][offsetProducts]":      str(offset),
            "params[filter][magazinePage]":        "null",
            "params[filter][advertisingProductId]": "null",
            "params[objectiveGroup]":              "null",
            "params[showStrategicPartners]":       "null",
            "params[sellerId]":                    self._seller_id,
            "params[isClient]":                    "true",
            "params[userName]":                    self._user_name,
            "params[zone]":                        self._zone,
            "params[quotaSellerId]":               self._user_name,
        }

    def _build_product_payload(
        self, dept_id: str, sector_id: str, offset: int
    ) -> dict:
        """Build product fetch payload with stock filter and pagination."""
        payload = self._build_filter_payload(
            dao_name="onlineProductDao",
            method="findAllWithOrder",
            dept_id=dept_id,
            sector_id=sector_id,
            offset=offset,
        )
        payload.update({
            "params[filter][withStock]":                              "true",
            "params[filter][buyArticles][]":                          "-1",
            "params[filter][limit]":                                  "50",
            "params[filter][currentOrder][client][averageOrderCost]": "0",
            "params[filter][currentOrder][totalCost]":                "0",
        })
        return payload

    def _build_name(self, p: dict) -> str:
        """
        Build the full product name.
        Strategy:
          1. Pick the longer of largeDescription vs smallDescription as the base
             (some products have largeDescription = '  85 G' while smallDescription
             has the real name e.g. 'Cofler Choc.Rell.Choc. 27X85')
          2. Prefix with trademark if it is not already present in the chosen description
        """
        trademark = (p.get("trademark") or "").strip()
        large     = (p.get("largeDescription") or "").strip()
        small     = (p.get("smallDescription") or "").strip()
        # Prefer the longer string — it almost always has more information
        description = large if len(large) >= len(small) else small
        if not description:
            description = large or small
        if trademark and not description.startswith(trademark):
            return f"{trademark}  {description}".strip()
        return description

    def _parse_product(self, p: dict, category: str) -> dict | None:
        """
        Map a raw product dict to the standard scraper schema.
        price_unit = priceWithTax decoded (unit price including IVA)
        price_bulk = price_unit × units_per_package (closed box cost including IVA)
        units_per_package and packs_per_pallet stored for future order calculations.
        """
        try:
            price_unit = self.decode_price_with_tax(p.get("priceWithTax", ""))
            if price_unit is None:
                # Fallback: raw price field (excludes IVA) if encoding fails
                price_unit = self.parse_price(p.get("price"))

            units_per_package: int | None = None
            packs_per_pallet:  int | None = None
            try:
                units_per_package = int(p["unidsPerPackage"]) if p.get("unidsPerPackage") else None
            except (ValueError, TypeError):
                pass
            try:
                packs_per_pallet = int(p["packsPerPallet"]) if p.get("packsPerPallet") else None
            except (ValueError, TypeError):
                pass

            price_bulk: float | None = None
            if units_per_package and price_unit is not None:
                price_bulk = round(price_unit * units_per_package, 2)

            return {
                "sku":               str(p["id"]),
                "name":              self._build_name(p),
                "url":               f"{BASE}/ventas.online/?nini.controllers.listadoDeProductos",
                "category":          category,
                "price_unit":        price_unit,
                "price_bulk":        price_bulk,
                "stock":             str(p.get("stock", "unknown")),
                "units_per_package": units_per_package,
                "packs_per_pallet":  packs_per_pallet,
            }
        except Exception as e:
            logger.warning(f"Nini: failed to parse product {p.get('id')} — {e}")
            return None
