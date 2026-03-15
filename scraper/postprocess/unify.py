"""
Cross-supplier product unification.

Matches products across suppliers using normalized features extracted by each
supplier's postprocessor (brand, product_type, weight_g / volume_ml).

Supported suppliers: maxiconsumo, santamaria, vital
Luvik and Nini do not yet have postprocessors and are excluded.

Match key: ascii_fold(brand) + "|" + ascii_fold(product_type) + "|" + measurement
  where measurement = "W<grams>" (weight) or "V<ml>" (volume) or "?" (unknown)

Run as a standalone pass:
    python -m scraper.postprocess.unify

Output: products found in 2+ suppliers, sorted by brand + product_type, with
latest prices from each supplier side-by-side.
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
from scraper.postprocess import maxiconsumo as _maxi
from scraper.postprocess import santamaria as _sm
from scraper.postprocess import vital as _vital

logger = logging.getLogger(__name__)

_SUPPORTED_SUPPLIERS = ("maxiconsumo", "santamaria", "vital")


# ---------------------------------------------------------------------------
# Feature normalization
# ---------------------------------------------------------------------------

def _meas_from_dict(d: dict | None) -> float | None:
    """Extract float from {"value": float, "unit": str} (Maxi/Vital format)."""
    return d["value"] if isinstance(d, dict) else d


def extract_unified(supplier: str, name: str, category: str) -> dict:
    """
    Dispatch to the correct postprocessor and return a normalized feature dict.

    All suppliers produce the same output keys:
        brand, product_type, variant, weight_g, volume_ml, units_in_name,
        clean_name, category_norm
    """
    if supplier == "maxiconsumo":
        f = _maxi.extract_features(name)
        c = _maxi.parse_category(category)
        return {
            "brand":         f["brand"],
            "product_type":  f["product_type"],
            "variant":       f["variant"],
            "weight_g":      _meas_from_dict(f.get("weight")),
            "volume_ml":     _meas_from_dict(f.get("volume")),
            "units_in_name": f["units_in_name"],
            "clean_name":    f["clean_name"],
            "category_norm": c["leaf"] or c["section"] or category,
        }
    elif supplier == "santamaria":
        f = _sm.extract_features(name)
        return {
            "brand":         f["brand"],
            "product_type":  f["product_type"],
            "variant":       f["variant"],
            "weight_g":      f["weight_g"],
            "volume_ml":     f["volume_ml"],
            "units_in_name": f["units_in_name"],
            "clean_name":    f["clean_name"],
            "category_norm": _sm.normalize_category(category),
        }
    elif supplier == "vital":
        f = _vital.extract_features(name)
        return {
            "brand":         f["brand"],
            "product_type":  f["product_type"],
            "variant":       f["variant"],
            "weight_g":      _meas_from_dict(f.get("weight")),
            "volume_ml":     _meas_from_dict(f.get("volume")),
            "units_in_name": f["units_in_name"],
            "clean_name":    f["clean_name"],
            "category_norm": category,
        }
    else:
        return {
            "brand": None, "product_type": None, "variant": None,
            "weight_g": None, "volume_ml": None, "units_in_name": None,
            "clean_name": name, "category_norm": category,
        }


# ---------------------------------------------------------------------------
# Canonical match key
# ---------------------------------------------------------------------------

def canonical_key(brand: str | None, product_type: str | None,
                  weight_g: float | None, volume_ml: float | None) -> str:
    """
    Build a canonical key for cross-supplier product matching.

    Two products match when they share the same brand, product type, and
    measurement (weight or volume, rounded to nearest gram/ml).
    Keys are accent-insensitive and space-normalized.
    """
    b = _ascii_fold(brand or "").upper().replace(" ", "")
    t = _ascii_fold(product_type or "").upper().replace(" ", "")

    if weight_g and weight_g > 0:
        m = f"W{round(weight_g)}"
    elif volume_ml and volume_ml > 0:
        m = f"V{round(volume_ml)}"
    else:
        m = "?"

    return f"{b}|{t}|{m}"


# ---------------------------------------------------------------------------
# DB query
# ---------------------------------------------------------------------------

_FETCH_SQL = """
    SELECT
        p.sku,
        p.supplier,
        p.name,
        p.category,
        s.price_unit,
        s.price_bulk,
        s.stock,
        s.scraped_at
    FROM products p
    LEFT JOIN price_snapshots s
        ON  s.sku      = p.sku
        AND s.supplier = p.supplier
        AND s.scraped_at = (
            SELECT MAX(scraped_at)
            FROM   price_snapshots
            WHERE  sku = p.sku AND supplier = p.supplier
        )
    WHERE p.supplier = ANY($1)
    ORDER BY p.supplier, p.name
"""


# ---------------------------------------------------------------------------
# Main unification logic
# ---------------------------------------------------------------------------

def build_matches(rows: list) -> dict[str, list[dict]]:
    """
    Group all rows by canonical key.

    Returns {key: [product_dict, ...]} where each product_dict contains
    supplier, raw name, features, and latest prices.
    """
    groups: dict[str, list[dict]] = defaultdict(list)

    for r in rows:
        feat = extract_unified(r["supplier"], r["name"], r["category"])
        key = canonical_key(
            feat["brand"], feat["product_type"],
            feat["weight_g"], feat["volume_ml"]
        )
        groups[key].append({
            "supplier":    r["supplier"],
            "sku":         r["sku"],
            "name":        r["name"],
            "price_unit":  r["price_unit"],
            "price_bulk":  r["price_bulk"],
            "stock":       r["stock"],
            "scraped_at":  r["scraped_at"],
            **feat,
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
    "vital":       "VIT",
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
        f"{'BRAND':<20} {'TYPE':<18} {'MEAS':<9} "
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

        # Measurement label
        if rep["weight_g"]:
            w = rep["weight_g"]
            meas = f"{w:.0f}g" if w < 1000 else f"{w/1000:.1f}kg"
        elif rep["volume_ml"]:
            v = rep["volume_ml"]
            meas = f"{v:.0f}ml" if v < 1000 else f"{v/1000:.2f}L"
        else:
            meas = "?"

        # Build per-supplier price lookup
        price_map: dict[str, dict] = {}
        for p in products:
            sup = p["supplier"]
            if sup not in price_map:
                price_map[sup] = p

        row = f"{brand:<20} {ptype:<18} {meas:<9}"
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
        "key", "supplier", "sku", "name", "brand", "product_type", "variant",
        "weight_g", "volume_ml", "units_in_name", "category_norm",
        "price_unit", "price_bulk", "stock", "scraped_at",
    ])
    for key, products in sorted(matches.items()):
        for p in products:
            writer.writerow([
                key, p["supplier"], p["sku"], p["name"],
                p["brand"], p["product_type"], p["variant"],
                p["weight_g"], p["volume_ml"], p["units_in_name"],
                p["category_norm"],
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
