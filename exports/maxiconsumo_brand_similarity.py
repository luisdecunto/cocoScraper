"""
Maxiconsumo brand similarity analysis.

Reads all unique brands from the postprocessed maxiconsumo_products.txt and runs
fuzzy similarity clustering to surface typos and near-duplicates.

Usage:
    python exports/maxiconsumo_brand_similarity.py [--threshold 82]

Output:
    exports/maxiconsumo_brand_groups.txt  — grouped near-duplicate brands
    exports/maxiconsumo_brands_unique.txt — sorted unique brand list
"""

import argparse
import sys
from pathlib import Path

from rapidfuzz import fuzz

sys.path.insert(0, str(Path(__file__).parent.parent))
from scraper.postprocess._utils import _ascii_fold


def similarity(a: str, b: str) -> float:
    """Token-sort ratio: handles word-order differences (e.g. 'SAN ROQUE' vs 'ROQUE SAN')."""
    return fuzz.token_sort_ratio(a, b)


def cluster_brands(brands: list[str], threshold: float) -> list[list[str]]:
    """
    Single-linkage clustering: two brands go in the same group if their
    similarity >= threshold. Returns groups with 2+ members only.
    """
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

    groups: dict[int, list[int]] = {}
    for i in range(len(brands)):
        root = find(i)
        groups.setdefault(root, []).append(i)

    return [
        sorted([brands[i] for i in members])
        for members in groups.values()
        if len(members) >= 2
    ]


def load_brands(products_path: Path) -> list[str]:
    """Parse the Marca column from maxiconsumo_products.txt."""
    brand_set: set[str] = set()
    with open(products_path, encoding="utf-8") as f:
        for line in f:
            if not line.startswith("| "):
                continue
            parts = [p.strip() for p in line.split("|")]
            # cols: 0=empty, 1=Nombre, 2=Marca, 3=Tipo, 4=Variante, 5=Tamaño, 6=Categoría
            brand = parts[2] if len(parts) > 2 else ""
            if brand and brand not in ("Generico", "Marca"):  # skip header row
                brand_set.add(brand)
    return sorted(brand_set, key=lambda x: _ascii_fold(x).upper())


def main(threshold: float) -> None:
    base = Path(__file__).parent

    brands = load_brands(base / "maxiconsumo_products.txt")
    print(f"Unique brands: {len(brands)}")

    unique_path = base / "maxiconsumo_brands_unique.txt"
    unique_path.write_text("\n".join(brands) + "\n", encoding="utf-8")
    print(f"Written: {unique_path}")

    groups = cluster_brands(brands, threshold)
    groups.sort(key=lambda g: _ascii_fold(g[0]).upper())

    groups_path = base / "maxiconsumo_brand_groups.txt"
    lines = [
        f"Brand similarity groups (threshold={threshold:.0f}%)",
        f"Total unique brands: {len(brands)}",
        f"Groups found: {len(groups)}",
        "",
    ]
    for g in groups:
        fa = [_ascii_fold(x).upper() for x in g]
        scores = [similarity(fa[i], fa[j]) for i in range(len(g)) for j in range(i + 1, len(g))]
        max_score = max(scores) if scores else 0
        lines.append(f"[{max_score:.0f}%]  " + "  |  ".join(g))

    groups_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Written: {groups_path}  ({len(groups)} groups)")

    print()
    for line in lines[4:]:
        print(line.encode("ascii", "replace").decode("ascii"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Maxiconsumo brand similarity analysis")
    parser.add_argument(
        "--threshold", type=float, default=82,
        help="Similarity threshold (0–100). Default: 82"
    )
    args = parser.parse_args()
    main(args.threshold)
