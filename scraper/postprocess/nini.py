"""
Postprocessing for Nini product data.

Run as a standalone pass after scraping:
    python -m scraper.postprocess.nini

Functions are also importable for use in tests or other modules.
"""

import logging
import re
import unicodedata
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Load lookup data from text files
# ---------------------------------------------------------------------------

_DATA_DIR = Path(__file__).parent / "data"


def _load_lines(filename: str) -> list[str]:
    """Load non-empty, non-comment lines from a data file."""
    path = _DATA_DIR / filename
    if not path.exists():
        return []
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]


def _ascii_fold(text: str) -> str:
    """Remove accents for accent-insensitive matching (ñ→N, á→A, etc.)."""
    return unicodedata.normalize("NFD", text).encode("ascii", "ignore").decode("ascii")


# Punctuation chars that should be stripped from brand names
_PUNCT_RE = re.compile(r"[''´`'\"\u00b4\u2018\u2019\u201c\u201d]")


def _normalize_brand(brand: str) -> str:
    """Canonical brand form: uppercase, accents removed, stray punctuation stripped."""
    s = _ascii_fold(brand).upper()
    s = _PUNCT_RE.sub("", s)
    s = re.sub(r"\s+", " ", s).strip()
    return _BRAND_CORRECTIONS.get(s, s)


# Maps normalized brand → canonical brand name.
# Add entries here whenever the similarity report flags a real duplicate.
_BRAND_CORRECTIONS: dict[str, str] = {
    "ALA CAMELLITO":   "ALA",
    "ALA ULTRA":       "ALA",
    "BABYSEC PREMIUM": "BABYSEC",
    "BABYSEC ULTRA":   "BABYSEC",
    "BAGGIO FRESH":    "BAGGIO",
    "BAGGIO PRONTO":   "BAGGIO",
    "BALLANTINE S":    "BALLANTINES",
    "BURNETT S":       "BURNETTS",
    "CIEL CRYSTAL":    "CIEL",
    "CIEL DETIENNE":   "CIEL",
    "CIEL NUIT":       "CIEL",
    "CIF ACTIVE GEL":  "CIF",
    "HELLMANN S":      "HELLMANS",
    "HILERET STEVIA":  "HILERET",
    "HILERET SWEET":   "HILERET",
    "HILERET ZUCRA":   "HILERET",
    "MORENITA S/S":    "MORENITA",
    "PALITOS SELVA":   "PALITOS DE LA SELVA",
    "POND S":          "PONDS",
    "PORTENITAS":      "PORTEÑITAS",
    "PRONTO BITT":     "PRONTO",
    "PRONTO SHAKE":    "PRONTO",
    "SAN TROPEZ":      "SAINT TROPEZ",
}


def _load_aliases(filename: str) -> dict[str, str]:
    """Load VARIANT=Canonical alias pairs. Keys are ascii-folded + uppercased."""
    path = _DATA_DIR / filename
    if not path.exists():
        return {}
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        variant, _, canonical = line.partition("=")
        result[_ascii_fold(variant.strip()).upper()] = canonical.strip()
    return result


# Brand list: explicit entries for brands the all-caps heuristic misses.
# Sorted longest-first for greedy matching.
_EXTRA_BRANDS_RAW: list[str] = _load_lines("nini_brands.txt")
_EXTRA_BRANDS_FOLDED: list[str] = sorted(
    [_ascii_fold(b).upper() for b in _EXTRA_BRANDS_RAW],
    key=lambda x: -len(x),
)

# Product-type alias map: folded first-word → canonical product type string.
_PT_ALIAS_MAP: dict[str, str] = _load_aliases("nini_product_type_aliases.txt")

# Variant text normalizations: list of (compiled regex, replacement) pairs.
# Applied to variant text after extraction.
# Note: when abbreviation ends with "." and is concatenated with next word (no space),
#   the period acts as a separator — the replacement must insert a space in that case.
_VARIANT_NORMALIZATIONS: list[tuple] = [
    # "Yog.Surtido" → "Yogur Surtido", "Yog. " → "Yogur "
    (re.compile(r"\bYog\.(\S)",  re.IGNORECASE), r"Yogur \1"),
    (re.compile(r"\bYog\.",      re.IGNORECASE), "Yogur"),
    # "Surt.Esp." → "Surtidos Esp.", "Surt. " → "Surtidos "
    (re.compile(r"\bSurt\.(\S)", re.IGNORECASE), r"Surtidos \1"),
    (re.compile(r"\bSurt\.",     re.IGNORECASE), "Surtidos"),
    (re.compile(r"\bSurtida\b",  re.IGNORECASE), "Surtidos"),   # normalize gender variant
    (re.compile(r"\bAl\s+Hvo\.?\s+N(?:[°º?]\s*)?(\d+)\b", re.IGNORECASE), r"N\1 Huevo"),
    (re.compile(r"\bN(?:[°º?]\s*)?(\d+)\b", re.IGNORECASE), r"N\1"),
    (re.compile(r"\bAl\s+Hvo\.?(?=\s|$)", re.IGNORECASE), "Huevo"),
    (re.compile(r"^\s*Cap\.\s*", re.IGNORECASE), ""),
]

# ---------------------------------------------------------------------------
# Size extraction
# ---------------------------------------------------------------------------

# Matches a trailing size token, e.g. "500 G", "1,80 Kg", "750 Ml", "20 Uni".
# Anchored to end of string; also strips anything after the unit (e.g. old-format "(old)").
_SIZE_RE = re.compile(
    r"\s+(\d+[.,]?\d*)\s*(G|Ml|Lts?|L|Uni|Cm3|Kg|Cc|Gr|Kgs?)\b.*$",
    re.IGNORECASE,
)

# Canonical unit normalization
_WEIGHT_UNITS: dict[str, str] = {
    "kg": "kg", "kgs": "kg",
    "g": "g", "gr": "g",
}
_VOLUME_UNITS: dict[str, str] = {
    "l": "l", "lt": "l", "lts": "l",
    "ml": "ml", "cc": "ml", "cm3": "ml",
}


def extract_size(name: str) -> tuple[float, str] | None:
    """
    Extract the trailing size from a Nini product name.

    Returns (value, canonical_unit) or None.
    The value is always converted to a canonical base unit (grams or millilitres
    for weight/volume; raw float for 'uni').

    Examples:
        "BRAND  Aceite Oliva  500 Ml" → (500.0, "ml")
        "BRAND  Yerba Mate  1,80 Kg"  → (1800.0, "g")
        "BRAND  Pañales  20 Uni"       → (20.0, "uni")
    """
    m = _SIZE_RE.search(name)
    if not m:
        return None
    raw_value = float(m.group(1).replace(",", "."))
    raw_unit = m.group(2).lower()

    if raw_unit in _WEIGHT_UNITS:
        canonical = _WEIGHT_UNITS[raw_unit]
        value = raw_value * 1000 if canonical == "kg" else raw_value
        # kg → convert to grams for uniformity
        if raw_unit in ("kg", "kgs"):
            value = raw_value * 1000
            canonical = "g"
        else:
            canonical = "g"
        return (value, canonical)

    if raw_unit in _VOLUME_UNITS:
        canonical = _VOLUME_UNITS[raw_unit]
        if canonical == "l":
            value = raw_value * 1000
            canonical = "ml"
        else:
            value = raw_value
        return (value, canonical)

    # uni / unidades
    return (raw_value, "uni")


# ---------------------------------------------------------------------------
# Brand extraction
# ---------------------------------------------------------------------------

def extract_brand(name: str) -> str | None:
    """
    Extract the brand prefix from a Nini product name.

    Strategy:
    1. All-caps prefix heuristic: take leading tokens that are already fully
       uppercase in the *original* name (original-case check, not uppercased).
       Token must have length ≥ 2 and at least one uppercase alpha character.
    2. Explicit brand-list lookup: for brands the heuristic misses or only
       partially captures (numeric-start like "9 DE ORO", multi-word like
       "BON O BON", title-case like "Cofler").

    The longer of the two matches wins, so the explicit list can extend a
    partial heuristic match (e.g. "BON" → "BON O BON").

    Returns the brand string in its original case, or None if not found.
    """
    tokens = name.split()
    brand_tokens: list[str] = []
    for tok in tokens:
        alpha = "".join(c for c in _ascii_fold(tok) if c.isalpha())
        # Accept token if: original len ≥ 2, no lowercase alpha in ascii-folded
        # form, and at least one uppercase alpha character.
        if len(tok) >= 2 and alpha == alpha.upper() and any(c.isupper() for c in alpha):
            brand_tokens.append(tok)
        else:
            break

    heuristic_brand = " ".join(brand_tokens)

    # Explicit brand list (longest match wins, already sorted longest-first)
    name_folded = _ascii_fold(name).upper()
    list_brand = ""
    for eb in _EXTRA_BRANDS_FOLDED:
        if name_folded.startswith(eb):
            eb_len = len(eb.split())
            list_brand = " ".join(tokens[:eb_len])
            break

    # Prefer the longer match; explicit list can extend a partial heuristic hit
    raw = list_brand if len(list_brand) > len(heuristic_brand) else heuristic_brand
    return _normalize_brand(raw) if raw else None


# ---------------------------------------------------------------------------
# Name cleaning
# ---------------------------------------------------------------------------

def clean_name(raw: str) -> str:
    """Strip extra whitespace and normalize NFKC."""
    text = unicodedata.normalize("NFKC", raw)
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------

def extract_features(name: str, category: str = "") -> dict:
    """
    Extract structured features from a raw Nini product name.

    Returns a dict with keys:
        brand          str | None
        product_type   str | None    — first word of description, alias-expanded
        variant        str | None    — rest of description (between type and size)
        size_value     float | None  — canonical value (g, ml, or uni)
        size_unit      str | None    — "g", "ml", or "uni"
        category       str           — normalized category leaf
    """
    name = clean_name(name)

    # 1. Size (strip from a working copy; keep original for brand extraction)
    size = extract_size(name)
    name_no_size = _SIZE_RE.sub("", name).strip() if size else name

    # 2. Brand
    brand = extract_brand(name_no_size)
    after_brand = name_no_size[len(brand):].strip() if brand else name_no_size.strip()

    # 3. Product type = first word of remaining description, alias-expanded.
    #    Fallback: if no description remains (brand-only name like "MANON 182 G"),
    #    use the first word of the normalized category leaf.
    product_type: str | None = None
    after_type: str = after_brand
    if after_brand:
        tokens = after_brand.split()
        first_word = tokens[0]
        folded_first = _ascii_fold(first_word).upper()
        if folded_first in _PT_ALIAS_MAP:
            product_type = _PT_ALIAS_MAP[folded_first]
        else:
            product_type = first_word
        after_type = " ".join(tokens[1:])
    elif category:
        # Brand-only name: derive product type from category leaf
        cat_clean = normalize_category(category)
        # Use the full category as product type if it's a short leaf (≤ 3 words),
        # otherwise just the first word.
        cat_words = cat_clean.split()
        product_type = cat_clean if len(cat_words) <= 3 else cat_words[0]

    # 4. Variant = rest after product type
    variant = after_type.strip() if after_type.strip() else None

    # 5. Apply variant text normalizations (abbreviation expansions)
    if variant:
        for pattern, replacement in _VARIANT_NORMALIZATIONS:
            variant = pattern.sub(replacement, variant)
        # "Frutal" → "Frutales" only in candy contexts
        _CANDY_TYPES = {"GOMITAS", "CARAMELOS", "CHUPETINES", "CHUPETIN", "CHICLES",
                        "GOMITAS", "TURRON", "GOLOSINAS", "CONFITES", "PASTILLITAS"}
        if _ascii_fold(product_type or "").upper() in _CANDY_TYPES:
            variant = re.sub(r"\bFrutal\b", "Frutales", variant, flags=re.IGNORECASE)
    if _ascii_fold(brand or "").upper() == "9 DE ORO":
        if product_type == "Bizcochitos":
            product_type = "Bizcochos"
        if product_type == "Brownie":
            product_type = "Bizcochuelo"
            variant = "Brownie"
        if product_type in {"Galleta", "Galletita"}:
            product_type = "Galletitas"
        if product_type == "Brigitte":
            product_type = "Galletitas"
            if variant:
                variant = re.sub(r"^\s*Galletitas\s+", "", variant, flags=re.IGNORECASE)
                variant = f"Brigitte {variant}".strip()
            else:
                variant = "Brigitte"
        if product_type == "Snacks":
            product_type = "Crujitas"
            if variant:
                variant = re.sub(r"^\s*Crujitas\s+", "", variant, flags=re.IGNORECASE)
        if product_type == "Pepas" or (variant and re.search(r"\bPepas\b", variant, re.IGNORECASE)):
            product_type = "Pepas"
            variant = "Membrillo"
        if product_type == "Con" and variant:
            if re.fullmatch(r"Chips De Chocolate", variant, re.IGNORECASE):
                product_type = "Galletitas"
                variant = "Chips Chocolate"
            elif re.fullmatch(r"Chips De Chocolate Bco\.", variant, re.IGNORECASE):
                product_type = "Galletitas"
                variant = "Chips Chocolate Blanco"
        if variant == "Agridulce":
            variant = "Agridulces"
        if variant == "Agridulces Azucarados":
            variant = "Agridulces"
        if variant == "Azucaradas":
            variant = "Azucarados"
        if variant == "Clasico":
            variant = "Clasicos"
    if brand == "317" and product_type == "Tintura" and variant:
        variant = re.sub(
            r"^\s*Kit\s+N?(\d+(?:\.\d+)?)\b.*$",
            r"Kit \1",
            variant,
            flags=re.IGNORECASE,
        )

    return {
        "brand":        brand,
        "product_type": product_type,
        "variant":      variant,
        "size_value":   size[0] if size else None,
        "size_unit":    size[1] if size else None,
        "category":     normalize_category(category),
    }


# ---------------------------------------------------------------------------
# Category normalization
# ---------------------------------------------------------------------------

# Known cleanup patterns in category strings
_CATEGORY_FIXES: list[tuple[str, str]] = [
    # Fix double spaces
    (r"\s{2,}", " "),
    # Remove trailing/leading parenthetical qualifiers like "(Fiestas)"
    (r"\s*\([^)]+\)\s*$", ""),
]


def normalize_category(raw: str) -> str:
    """
    Clean up a Nini category string.

    The scraper already stores the leaf description (e.g. "Aceites Y Grasas"),
    so this function only performs minor cleanup: double spaces, parenthetical
    qualifiers, encoding artifacts.
    """
    result = unicodedata.normalize("NFKC", raw).strip()
    for pattern, replacement in _CATEGORY_FIXES:
        result = re.sub(pattern, replacement, result).strip()
    return result


# ---------------------------------------------------------------------------
# CLI — dry-run / coverage report
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import asyncio
    import os
    import asyncpg
    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    async def run() -> None:
        """Print postprocessed results and coverage stats for all Nini products."""
        pool = await asyncpg.create_pool(
            host=os.getenv("DB_HOST", "localhost"),
            port=int(os.getenv("DB_PORT", 5432)),
            database=os.getenv("DB_NAME", "prices"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASS"),
        )
        rows = await pool.fetch(
            "SELECT sku, name, category FROM products WHERE supplier='nini'"
        )
        await pool.close()

        total = len(rows)
        no_brand = no_type = no_size = 0

        sample_no_brand: list[str] = []
        sample_no_type: list[str] = []

        for r in rows:
            f = extract_features(r["name"], r["category"])
            if not f["brand"]:
                no_brand += 1
                if len(sample_no_brand) < 5:
                    sample_no_brand.append(r["name"])
            if not f["product_type"]:
                no_type += 1
                if len(sample_no_type) < 5:
                    sample_no_type.append(r["name"])
            if f["size_value"] is None:
                no_size += 1

        print(f"\n=== Nini postprocessing coverage ({total} products) ===")
        print(f"  Brand extracted   : {total - no_brand:5d} / {total}  ({(total-no_brand)/total*100:.1f}%)")
        print(f"  Product type found: {total - no_type:5d} / {total}  ({(total-no_type)/total*100:.1f}%)")
        print(f"  Size extracted    : {total - no_size:5d} / {total}  ({(total-no_size)/total*100:.1f}%)")

        if sample_no_brand:
            print(f"\n  Products without brand ({no_brand} total, showing up to 5):")
            for n in sample_no_brand:
                print(f"    {n!r}")

        if sample_no_type:
            print(f"\n  Products without product type ({no_type} total):")
            for n in sample_no_type:
                print(f"    {n!r}")

        # Print 20 random samples
        import random
        sample = random.sample(list(rows), min(20, total))
        print(f"\n{'RAW NAME':<55} {'BRAND':<20} {'TYPE':<28} {'VARIANT':<25} {'SIZE'}")
        print("-" * 145)
        for r in sample:
            f = extract_features(r["name"], r["category"])
            size_str = f"{f['size_value']}{f['size_unit']}" if f["size_value"] else ""
            print(
                f"{r['name']:<55} "
                f"{(f['brand'] or ''):<20} "
                f"{(f['product_type'] or ''):<28} "
                f"{(f['variant'] or ''):<25} "
                f"{size_str}"
            )

    asyncio.run(run())
