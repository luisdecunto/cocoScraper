"""
Throwaway script — verify Maxiconsumo CSS selectors.
Run with: python analysis/maxiconsumo/selector_debug.py
Delete or keep in analysis/ — never import from production code.
"""

import asyncio
import httpx
from bs4 import BeautifulSoup

TEST_URL = "https://maxiconsumo.com/sucursal_moreno/almacen/aceites-y-vinagres/aceites.html?product_list_limit=12"

async def main():
    async with httpx.AsyncClient(follow_redirects=True) as client:
        r = await client.get(TEST_URL)
        soup = BeautifulSoup(r.text, "lxml")

        items = soup.select(".product-item")
        print(f"Found {len(items)} product items")

        if items:
            item = items[0]
            print("\n--- RAW HTML OF FIRST PRODUCT ITEM ---")
            print(item.prettify())
            print("\n--- EXTRACTED FIELDS ---")
            print("name:  ", item.select_one(".product-item-link"))
            print("sku:   ", item.select_one(".product-item-sku"))
            print("prices:", item.select(".price-box .price"))
            print("stock: ", item.select_one(".stock"))

        next_page = soup.select_one('a[title="Siguiente"]')
        print(f"\nnext_page link: {next_page}")

asyncio.run(main())
