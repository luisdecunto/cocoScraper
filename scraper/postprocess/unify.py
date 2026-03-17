"""
Cross-supplier product unification.

Matches products across suppliers using normalized features extracted by the
centralized pipeline (brand, product_type, size, canonical_key).

Supported suppliers: maxiconsumo, santamaria, luvik, vital, nini

Match key: canonical_key from products table (or computed dynamically)
  BRAND|PRODUCTTYPE|VARIANT|MEASUREMENT where measurement = "W<grams>", "V<ml>", or "?"

Run as a standalone pass:
    python -m scraper.postprocess.unify

Output: products found in 2+ suppliers, sorted by brand + product_type, with
latest prices from each supplier side-by-side and low_confidence flag.
"""

import asyncio
import csv
import io
import logging
import os
import sys
from collections import defaultdict
from datetime import date

import asyncpg
from dotenv import load_dotenv

from scraper.postprocess._utils import _ascii_fold
from scraper.postprocess.pipeline import extract_unified
from scraper.config import SUPPLIERS

logger = logging.getLogger(__name__)

_SUPPORTED_SUPPLIERS = tuple(s["id"] for s in SUPPLIERS)


# ---------------------------------------------------------------------------
# Canonical match key helper
# ---------------------------------------------------------------------------

def get_low_confidence(canonical_key: str) -> bool:
    """Return True if measurement part of key is unknown (ends with |?)."""
    parts = canonical_key.split("|")
    meas = parts[-1] if parts else "?"
    return meas == "?"


# ---------------------------------------------------------------------------
# DB query
# ---------------------------------------------------------------------------

_FETCH_SQL = """
    SELECT
        sku,
        supplier,
        name,
        category,
        brand,
        product_type,
        size_value,
        size_unit,
        canonical_key,
        price_unit,
        price_bulk,
        stock,
        last_scraped_at AS scraped_at
    FROM products
    WHERE supplier = ANY($1)
    ORDER BY supplier, name
"""


# ---------------------------------------------------------------------------
# Main unification logic
# ---------------------------------------------------------------------------

def build_matches(rows: list) -> dict[str, list[dict]]:
    """
    Group all rows by canonical key (from products.canonical_key).

    Returns {key: [product_dict, ...]} where each product_dict contains
    supplier, raw name, features, and latest prices.
    """
    groups: dict[str, list[dict]] = defaultdict(list)

    for r in rows:
        # Use canonical_key from DB (already computed and stored during pipeline)
        key = r.get("canonical_key") or "?|?|?|?"

        groups[key].append({
            "supplier":       r["supplier"],
            "sku":            r["sku"],
            "name":           r["name"],
            "brand":          r["brand"],
            "product_type":   r["product_type"],
            "size_value":     r["size_value"],
            "size_unit":      r["size_unit"],
            "price_unit":     r["price_unit"],
            "price_bulk":     r["price_bulk"],
            "stock":          r["stock"],
            "scraped_at":     r["scraped_at"],
            "low_confidence": get_low_confidence(key),
        })

    return dict(groups)


def filter_multi_supplier(groups: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """Keep only groups that have products from 2+ different suppliers."""
    return {
        key: products
        for key, products in groups.items()
        if len({p["supplier"] for p in products}) >= 2
    }


# ---------------------------------------------------------------------------
# Reporting helpers
# ---------------------------------------------------------------------------

_SUPPLIER_COLS = {
    "maxiconsumo": "MAXI",
    "santamaria":  "SM",
    "luvik":       "LV",
    "vital":       "VIT",
    "nini":        "NN",
}


def _price_str(price: float | None) -> str:
    """Format a price for display."""
    if price is None:
        return "-"
    return f"${price:,.2f}"


def _pct_diff(a: float | None, b: float | None) -> str:
    """Return percentage difference (b relative to a)."""
    if a is None or b is None or a == 0:
        return ""
    pct = 100 * (b - a) / a
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.1f}%"


def print_comparison(matches: dict[str, list[dict]], max_rows: int = 0) -> None:
    """Print a human-readable cross-supplier price comparison table."""
    cols = list(_SUPPLIER_COLS.keys())
    col_abbr = list(_SUPPLIER_COLS.values())

    header = (
        f"{'BRAND':<20} {'TYPE':<18} {'VARIANT':<20} {'MEAS':<9} "
        + "  ".join(f"{a+' p_unit':>12} {a+' p_bulk':>12}" for a in col_abbr)
        + f"  {'NOTES'}"
    )
    print(header)
    print("-" * len(header))

    # Sort by brand + product_type
    sorted_keys = sorted(
        matches.keys(),
        key=lambda k: k.split("|")[:2],
    )
    if max_rows:
        sorted_keys = sorted_keys[:max_rows]

    for key in sorted_keys:
        products = matches[key]
        # Pick representative for brand/type display (first product)
        rep = products[0]

        brand = rep["brand"] or ""
        ptype = rep["product_type"] or ""

        # Variant + measurement from canonical key (format: BRAND|TYPE|VARIANT|MEAS)
        parts = key.split("|")
        variant_part = parts[2] if len(parts) > 2 else "?"
        meas_part = parts[3] if len(parts) > 3 else "?"
        if meas_part.startswith("W"):
            grams = int(meas_part[1:])
            meas = f"{grams}g" if grams < 1000 else f"{grams/1000:.1f}kg"
        elif meas_part.startswith("V"):
            ml = int(meas_part[1:])
            meas = f"{ml}ml" if ml < 1000 else f"{ml/1000:.2f}L"
        elif meas_part.startswith("U"):
            meas = f"{meas_part[1:]} uni"
        else:
            meas = "?"

        # Build per-supplier price lookup
        price_map: dict[str, dict] = {}
        for p in products:
            sup = p["supplier"]
            if sup not in price_map:
                price_map[sup] = p

        variant_display = variant_part if variant_part != "?" else ""
        row = f"{brand:<20} {ptype:<18} {variant_display:<20} {meas:<9}"
        prices_unit = []
        for sup in cols:
            p = price_map.get(sup)
            pu = _price_str(p["price_unit"] if p else None)
            pb = _price_str(p["price_bulk"] if p else None)
            row += f"  {pu:>12} {pb:>12}"
            if p:
                prices_unit.append(p["price_unit"])

        # Flag large price spreads
        valid = [x for x in prices_unit if x is not None]
        if len(valid) >= 2:
            spread = (max(valid) - min(valid)) / min(valid) * 100
            if spread > 20:
                row += f"  *** SPREAD {spread:.0f}%"

        print(row)


def to_csv(matches: dict[str, list[dict]]) -> str:
    """Return a CSV string with one row per supplier-product pair in matched groups."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([
        "key", "supplier", "sku", "name", "brand", "product_type",
        "size_value", "size_unit", "low_confidence",
        "price_unit", "price_bulk", "stock", "scraped_at",
    ])
    for key, products in sorted(matches.items()):
        for p in products:
            writer.writerow([
                key, p["supplier"], p["sku"], p["name"],
                p["brand"], p["product_type"],
                p["size_value"], p["size_unit"], p["low_confidence"],
                p["price_unit"], p["price_bulk"], p["stock"], p["scraped_at"],
            ])
    return out.getvalue()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Cross-supplier product unification")
    parser.add_argument(
        "--suppliers", nargs="+", default=list(_SUPPORTED_SUPPLIERS),
        choices=list(_SUPPORTED_SUPPLIERS),
        help="Suppliers to include (default: all supported)"
    )
    parser.add_argument(
        "--csv", metavar="FILE",
        help="Write full matched-product CSV to this file"
    )
    parser.add_argument(
        "--top", type=int, default=50,
        help="Print top N matched groups (default: 50, 0=all)"
    )
    parser.add_argument(
        "--min-spread", type=float, default=0,
        help="Only show groups where price spread exceeds this %% (default: 0)"
    )
    args = parser.parse_args()

    async def run() -> None:
        pool = await asyncpg.create_pool(
            host=os.getenv("DB_HOST", "localhost"),
            port=int(os.getenv("DB_PORT", 5432)),
            database=os.getenv("DB_NAME", "prices"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASS"),
        )
        logger.info("Fetching products from: %s", ", ".join(args.suppliers))
        rows = await pool.fetch(_FETCH_SQL, list(args.suppliers))
        await pool.close()

        logger.info("Loaded %d product rows", len(rows))

        groups = build_matches(rows)
        matches = filter_multi_supplier(groups)

        logger.info(
            "Total groups: %d | Multi-supplier matches: %d",
            len(groups), len(matches)
        )

        # Apply min-spread filter
        if args.min_spread > 0:
            filtered = {}
            for key, products in matches.items():
                prices = [p["price_unit"] for p in products if p["price_unit"] is not None]
                prices = list({p["supplier"]: p for p in products}.values())
                vals = [p["price_unit"] for p in prices if p["price_unit"] is not None]
                if len(vals) >= 2:
                    spread = (max(vals) - min(vals)) / min(vals) * 100
                    if spread >= args.min_spread:
                        filtered[key] = products
            matches = filtered
            logger.info("After spread filter (>=%g%%): %d matches", args.min_spread, len(matches))

        print_comparison(matches, max_rows=args.top)

        if args.csv:
            csv_text = to_csv(matches)
            with open(args.csv, "w", encoding="utf-8", newline="") as f:
                f.write(csv_text)
            logger.info("CSV written to %s", args.csv)

    asyncio.run(run())
