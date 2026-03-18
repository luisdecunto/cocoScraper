"""
Postprocessing for Vital product data.

Run as a standalone pass after scraping:
    python -m scraper.postprocess.vital

Functions are also importable for use in tests or other modules.
"""

from __future__ import annotations

import logging
import re

from scraper.postprocess._utils import (
    _DATA_DIR,
    _ascii_fold,
    _load_aliases,
    _load_lines,
    clean_name,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Load lookup data from text files
# ---------------------------------------------------------------------------

# Sorted longest-first so greedy matching works correctly
_KNOWN_PRODUCT_TYPES: list[str] = sorted(_load_lines("vital_product_types.txt"), key=lambda x: -len(x))
_KNOWN_PRODUCT_TYPES_FOLDED: list[tuple[str, str]] = [(_ascii_fold(pt), pt) for pt in _KNOWN_PRODUCT_TYPES]
_PRODUCT_TYPE_ALIAS_MAP: dict[str, str] = _load_aliases("vital_product_type_aliases.txt")

_KNOWN_BRANDS_RAW: list[str] = _load_lines("vital_brands.txt")
_BRAND_FOLD_MAP: dict[str, str] = {_ascii_fold(b): b for b in _KNOWN_BRANDS_RAW}
_KNOWN_BRANDS_FOLDED_SORTED: list[str] = sorted(_BRAND_FOLD_MAP.keys(), key=lambda x: -len(x))

# ---------------------------------------------------------------------------
# Measurement patterns
# ---------------------------------------------------------------------------


def _parse_number(s: str) -> float:
    """Parse a number that may use comma as decimal separator."""
    return float(s.replace(",", "."))


_MULTIPACK_RE = re.compile(
    r"\b(\d+)\s*[xX]\s*(\d+(?:[.,]\d+)?)\s*(kg|kilo|kilos|gr|grs|gramos|g|lts?|litros?|ml|cc|cm3)\b",
    re.IGNORECASE,
)

_WEIGHT_RE = re.compile(
    r"\b(\d+(?:[.,]\d+)?)\s*(kg|kilo|kilos|gr|grs|gramos|g)\b",
    re.IGNORECASE,
)

_VOLUME_RE = re.compile(
    r"\b(\d+(?:[.,]\d+)?)\s*(lts?|litros?|ml|cc|cm3)\b",
    re.IGNORECASE,
)

_UNITS_RE = re.compile(
    r"\b(?:x\s*(\d+)|(\d+)\s*un(?:d|idades?)?)\b",
    re.IGNORECASE,
)

_CONTAINER_RE = re.compile(
    r"\b(botella|pote|bolsa|caja|lata|frasco|sachet|saquito|doy\s*pack|pet|pvc|petaca|sobre|barra|tira)\b",
    re.IGNORECASE,
)

_WEIGHT_UNITS = {
    "kg": "kg",
    "kilo": "kg",
    "kilos": "kg",
    "gr": "g",
    "grs": "g",
    "gramos": "g",
    "g": "g",
}
_VOLUME_UNITS = {
    "lt": "l",
    "lts": "l",
    "litro": "l",
    "litros": "l",
    "l": "l",
    "ml": "ml",
    "cc": "ml",
    "cm3": "ml",
}


def _to_grams(value: float, unit: str) -> float:
    """Convert any weight value to grams."""
    return value * 1000 if _WEIGHT_UNITS[unit] == "kg" else value


def _to_ml(value: float, unit: str) -> float:
    """Convert any volume value to millilitres."""
    return value * 1000 if _VOLUME_UNITS[unit] == "l" else value


# ---------------------------------------------------------------------------
# Product-type and brand extraction
# ---------------------------------------------------------------------------

_BRAND_ARTICLES = {"LA", "EL", "LOS", "LAS", "LO", "LE", "DON", "SAN", "SANTA"}
_SKIP_AS_BRAND = {"DE", "DEL", "EN", "CON", "AL", "A", "Y"}

_BRAND_CORRECTIONS: dict[str, str] = {}


def _apply_product_type_alias(product_type: str | None) -> str | None:
    if product_type is None:
        return None
    folded = _ascii_fold(product_type.upper())
    if folded in _PRODUCT_TYPE_ALIAS_MAP:
        return clean_name(_PRODUCT_TYPE_ALIAS_MAP[folded])
    return product_type


def _extract_product_type(tokens: list[str]) -> tuple[str | None, int, bool]:
    """Return (product_type, consumed_words, matched_via_lookup)."""
    folded_text = _ascii_fold(" ".join(tokens))
    for folded_pt, canonical_pt in _KNOWN_PRODUCT_TYPES_FOLDED:
        if folded_text.startswith(folded_pt):
            product_type = clean_name(canonical_pt)
            return _apply_product_type_alias(product_type), len(canonical_pt.split()), True

    if not tokens:
        return None, 0, False

    return _apply_product_type_alias(tokens[0].capitalize()), 1, False


def _extract_brand_lookup(tokens: list[str]) -> tuple[str | None, int]:
    """Return (brand, consumed_words) when the start of tokens matches known brand."""
    folded_text = _ascii_fold(" ".join(tokens))
    for folded_b in _KNOWN_BRANDS_FOLDED_SORTED:
        if folded_text.startswith(folded_b):
            return clean_name(_BRAND_FOLD_MAP[folded_b]), len(folded_b.split())
    return None, 0


def _extract_brand(tokens: list[str]) -> tuple[str, int, str]:
    """
    Return (brand, consumed_words, source).

    source is one of: lookup, heuristic, generico.
    """
    brand, consumed = _extract_brand_lookup(tokens)
    if brand is not None:
        source = "lookup"
    else:
        source = "heuristic"
        working = list(tokens)
        consumed = 0

        if working and working[0] in _SKIP_AS_BRAND and len(working) > 1:
            working = working[1:]
            consumed += 1

        if working:
            first = working[0]
            if first in _BRAND_ARTICLES and len(working) >= 2:
                brand = clean_name(f"{working[0]} {working[1]}")
                consumed += 2
            else:
                brand = first.capitalize()
                consumed += 1
                if len(working) >= 2 and len(working[1]) <= 2 and working[1].isalpha():
                    brand = f"{brand} {working[1].upper()}"
                    consumed += 1
        else:
            brand = None

    if brand is not None:
        folded_brand = _ascii_fold(brand.upper())
        if folded_brand in _BRAND_CORRECTIONS:
            brand = _BRAND_CORRECTIONS[folded_brand]

    if brand is None:
        return "Generico", 0, "generico"

    return brand, consumed, source


def _parse_type_first(tokens: list[str]) -> dict:
    product_type, type_words, type_lookup = _extract_product_type(tokens)
    remaining_after_type = tokens[type_words:]

    brand, brand_words, brand_source = _extract_brand(remaining_after_type)
    remaining = remaining_after_type[brand_words:] if brand_words else remaining_after_type

    return {
        "product_type": product_type,
        "brand": brand,
        "remaining": remaining,
        "product_type_lookup": type_lookup,
        "brand_source": brand_source,
        "matched_words": type_words + (brand_words if brand_source == "lookup" else 0),
    }


def _parse_brand_first(tokens: list[str]) -> dict | None:
    """
    Vital names can also appear as BRAND + PRODUCT + VARIANT.
    Try this parse order only when the name starts with a known brand.
    """
    brand, brand_words = _extract_brand_lookup(tokens)
    if brand is None:
        return None

    remaining_after_brand = tokens[brand_words:]
    product_type, type_words, type_lookup = _extract_product_type(remaining_after_brand)
    remaining = remaining_after_brand[type_words:]

    return {
        "product_type": product_type,
        "brand": brand,
        "remaining": remaining,
        "product_type_lookup": type_lookup,
        "brand_source": "lookup",
        "matched_words": brand_words + type_words,
    }


def _extract_product_type_and_brand(tokens: list[str]) -> tuple[str | None, str, list[str], str]:
    """
    Return (product_type, brand, remaining_tokens, brand_source).

    Primary parse is PRODUCT -> BRAND (same as maxiconsumo).
    If that fails to lookup-match a brand, fallback to BRAND -> PRODUCT to
    handle Vital names where brand leads.
    """
    type_first = _parse_type_first(tokens)
    brand_first = _parse_brand_first(tokens)

    if brand_first is None:
        chosen = type_first
    elif type_first["brand_source"] != "lookup" and brand_first["brand_source"] == "lookup":
        chosen = brand_first
    elif (
        brand_first["brand_source"] == "lookup"
        and type_first["brand_source"] == "lookup"
        and brand_first["matched_words"] > type_first["matched_words"]
        and (brand_first["product_type_lookup"] or not type_first["product_type_lookup"])
    ):
        chosen = brand_first
    else:
        chosen = type_first

    return (
        chosen["product_type"],
        chosen["brand"],
        chosen["remaining"],
        chosen["brand_source"],
    )


# ---------------------------------------------------------------------------
# Main feature extraction
# ---------------------------------------------------------------------------


def extract_features(name: str) -> dict:
    """
    Extract structured features from a raw Vital product name.

    Returns a dict with keys:
        product_type   str | None
        brand          str
        variant        str | None
        weight         {"value": float, "unit": str} | None
        volume         {"value": float, "unit": str} | None
        units_in_name  int | None
        clean_name     str
        _brand_source  str  (lookup | heuristic | generico)
    """
    text = name.upper()
    weight = None
    volume = None
    units_in_name = None

    m = _MULTIPACK_RE.search(text)
    if m:
        units_in_name = int(m.group(1))
        per_unit_val = _parse_number(m.group(2))
        unit_raw = m.group(3).lower()
        if unit_raw in _WEIGHT_UNITS:
            weight = {"value": _to_grams(per_unit_val, unit_raw), "unit": "g"}
        else:
            volume = {"value": _to_ml(per_unit_val, unit_raw), "unit": "ml"}
        text = text[: m.start()] + text[m.end() :]

    if weight is None:
        m = _WEIGHT_RE.search(text)
        if m:
            val = _parse_number(m.group(1))
            unit_raw = m.group(2).lower()
            weight = {"value": _to_grams(val, unit_raw), "unit": "g"}
            text = text[: m.start()] + text[m.end() :]

    if volume is None:
        m = _VOLUME_RE.search(text)
        if m:
            val = _parse_number(m.group(1))
            unit_raw = m.group(2).lower()
            volume = {"value": _to_ml(val, unit_raw), "unit": "ml"}
            text = text[: m.start()] + text[m.end() :]

    if units_in_name is None:
        m = _UNITS_RE.search(text)
        if m:
            units_in_name = int(m.group(1) or m.group(2))
            text = text[: m.start()] + text[m.end() :]

    tokens_raw = text.split()
    if tokens_raw:
        first_token = tokens_raw[0]
        rest = _CONTAINER_RE.sub("", " ".join(tokens_raw[1:])).split()
        tokens = [first_token] + rest
    else:
        tokens = []

    product_type, brand, remaining, brand_source = _extract_product_type_and_brand(tokens)
    variant = clean_name(" ".join(remaining)) if remaining else None
    if variant == "":
        variant = None
    if re.search(r"\bINDIVIDUAL\s+BLOCK\s*PRINT\b|\bINDIVIDUAL\s+BLOCKPRINT\b", name, re.IGNORECASE):
        brand = "Blockprint"
        product_type = "Individual"
        if re.search(r"\bAZUL\b", name, re.IGNORECASE):
            variant = "Azul"
        elif re.search(r"\bGRIS\b", name, re.IGNORECASE):
            variant = "Gris"
        if re.search(r"\b6\s*U\b", name, re.IGNORECASE):
            units_in_name = 6
    elif re.search(r"\bMANTEL\s+BLOCK\s*PRINT\b|\bMANTEL\s+BLOCKPRINT\b", name, re.IGNORECASE):
        brand = "Generico"
        product_type = "Mantel"
        if re.search(r"\bAZUL\b", name, re.IGNORECASE):
            variant = "Print Azul" if re.search(r"\b2[.,]00\s*X\s*3[.,]00\b", name, re.IGNORECASE) else "Azul"
        elif re.search(r"\bGRIS\b", name, re.IGNORECASE):
            variant = "Print Gris" if re.search(r"\b2[.,]00\s*X\s*3[.,]00\b", name, re.IGNORECASE) else "Gris"
        if re.search(r"\b160\s*X\s*200\s*CM\b", name, re.IGNORECASE):
            units_in_name = None
    if _ascii_fold(brand or "").upper() == "SPEED":
        if variant:
            variant = re.sub(r"\bLata\b", "", variant, flags=re.IGNORECASE)
            variant = re.sub(r"^\s*473/\s*$", "", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\s+", " ", variant).strip() or None
        if volume is not None and int(round(volume["value"])) == 500 and re.search(r"\b473/500\b", name, re.IGNORECASE):
            volume["value"] = 473
    if _ascii_fold(brand or "").upper() == "STELLA ARTOIS" and variant:
        variant = re.sub(r"\b0\.0%\s*S/Alcohol\b", "S/Alcohol", variant, flags=re.IGNORECASE)
        variant = re.sub(r"\bLata\b", "", variant, flags=re.IGNORECASE)
        variant = re.sub(r"\s+", " ", variant).strip() or None
    if _ascii_fold(brand or "").upper() == "AUTOR" and variant:
        variant = re.sub(r"\b500\s+Hojas\b", "", variant, flags=re.IGNORECASE)
        variant = re.sub(r"\s+", " ", variant).strip() or None
    if _ascii_fold(brand or "").upper() == "CLUB SOCIAL":
        if _ascii_fold(product_type or "").upper() in {"GALLET.", "GALLETITA", "GALLETITAS"}:
            product_type = "Galletitas"
        if variant and re.fullmatch(r"Agrupado", variant, re.IGNORECASE):
            variant = "Original"
        if weight is not None and int(round(weight["value"])) == 24:
            weight["value"] = 141
    if _ascii_fold(brand or "").upper() == "BAY BISCUIT":
        if _ascii_fold(product_type or "").upper() in {"GALLET.", "GALLETITA", "GALLETITAS"}:
            product_type = "Galletitas"
    if _ascii_fold(brand or "").upper() == "BAYGON":
        product_type = "Insecticida"
        if variant and re.fullmatch(r"M\.?M\.?M\.?|Mata Moscas y Mosquitos", variant, re.IGNORECASE):
            variant = "Mata Moscas y Mosquitos"
    if _ascii_fold(brand or "").upper() == "BAYGON" and re.search(r"\bMatacucarachas\b", name, re.IGNORECASE):
        product_type = "Insecticida"
        variant = "Matacucarachas"
    if _ascii_fold(brand or "").upper() == "ACQUA DI COLBERT" and _ascii_fold(product_type or "").upper() == "DESODORANTE MASCULINO":
        product_type = "Desodorante"
    if re.search(r"\bALARIS\b", text, re.IGNORECASE):
        brand = "Trapiche Alaris"
        if variant:
            variant = re.sub(r"^\s*Alaris\s+", "", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\s+", " ", variant).strip(" .") or None
    if _ascii_fold(brand or "").upper() == "COFLER BLOCK":
        if _ascii_fold(product_type or "").upper() == "ALFAJOR" and variant and re.fullmatch(r"I", variant, re.IGNORECASE):
            variant = "Individual"
        if _ascii_fold(product_type or "").upper() == "HUEVO PASCUA":
            product_type = "Huevo de Pascua"
        if _ascii_fold(product_type or "").upper() == "GALLETITA":
            product_type = "Galletitas"
    if _ascii_fold(brand or "").upper() in {"DRF", "D R F"} or re.search(r"\bD\s*R\s*F\b", name, re.IGNORECASE):
        brand = "Drf"
        if _ascii_fold(product_type or "").upper() == "CARAMELOS DUROS":
            product_type = "Pastillas"
        if variant:
            variant = re.sub(r"#", "", variant)
            variant = re.sub(r"\s+", " ", variant).strip() or None
        weight = None
        volume = None
        units_in_name = None

    parts = [p for p in [product_type, brand, variant] if p]
    clean = " ".join(parts)

    return {
        "product_type": product_type,
        "brand": brand,
        "variant": variant,
        "weight": weight,
        "volume": volume,
        "units_in_name": units_in_name,
        "clean_name": clean,
        "_brand_source": brand_source,
    }


# ---------------------------------------------------------------------------
# Category parsing
# ---------------------------------------------------------------------------

_CATEGORY_FIXES: dict[str, str] = {}


def parse_category(raw: str) -> dict:
    """
    Split a category path into section/subsection/leaf.

    Vital categories are usually single-level names, but this parser supports
    delimited paths for consistency with other suppliers.
    """
    result = raw
    for bad, good in _CATEGORY_FIXES.items():
        result = result.replace(bad, good)

    parts = [p.strip() for p in result.split(">")]
    return {
        "section": parts[0] if len(parts) > 0 else None,
        "subsection": parts[1] if len(parts) > 1 else None,
        "leaf": parts[2] if len(parts) > 2 else None,
    }


def normalize_category(raw: str) -> str:
    """Return the full category path as a normalized string."""
    cat = parse_category(raw)
    parts = [p for p in [cat["section"], cat["subsection"], cat["leaf"]] if p]
    return " > ".join(parts)


# ---------------------------------------------------------------------------
# CLI - dry-run preview
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import asyncio
    import os
    from pathlib import Path

    import asyncpg
    from dotenv import load_dotenv

    load_dotenv(dotenv_path=Path(".env"))
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    async def run() -> None:
        """Print postprocessed results for 20 random Vital products."""
        pool = await asyncpg.create_pool(
            host=os.getenv("DB_HOST", "localhost"),
            port=int(os.getenv("DB_PORT", 5432)),
            database=os.getenv("DB_NAME", "prices"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASS"),
        )
        rows = await pool.fetch(
            "SELECT sku, name, category FROM products "
            "WHERE supplier = 'vital' ORDER BY RANDOM() LIMIT 20"
        )
        await pool.close()

        print(
            f"\n{'RAW NAME':<50} {'TYPE':<22} {'BRAND':<20} {'VARIANT':<25} "
            f"{'WEIGHT':<12} {'VOLUME':<12} {'UN':<4} {'SECTION':<15} {'LEAF'}"
        )
        print("-" * 185)
        for r in rows:
            f = extract_features(r["name"])
            c = parse_category(r["category"])
            w = f"{f['weight']['value']}{f['weight']['unit']}" if f["weight"] else ""
            v = f"{f['volume']['value']}{f['volume']['unit']}" if f["volume"] else ""
            print(
                f"{r['name']:<50} "
                f"{(f['product_type'] or ''):<22} "
                f"{(f['brand'] or ''):<20} "
                f"{(f['variant'] or ''):<25} "
                f"{w:<12} "
                f"{v:<12} "
                f"{str(f['units_in_name'] or ''):<4} "
                f"{(c['section'] or ''):<15} "
                f"{c['leaf'] or ''}"
            )

    asyncio.run(run())
