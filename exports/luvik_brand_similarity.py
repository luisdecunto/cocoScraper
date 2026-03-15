"""
Luvik brand similarity analysis.

Extracts all unique brands from postprocessed Luvik products and runs
fuzzy similarity clustering to surface typos and near-duplicates.

Usage:
    python exports/luvik_brand_similarity.py [--threshold 85]

Output:
    exports/luvik_brand_groups.txt  — grouped near-duplicate brands
    exports/luvik_brands_unique.txt — sorted unique brand list
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

import asyncpg
from dotenv import load_dotenv
from rapidfuzz import fuzz

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))
from scraper.postprocess.luvik import extract_features, _ascii_fold


def similarity(a: str, b: str) -> float:
    """Token-sort ratio: handles word-order differences (e.g. 'SAN ROQUE' vs 'ROQUE SAN')."""
    return fuzz.token_sort_ratio(a, b)


def cluster_brands(brands: list[str], threshold: float) -> list[list[str]]:
    """
    Single-linkage clustering: two brands go in the same group if their
    similarity >= threshold. Returns groups with 2+ members only.
    """
    # Union-Find
    parent = list(range(len(brands)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        parent[find(x)] = find(y)

    folded = [_ascii_fold(b).upper() for b in brands]

    for i in range(len(brands)):
        for j in range(i + 1, len(brands)):
            score = similarity(folded[i], folded[j])
            if score >= threshold:
                union(i, j)

    # Group by root
    groups: dict[int, list[int]] = {}
    for i in range(len(brands)):
        root = find(i)
        groups.setdefault(root, []).append(i)

    return [
        sorted([brands[i] for i in members])
        for members in groups.values()
        if len(members) >= 2
    ]


async def main(threshold: float) -> None:
    load_dotenv()
    pool = await asyncpg.create_pool(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 5432)),
        database=os.getenv("DB_NAME", "prices"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASS"),
    )
    rows = await pool.fetch(
        "SELECT name, category FROM products WHERE supplier='luvik'"
    )
    await pool.close()

    # Collect unique brands
    brand_set: set[str] = set()
    for r in rows:
        f = extract_features(r["name"], r["category"])
        b = f["brand"]
        if b and b != "Generico":
            brand_set.add(b)

    brands_sorted = sorted(brand_set, key=lambda x: _ascii_fold(x).upper())
    print(f"Unique brands: {len(brands_sorted)}")

    # Write unique brand list
    unique_path = Path("exports/luvik_brands_unique.txt")
    unique_path.write_text("\n".join(brands_sorted) + "\n", encoding="utf-8")
    print(f"Written: {unique_path}")

    # Cluster
    groups = cluster_brands(brands_sorted, threshold)
    groups.sort(key=lambda g: _ascii_fold(g[0]).upper())

    # Write groups
    groups_path = Path("exports/luvik_brand_groups.txt")
    lines = [
        f"Brand similarity groups (threshold={threshold:.0f}%)",
        f"Total unique brands: {len(brands_sorted)}",
        f"Groups found: {len(groups)}",
        "",
    ]
    for g in groups:
        # Show pairwise scores within the group
        scores = []
        fa = [_ascii_fold(x).upper() for x in g]
        for i in range(len(g)):
            for j in range(i + 1, len(g)):
                scores.append(similarity(fa[i], fa[j]))
        max_score = max(scores) if scores else 0
        lines.append(f"[{max_score:.0f}%]  " + "  |  ".join(g))

    groups_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Written: {groups_path}  ({len(groups)} groups)")

    # Print to stdout as well (encode-safe for Windows cp1252)
    print()
    for line in lines[4:]:
        print(line.encode("ascii", "replace").decode("ascii"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--threshold", type=float, default=82,
        help="Similarity threshold (0–100). Default: 82"
    )
    args = parser.parse_args()
    asyncio.run(main(args.threshold))
