import asyncio, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")
import asyncpg
from scraper.postprocess.luvik import extract_features

PAIRS = [
    ("C. CRECIENTE", "CRECIENTE"),
    ("LUCCHETTI", "LUCCHETTINIS"),
    ("VENTUS", "VENUS"),
]

async def main():
    pool = await asyncpg.create_pool(
        host=os.getenv("DB_HOST","localhost"), port=int(os.getenv("DB_PORT",5432)),
        database=os.getenv("DB_NAME","prices"), user=os.getenv("DB_USER"), password=os.getenv("DB_PASS"),
    )
    rows = await pool.fetch("SELECT name, category FROM products WHERE supplier='luvik'")
    await pool.close()

    results = {b: [] for pair in PAIRS for b in pair}
    for r in rows:
        f = extract_features(r["name"], r["category"])
        b = f["brand"]
        if b in results:
            results[b].append(r["name"])

    lines = []
    for a, b in PAIRS:
        lines.append("=" * 60)
        lines.append(f"GROUP: {a}  |  {b}")
        lines.append("=" * 60)
        for brand in (a, b):
            lines.append(f"\n  --- {brand} ({len(results[brand])} products) ---")
            for name in sorted(results[brand]):
                lines.append(f"  {name}")
        lines.append("")

    out = "\n".join(lines)
    with open("exports/luvik_brand_review.txt", "w", encoding="utf-8") as fh:
        fh.write(out)
    print("Written: exports/luvik_brand_review.txt")
    print(out.encode("ascii","replace").decode("ascii"))

asyncio.run(main())
