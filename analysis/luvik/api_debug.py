"""
Luvik API debug — guest-only.
Verifies that the Shopify /products.json endpoint returns products without login.
Run from project root:
  PYTHONPATH=. python analysis/luvik/api_debug.py
"""

import asyncio
import json

import httpx

BASE = "https://tiendaluvik.com.ar"


async def main() -> None:
    async with httpx.AsyncClient(follow_redirects=True) as client:
        # --- 1. Product count across entire catalog ---
        r = await client.get(f"{BASE}/products/count.json")
        if r.status_code == 200 and r.text.strip():
            print(f"Total catalog count: {r.json()}")
        else:
            print(f"products/count.json not available (status {r.status_code})")

        # --- 2. Sample collection: girasol ---
        r = await client.get(f"{BASE}/collections/girasol/products.json?limit=250")
        products = r.json().get("products", [])
        print(f"\nGuest — /collections/girasol products: {len(products)}")

        if products:
            p = products[0]
            v = p["variants"][0]
            info = {
                "product": {
                    "id": p.get("id"),
                    "title": p.get("title"),
                    "handle": p.get("handle"),
                    "vendor": p.get("vendor"),
                    "num_variants": len(p.get("variants", [])),
                },
                "variant": {
                    "id": v.get("id"),
                    "sku": v.get("sku"),
                    "price": v.get("price"),
                    "compare_at_price": v.get("compare_at_price"),
                    "available": v.get("available"),
                    "title": v.get("title"),
                },
            }
            print("\nSample product+variant:")
            print(json.dumps(info, indent=2, ensure_ascii=True))

        # --- 3. All /collections/ links from homepage ---
        r = await client.get(f"{BASE}/")
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(r.text, "lxml")
        slugs = set()
        for a in soup.select("a[href*='/collections/']"):
            href = a.get("href", "")
            parts = href.strip("/").split("/")
            if len(parts) == 2 and parts[0] == "collections" and parts[1] not in ("all", "vendors", "types"):
                slugs.add(parts[1])
        print(f"\nCollections found in homepage nav: {len(slugs)}")
        for s in sorted(slugs):
            print(f"  {s}")

        # --- 4. All-products endpoint: page 1 ---
        r = await client.get(f"{BASE}/products.json?limit=250&page=1")
        all_p = r.json().get("products", [])
        print(f"\n/products.json page 1 count: {len(all_p)}")


asyncio.run(main())
