"""
Post-scrape feature extraction pipeline.

Central dispatcher that extracts normalized features (brand, type, size, category)
from raw product data using supplier-specific postprocessors.

Called automatically after each scrape run in scraper.py.
Also callable standalone via CLI: python -m scraper.postprocess.pipeline

Usage:
    # Extract features for products missing postprocessing
    python -m scraper.postprocess.pipeline

    # Force re-extract all products
    python -m scraper.postprocess.pipeline --force

    # List unmapped product_types for review
    python -m scraper.postprocess.pipeline --list-unmapped
"""

import asyncio
import logging
import os
from pathlib import Path

import asyncpg

from scraper.postprocess._utils import normalize_brand

logger = logging.getLogger(__name__)

# Bump this version when extraction logic changes.
# Products with features_version < FEATURES_VERSION will be re-extracted.
FEATURES_VERSION = 2


# ============================================================================
# Category Taxonomy Loader
# ============================================================================

def _load_category_map(filename: str) -> dict[str, tuple[str, str]]:
    """
    Load unified category taxonomy from data file.
    Returns: {product_type_upper: (department, subcategory)}
    """
    data_dir = Path(__file__).parent / "data"
    filepath = data_dir / filename

    mapping = {}
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                # Skip comments and empty lines
                if not line or line.startswith("#"):
                    continue

                parts = line.split("|")
                if len(parts) != 3:
                    logger.warning(f"Skipping malformed line in {filename}: {line}")
                    continue

                dept, sub, product_type = parts
                key = _ascii_fold(product_type).upper()
                mapping[key] = (dept.strip(), sub.strip())

        logger.info(f"Loaded {len(mapping)} category mappings from {filename}")
        return mapping
    except FileNotFoundError:
        logger.error(f"Category file not found: {filepath}")
        return {}


def _ascii_fold(text: str) -> str:
    """Remove accents and normalize to ASCII."""
    if not text:
        return ""
    import unicodedata
    nfd = unicodedata.normalize("NFD", text)
    return "".join(c for c in nfd if unicodedata.category(c) != "Mn")


def _get_canonical_category(
    product_type: str | None, category_map: dict[str, tuple[str, str]]
) -> tuple[str, str]:
    """
    Map product_type to (category_dept, category_sub).
    Returns ("Otros", "Otros") if not found.
    """
    if not product_type:
        return ("Otros", "Otros")

    key = _ascii_fold(product_type).upper()
    return category_map.get(key, ("Otros", "Otros"))


def _canonical_name(
    product_type: str | None,
    brand: str | None,
    variant: str | None,
    size: str | None,
) -> str | None:
    """
    Build a clean, human-readable product label from extracted features.
    Example: "Harina Blancaflor Leudante 1 kg"
    Parts are title-cased; None parts are omitted.
    """
    parts = []
    for part in (product_type, brand, variant, size):
        if part:
            parts.append(part.strip())
    if not parts:
        return None
    return " ".join(parts)


def _canonical_key(
    brand: str | None,
    product_type: str | None,
    variant: str | None,
    weight_g: float | None,
    volume_ml: float | None,
) -> str:
    """
    Build canonical matching key: BRAND|TYPE|VARIANT|MEASUREMENT

    Variant distinguishes sub-types of the same product (e.g. Leudante vs
    Integral for the same brand/type/size of harina).
    Measurement is W<grams>, V<ml>, or ?
    """
    brand_part = _ascii_fold(brand).upper() if brand else "?"
    type_part = _ascii_fold(product_type).upper() if product_type else "?"
    variant_part = _ascii_fold(variant).upper() if variant else "?"

    if weight_g is not None:
        meas_part = f"W{int(round(weight_g))}"
    elif volume_ml is not None:
        meas_part = f"V{int(round(volume_ml))}"
    else:
        meas_part = "?"

    return f"{brand_part}|{type_part}|{variant_part}|{meas_part}"


# ============================================================================
# Feature Extraction Dispatcher
# ============================================================================

def extract_unified(
    supplier: str, name: str, category: str, category_map: dict
) -> dict:
    """
    Dispatch to supplier-specific postprocessor.

    Returns normalized features dict with keys:
        brand, product_type, variant,
        size_value (float), size_unit (str: g/ml/uni/m/W/etc),
        weight_g, volume_ml (derived for matching),
        category_dept, category_sub,
        canonical_key
    """
    # Dynamic import of postprocessor
    try:
        if supplier == "maxiconsumo":
            from scraper.postprocess.maxiconsumo import extract_features
        elif supplier == "santamaria":
            from scraper.postprocess.santamaria import extract_features
        elif supplier == "luvik":
            from scraper.postprocess.luvik import extract_features
        elif supplier == "vital":
            from scraper.postprocess.vital import extract_features
        elif supplier == "nini":
            from scraper.postprocess.nini import extract_features
        else:
            logger.warning(f"Unknown supplier: {supplier}")
            return _empty_features()

        # Call supplier-specific extraction
        # Luvik and Nini accept category; others only take name
        if supplier in ("luvik", "nini"):
            features = extract_features(name, category)
        else:
            features = extract_features(name)

        # Normalize size across all 5 supplier formats:
        #   luvik/nini:          size_value (float) + size_unit (str)
        #   maxiconsumo/vital:   weight={'value': X, 'unit': 'g'} / volume={'value': X, 'unit': 'ml'}
        #   santamaria:          weight_g (float) / volume_ml (float)
        size_value = features.get("size_value")
        size_unit = features.get("size_unit")

        if size_value is None:
            # Try maxiconsumo/vital nested dict format
            weight_dict = features.get("weight")
            volume_dict = features.get("volume")
            if isinstance(weight_dict, dict) and weight_dict.get("value") is not None:
                size_value = float(weight_dict["value"])
                size_unit = weight_dict.get("unit", "g")
            elif isinstance(volume_dict, dict) and volume_dict.get("value") is not None:
                size_value = float(volume_dict["value"])
                size_unit = volume_dict.get("unit", "ml")
            # Try santamaria flat format
            elif features.get("weight_g") is not None:
                size_value = float(features["weight_g"])
                size_unit = "g"
            elif features.get("volume_ml") is not None:
                size_value = float(features["volume_ml"])
                size_unit = "ml"

        # Build human-readable merged size string
        size: str | None = None
        if size_value is not None and size_unit is not None:
            if size_unit in ("g", "ml") and size_value >= 1000:
                # Display as kg / L
                display_val = size_value / 1000
                display_unit = "kg" if size_unit == "g" else "L"
                size = f"{display_val:g} {display_unit}"
            else:
                size = f"{size_value:g} {size_unit}"

        # Bridge: convert size_unit to weight_g/volume_ml for canonical_key
        weight_g = None
        volume_ml = None
        if size_value is not None:
            if size_unit == "g":
                weight_g = size_value
            elif size_unit == "ml":
                volume_ml = size_value

        # Get canonical categories
        product_type = features.get("product_type")
        category_dept, category_sub = _get_canonical_category(product_type, category_map)

        # Build cross-supplier matching key
        brand = normalize_brand(features.get("brand"))

        variant = features.get("variant")
        canonical_key = _canonical_key(brand, product_type, variant, weight_g, volume_ml)
        canonical_name = _canonical_name(product_type, brand, variant, size)

        return {
            "brand": brand,
            "product_type": product_type,
            "variant": variant,
            "size": size,
            "size_value": size_value,
            "size_unit": size_unit,
            "weight_g": weight_g,
            "volume_ml": volume_ml,
            "category_dept": category_dept,
            "category_sub": category_sub,
            "canonical_key": canonical_key,
            "canonical_name": canonical_name,
        }

    except Exception as e:
        logger.error(f"Error extracting features for {supplier} '{name}': {e}")
        return _empty_features()


def _empty_features() -> dict:
    """Return a features dict with all None/default values."""
    return {
        "brand": None,
        "product_type": None,
        "variant": None,
        "size": None,
        "size_value": None,
        "size_unit": None,
        "weight_g": None,
        "volume_ml": None,
        "category_dept": "Otros",
        "category_sub": "Otros",
        "canonical_key": "?|?|?|?",
        "canonical_name": None,
    }


# ============================================================================
# Pipeline Runner
# ============================================================================

async def run_pipeline(
    pool: asyncpg.Pool,
    supplier: str,
    short_code: str,
    force: bool = False,
) -> int:
    """
    Extract features for products in a supplier.

    Args:
        pool: Database connection pool
        supplier: supplier ID (e.g. "maxiconsumo")
        short_code: supplier short code (e.g. "mx")
        force: if True, re-extract all products; else only unprocessed

    Returns: count of rows updated
    """
    # Load category map once
    category_map = _load_category_map("unified_categories.txt")

    # Fetch rows to process
    from scraper.db import fetch_products_for_postprocess, upsert_product_features

    if force:
        # Fetch all products
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT sku, name, category FROM products WHERE supplier = $1
                ORDER BY sku
                """,
                supplier,
            )
    else:
        # Fetch only unprocessed rows
        rows = await fetch_products_for_postprocess(pool, supplier, FEATURES_VERSION)

    if not rows:
        logger.info(f"{supplier}: no rows to process")
        return 0

    logger.info(f"{supplier}: processing {len(rows)} products")

    updated = 0
    for row in rows:
        sku = row["sku"]
        name = row["name"] or ""
        category = row["category"] or ""

        # Extract features
        features = extract_unified(supplier, name, category, category_map)

        # Generate product_id
        product_id = f"{short_code}_{sku}"

        # Write to DB
        try:
            await upsert_product_features(
                pool,
                sku=sku,
                supplier=supplier,
                product_id=product_id,
                brand=features.get("brand"),
                product_type=features.get("product_type"),
                variant=features.get("variant"),
                size=features.get("size"),
                size_value=features.get("size_value"),
                size_unit=features.get("size_unit"),
                category_dept=features.get("category_dept"),
                category_sub=features.get("category_sub"),
                canonical_key=features.get("canonical_key"),
                canonical_name=features.get("canonical_name"),
                features_version=FEATURES_VERSION,
            )
            updated += 1
        except Exception as e:
            logger.error(f"Failed to update {sku} ({supplier}): {e}")

    logger.info(f"{supplier}: updated {updated} products")
    return updated


async def run_all_suppliers(pool: asyncpg.Pool, force: bool = False) -> None:
    """Run pipeline for all registered suppliers sequentially."""
    from scraper.config import SUPPLIERS

    total_updated = 0
    for supplier_config in SUPPLIERS:
        supplier_id = supplier_config["id"]
        short_code = supplier_config["short_code"]
        updated = await run_pipeline(pool, supplier_id, short_code, force=force)
        total_updated += updated

    logger.info(f"Pipeline complete: {total_updated} total products updated")


# ============================================================================
# Unmapped Type Discovery
# ============================================================================

async def list_unmapped_types(pool: asyncpg.Pool) -> None:
    """
    Find all distinct product_types and show which ones are unmapped.
    Useful for extending the taxonomy.
    """
    category_map = _load_category_map("unified_categories.txt")

    async with pool.acquire() as conn:
        # Get all distinct product_types across all suppliers
        rows = await conn.fetch(
            """
            SELECT DISTINCT product_type, COUNT(*) as count
            FROM products
            WHERE product_type IS NOT NULL
            GROUP BY product_type
            ORDER BY count DESC
            """
        )

    unmapped = []
    for row in rows:
        product_type = row["product_type"]
        count = row["count"]
        key = _ascii_fold(product_type).upper()
        if key not in category_map:
            unmapped.append((product_type, count))

    if not unmapped:
        print("All product_types are mapped!")
        return

    print(f"\nUnmapped product_types ({len(unmapped)} found):\n")
    for product_type, count in unmapped:
        print(f"  {product_type:<40} ({count} products)")

    print(f"\nTo add these, update scraper/postprocess/data/unified_categories.txt")
    print(f"Format: DEPARTMENT|SUBCATEGORY|{product_type}")


# ============================================================================
# CLI
# ============================================================================

def _print_dry_run_table(rows_features: list[tuple[str, str, str, dict]]) -> None:
    """Print a formatted debug table for dry-run mode."""
    W = {"raw": 45, "type": 18, "brand": 18, "variant": 22, "size": 10, "name": 40}
    header = (
        f"{'SKU':<14} {'RAW NAME':<{W['raw']}} "
        f"{'TYPE':<{W['type']}} {'BRAND':<{W['brand']}} "
        f"{'VARIANT':<{W['variant']}} {'SIZE':<{W['size']}} "
        f"{'CANONICAL NAME':<{W['name']}}"
    )
    print(header)
    print("-" * len(header))
    for supplier, sku, raw_name, f in rows_features:
        print(
            f"{sku:<14} {(raw_name or '')[:W['raw']]:<{W['raw']}} "
            f"{(f.get('product_type') or '')[:W['type']]:<{W['type']}} "
            f"{(f.get('brand') or '')[:W['brand']]:<{W['brand']}} "
            f"{(f.get('variant') or '')[:W['variant']]:<{W['variant']}} "
            f"{(f.get('size') or '')[:W['size']]:<{W['size']}} "
            f"{(f.get('canonical_name') or '')[:W['name']]:<{W['name']}}"
        )


async def main():
    """CLI entry point."""
    import argparse
    from dotenv import load_dotenv

    load_dotenv()

    parser = argparse.ArgumentParser(description="Post-scrape feature extraction pipeline")
    parser.add_argument("--supplier", type=str, default=None, help="Process specific supplier only")
    parser.add_argument("--force", action="store_true", help="Re-extract all products, not just unprocessed")
    parser.add_argument("--list-unmapped", action="store_true", help="Show unmapped product_types for review")
    parser.add_argument("--dry-run", action="store_true", help="Print extracted features without writing to DB")
    parser.add_argument("--sample", type=int, default=0, metavar="N", help="With --dry-run: limit to N products (default: all)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")

    from scraper.db import get_pool

    pool = await get_pool()

    try:
        if args.list_unmapped:
            await list_unmapped_types(pool)
        elif args.dry_run:
            # Dry-run: extract features and print table, no DB writes
            category_map = _load_category_map("unified_categories.txt")
            suppliers_to_check = [args.supplier] if args.supplier else [s["id"] for s in __import__("scraper.config", fromlist=["SUPPLIERS"]).SUPPLIERS]
            results: list[tuple] = []
            for supplier_id in suppliers_to_check:
                async with pool.acquire() as conn:
                    query = "SELECT sku, name, category FROM products WHERE supplier = $1 ORDER BY RANDOM()"
                    if args.sample:
                        query += f" LIMIT {args.sample}"
                    rows = await conn.fetch(query, supplier_id)
                for row in rows:
                    f = extract_unified(supplier_id, row["name"] or "", row["category"] or "", category_map)
                    results.append((supplier_id, row["sku"], row["name"], f))
            _print_dry_run_table(results)
            print(f"\n{len(results)} products shown (dry-run — nothing written to DB)")
        elif args.supplier:
            from scraper.config import get_supplier_config, get_short_code
            short_code = get_short_code(args.supplier)
            await run_pipeline(pool, args.supplier, short_code, force=args.force)
        else:
            await run_all_suppliers(pool, force=args.force)
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
