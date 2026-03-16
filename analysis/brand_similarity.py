"""
Brand similarity analysis across suppliers.

1. Fetches all (brand, supplier, count) from DB.
2. Normalizes: uppercase + ascii-fold + strip punctuation.
3. Groups brands that are IDENTICAL after normalization → easy unified candidates.
4. Fuzzy-matches remaining brands that share first 3 chars → potential aliases.

Output: analysis/brand_similarity_report.txt
"""

import asyncio
import os
import re
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

load_dotenv()

OUTPUT = Path(__file__).parent / "brand_similarity_report.txt"


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def ascii_fold(text: str) -> str:
    nfd = unicodedata.normalize("NFD", text)
    return "".join(c for c in nfd if unicodedata.category(c) != "Mn")


def normalize(brand: str) -> str:
    """Uppercase + no accents + strip non-alphanumeric (keep spaces)."""
    s = ascii_fold(brand).upper()
    s = re.sub(r"[^A-Z0-9 ]", " ", s)   # apostrophes, dots, hyphens → space
    s = re.sub(r"\s+", " ", s).strip()
    return s


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def fetch_brands() -> list[dict]:
    pool = await asyncpg.create_pool(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 5432)),
        database=os.getenv("DB_NAME", "prices"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASS"),
    )
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT brand, supplier, COUNT(*) AS cnt
            FROM products
            WHERE brand IS NOT NULL AND brand != 'Generico'
            GROUP BY brand, supplier
            ORDER BY brand
        """)
    await pool.close()
    return [dict(r) for r in rows]


def build_report(rows: list[dict]) -> str:
    lines = []

    # Build: norm → list of (original_brand, supplier, count)
    norm_map: dict[str, list[tuple]] = defaultdict(list)
    for r in rows:
        n = normalize(r["brand"])
        if n:
            norm_map[n].append((r["brand"], r["supplier"], r["cnt"]))

    # -----------------------------------------------------------------------
    # Section 1: identical after normalization, across different suppliers
    # -----------------------------------------------------------------------
    unified = {
        norm: entries
        for norm, entries in norm_map.items()
        if len({e[1] for e in entries}) >= 2   # at least 2 different suppliers
    }

    lines.append("=" * 80)
    lines.append("SECTION 1: BRANDS IDENTICAL AFTER NORMALIZATION — CROSS-SUPPLIER")
    lines.append(f"  {len(unified)} groups where the same brand appears in 2+ suppliers")
    lines.append("  These can be unified automatically (same canonical form already).")
    lines.append("=" * 80)
    lines.append("")

    for norm, entries in sorted(unified.items(), key=lambda x: -sum(e[2] for e in x[1])):
        suppliers_seen = sorted({e[1] for e in entries})
        lines.append(f"  NORM: {norm}")
        for orig, sup, cnt in sorted(entries, key=lambda e: e[1]):
            lines.append(f"    [{sup:<15}] '{orig}'  ({cnt} products)")
        lines.append("")

    # -----------------------------------------------------------------------
    # Section 2: fuzzy matches across different suppliers
    # -----------------------------------------------------------------------
    lines.append("=" * 80)
    lines.append("SECTION 2: FUZZY MATCHES ACROSS SUPPLIERS (similarity >= 0.82)")
    lines.append("  These are likely the same brand spelled differently.")
    lines.append("  Review each group and decide on a canonical form.")
    lines.append("=" * 80)
    lines.append("")

    # For fuzzy matching: one representative per normalized brand
    # (pick highest-count original per norm)
    norm_rep: dict[str, tuple] = {}   # norm → (best_original, suppliers_set, total_count)
    for norm, entries in norm_map.items():
        all_sups = {e[1] for e in entries}
        total = sum(e[2] for e in entries)
        best_orig = max(entries, key=lambda e: e[2])[0]
        norm_rep[norm] = (best_orig, all_sups, total, entries)

    norms = sorted(norm_rep.keys())

    # Group by first 3 chars to limit comparisons
    prefix_groups: dict[str, list[str]] = defaultdict(list)
    for n in norms:
        prefix_groups[n[:3]].append(n)

    fuzzy_groups: list[list[str]] = []
    visited = set()

    for prefix, group in sorted(prefix_groups.items()):
        if len(group) < 2:
            continue
        for i, a in enumerate(group):
            if a in visited:
                continue
            cluster = [a]
            for b in group[i + 1:]:
                if b in visited:
                    continue
                # Only match across different suppliers
                sups_a = norm_rep[a][1]
                sups_b = norm_rep[b][1]
                if sups_a == sups_b and len(sups_a) == 1:
                    # Both exist only in the same single supplier — less interesting
                    # but still include if very similar and from different single suppliers
                    if sups_a == sups_b:
                        continue
                sim = similarity(a, b)
                if sim >= 0.82 and a != b:
                    cluster.append(b)
            if len(cluster) >= 2:
                # Check that at least 2 different suppliers are represented
                all_sups = set()
                for n in cluster:
                    all_sups |= norm_rep[n][1]
                if len(all_sups) >= 2:
                    fuzzy_groups.append(cluster)
                    visited.update(cluster)

    # Sort by total product count descending
    fuzzy_groups.sort(key=lambda g: -sum(norm_rep[n][2] for n in g))

    for group in fuzzy_groups:
        all_entries = []
        for n in group:
            all_entries.extend(norm_rep[n][3])
        all_entries.sort(key=lambda e: e[1])  # sort by supplier
        total = sum(e[2] for e in all_entries)
        lines.append(f"  FUZZY GROUP  (total: {total} products across {len({e[1] for e in all_entries})} suppliers)")
        for orig, sup, cnt in all_entries:
            norm = normalize(orig)
            lines.append(f"    [{sup:<15}] '{orig}'  →  norm: '{norm}'  ({cnt} products)")
        lines.append("")

    # -----------------------------------------------------------------------
    # Section 3: brands unique to one supplier only (informational)
    # -----------------------------------------------------------------------
    solo = {
        norm: entries
        for norm, entries in norm_map.items()
        if len({e[1] for e in entries}) == 1
    }
    lines.append("=" * 80)
    lines.append(f"SECTION 3: BRANDS FOUND IN ONLY ONE SUPPLIER ({len(solo)} groups)")
    lines.append("  Listed for completeness — no cross-supplier unification needed.")
    lines.append("=" * 80)
    lines.append("")
    for norm, entries in sorted(solo.items(), key=lambda x: -sum(e[2] for e in x[1])):
        sup = entries[0][1]
        total = sum(e[2] for e in entries)
        origs = sorted({e[0] for e in entries})
        tag = f"  [{sup:<15}] {', '.join(repr(o) for o in origs[:3])}{'...' if len(origs) > 3 else ''}  ({total} products)"
        lines.append(tag)

    return "\n".join(lines)


async def main():
    print("Fetching brands from DB...")
    rows = await fetch_brands()
    print(f"  {len(rows)} (brand, supplier) pairs")

    print("Building report...")
    report = build_report(rows)

    OUTPUT.write_text(report, encoding="utf-8")
    print(f"Report written to: {OUTPUT}")

    # Quick summary
    section_counts = report.count("FUZZY GROUP")
    unified_count = report.count("NORM:")
    print(f"  Section 1 (auto-unifiable): {unified_count} groups")
    print(f"  Section 2 (fuzzy matches): {section_counts} groups")


if __name__ == "__main__":
    asyncio.run(main())
