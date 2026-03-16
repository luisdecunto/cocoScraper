import asyncio, asyncpg, os
from collections import defaultdict
from dotenv import load_dotenv
load_dotenv()

async def main():
    pool = await asyncpg.create_pool(
        host=os.getenv('DB_HOST','localhost'), port=int(os.getenv('DB_PORT',5432)),
        database=os.getenv('DB_NAME','prices'), user=os.getenv('DB_USER'), password=os.getenv('DB_PASS')
    )
    async with pool.acquire() as conn:
        rows = list(await conn.fetch(
            "SELECT p.canonical_key, p.supplier, p.name, s.price_unit FROM products p "
            "JOIN price_snapshots s ON s.sku=p.sku AND s.supplier=p.supplier "
            "WHERE s.scraped_at=(SELECT MAX(scraped_at) FROM price_snapshots WHERE sku=p.sku AND supplier=p.supplier) "
            "AND p.canonical_key IS NOT NULL AND p.supplier IN ('luvik','vital')"
        ))
    by_key = defaultdict(dict)
    for r in rows:
        ck = r['canonical_key']
        if ck != '?|?|?':
            by_key[ck][r['supplier']] = r
    out = []
    for key, sups in by_key.items():
        if 'luvik' in sups and 'vital' in sups:
            lv = float(sups['luvik']['price_unit'] or 0)
            vt = float(sups['vital']['price_unit'] or 0)
            if vt > 0 and lv / vt > 3:
                out.append((round(lv/vt, 1), key, sups['luvik']['name'], lv, vt))
    out.sort(reverse=True)
    print(f'ratio>3x: {len(out)}')
    for ratio, key, name, lv, vt in out[:25]:
        print(f'  {ratio}x  lv={lv:.0f} vt={vt:.0f}  {name!r}')
    # Also check distinct ratios to identify common package sizes
    ratios = [r[0] for r in out]
    from collections import Counter
    rounded = [round(r) for r in ratios]
    print('Most common rounded ratios:', Counter(rounded).most_common(10))
    await pool.close()

asyncio.run(main())
