"""Quick test: verify price_unit (with IVA), price_bulk, and multipliers."""
import asyncio
import httpx
from scraper.config import get_supplier_config, load_supplier_class


async def test():
    config = get_supplier_config("nini")
    supplier = load_supplier_class(config)
    sem = asyncio.Semaphore(3)
    async with httpx.AsyncClient(follow_redirects=True) as client:
        await supplier.login(client)
        cats = await supplier.discover_categories(client)
        sector_label = cats[0].split(":", 3)[3]
        products = await supplier.scrape_category(client, cats[0], sem)
        print(f"Sector: {sector_label}  ({len(products)} products)\n")
        print(f"{'SKU':<10} {'price_unit':>12} {'price_bulk':>12} {'u/pkg':>5} {'pkg/plt':>7}  name")
        print("-" * 80)
        for p in products[:6]:
            bulk = f"{p['price_bulk']:>12.2f}" if p["price_bulk"] else "        None"
            upp  = str(p["units_per_package"]) if p["units_per_package"] else "-"
            ppp  = str(p["packs_per_pallet"])  if p["packs_per_pallet"]  else "-"
            print(f"{p['sku']:<10} {p['price_unit']:>12.3f} {bulk} {upp:>5} {ppp:>7}  {p['name'][:35]}")


asyncio.run(test())
