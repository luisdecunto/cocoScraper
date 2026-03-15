import asyncio, httpx, json

BASE = "https://tiendaonline.vital.com.ar"

async def main():
    async with httpx.AsyncClient(follow_redirects=True) as client:

        # 1. Fetch category tree (depth 3)
        r = await client.get(f"{BASE}/api/catalog_system/pub/category/tree/3")
        print("Category tree status:", r.status_code)
        if r.status_code == 200:
            tree = r.json()
            print(f"Top-level categories: {len(tree)}")
            for cat in tree[:3]:
                print(f"  id={cat['id']} name={cat['name']} children={len(cat.get('children', []))}")
                for sub in cat.get("children", [])[:2]:
                    print(f"    id={sub['id']} name={sub['name']}")

        # 2. Fetch 3 products from first category
        if r.status_code == 200 and tree:
            first_cat_id = tree[0]["id"]
            r2 = await client.get(
                f"{BASE}/api/catalog_system/pub/products/search",
                params={"_from": 0, "_to": 2, "fq": f"C:/{first_cat_id}/"}
            )
            print(f"\nProducts search status: {r2.status_code}")
            print("Resources header:", r2.headers.get("X-VTEX-Resources-Info", "not present"))
            if r2.status_code == 200:
                products = r2.json()
                print(f"Products returned: {len(products)}")
                if products:
                    p = products[0]
                    print(f"\nSample product (raw):")
                    print(json.dumps(p, indent=2, ensure_ascii=False)[:2000])

asyncio.run(main())
