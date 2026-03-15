"""
Postprocessing for Luvik product data.

Name format: [PRODUCT_TYPE] [BRAND] [VARIANT] [SIZE UNIT]
- Product type always comes FIRST — often abbreviated (FID.=Fideos, MERM.=Mermelada, etc.)
- Brand follows immediately after type
- Variant/descriptor after brand
- Size (number + unit) at the end, unit may have trailing period (CC., Un., ml.)

Run as a standalone pass after scraping:
    python -m scraper.postprocess.luvik

Functions are also importable for tests or other modules.
"""

import logging
import re
import unicodedata
from pathlib import Path

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).parent / "data"


# ---------------------------------------------------------------------------
# Loader helpers
# ---------------------------------------------------------------------------

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
    """Strip accents for accent-insensitive matching (ñ→N, á→A, etc.).

    U+FFFD (replacement character) appears in DB when Ñ (or other non-ASCII)
    is lost due to encoding corruption — treat as N for Spanish text matching.
    """
    text = text.replace("\ufffd", "N")
    return unicodedata.normalize("NFD", text).encode("ascii", "ignore").decode("ascii")


def _load_type_aliases(filename: str) -> dict[str, str]:
    """
    Load KEY=Canonical alias pairs. Keys are uppercased + ascii-folded for matching.
    Returns dict[folded_upper_key → canonical_display_string].
    """
    path = _DATA_DIR / filename
    if not path.exists():
        return {}
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, canonical = line.partition("=")
        folded = _ascii_fold(key.strip()).upper()
        result[folded] = canonical.strip()
    return result


# ---------------------------------------------------------------------------
# Product type aliases
# Sorted: longest key first (word count DESC, then char length DESC).
# Multi-word types must be tried before their single-word prefixes.
# ---------------------------------------------------------------------------
_PT_ALIAS_MAP: dict[str, str] = _load_type_aliases("luvik_product_types.txt")
_PT_ALIAS_SORTED: list[str] = sorted(
    _PT_ALIAS_MAP.keys(),
    key=lambda k: (-len(k.split()), -len(k)),
)

# ---------------------------------------------------------------------------
# Brand list — multi-word brands the single-token heuristic would split
# ---------------------------------------------------------------------------
_BRANDS_RAW: list[str] = _load_lines("luvik_brands.txt")
_BRANDS_FOLDED: list[str] = sorted(
    [_ascii_fold(b).upper() for b in _BRANDS_RAW],
    key=lambda x: -len(x),
)
# Map folded → original-case canonical brand
_BRAND_FOLD_MAP: dict[str, str] = {
    _ascii_fold(b).upper(): b for b in _BRANDS_RAW
}

# Articles that signal a 2-word brand: "SAN FELIPE", "LA SERENISIMA"
_BRAND_ARTICLES = {"LA", "EL", "LOS", "LAS", "SAN", "SANTA", "DON", "DOG", "OLD", "NEW",
                   "CLOSE", "ORAL"}

# Tokens that are clearly not stand-alone brands (stop the heuristic)
_NOT_A_BRAND = {"DE", "DEL", "AL", "CON", "SIN", "PARA", "Y", "E", "S/", "C/", "P/",
                "X", "EXTRA", "SUPER", "ULTRA", "LIGHT", "DIET", "NATURAL", "0%",
                "S/AZUCAR", "D/P", "F/P", "D/T"}

# Post-extraction brand normalization: fixes typos, encoding corruption, and
# capitalization inconsistencies found via similarity analysis.
# Keys are _ascii_fold(brand).upper() of the incorrect form.
_BRAND_NORMALIZATIONS: dict[str, str] = {
    "CANUELAS":      "Cañuelas",       # Ñ stored as U+FFFD in some rows
    "MR.MUSCULO":    "Mr.Músculo",     # capitalization inconsistency
    "GENTELMAN":     "Gentleman",      # typo in source data
    "DCE GUSTO":     "Dolce Gusto",    # abbreviated/corrupt form of Dolce Gusto
    "CRECIENTE":     "C. Creciente",   # missing "C." prefix (same brand)
    "C. CRECIENTE":  "C. Creciente",   # all-caps from brand list → canonical
    "TRES NINAS":    "Las Tres Niñas", # both spellings → canonical
    "LAS TRES NINAS":"Las Tres Niñas",
}


# ---------------------------------------------------------------------------
# Size extraction
# ---------------------------------------------------------------------------

# Standard size: NUMBER UNIT[.] anchored to end.
# NUMBER can be plain (500) or multipack (3x125, 2x200) — NxM returns total.
_SIZE_RE = re.compile(
    r"""
    \s+
    (\d+[xX]\d+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)  # NxM or plain number
    \s*
    (                           # unit — case-insensitive
      Kgs?\.?  |               # Kgs, Kg, Kgs., Kg.
      Grs?\.?  |               # Grs, Gr, Grs., Gr.
      (?<!\d)G\.? |            # G alone (not part of a longer number), e.g. "102 G"
      Ml\.?    |               # Ml, Ml.
      L[Tt]s?\.? |             # Lt, Lts, Lt., Lts.
      [Cc][Cc]\.? |            # CC, Cc, CC., Cc.
      Un\.?    |               # Un, Un.
      Mts?\.?  |               # Mts, Mt, Mts., Mt.
      Cms?\.?  |               # Cm, Cms (centimetres)
      W\.?     |               # W (watts) — lamps, appliances e.g. "20W", "50W"
      wa\.?                    # wa (watts abbreviation) — e.g. "860 wa", "710 wa"
    )
    \s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Standalone " Un." with no preceding number — means 1 unit (single item).
_UN_SUFFIX_RE = re.compile(r"\s+Un\.?\s*$", re.IGNORECASE)

# Color-temperature / light-tone suffixes on lamp products (e.g. "18W FRIA", "18W CALIDA").
# Strip before size matching so the watt regex can anchor to end-of-string.
_COLOR_TEMP_RE = re.compile(r"\s+(?:FRIA|CALIDA|NEUTRA|BLANCA|AMARILLA)\s*$", re.IGNORECASE)

# Embedded watt pattern: number directly adjacent to W/wa without a preceding space
# (e.g. "CUAD.18W", "RED.18W"). Used only after stripping color-temp suffix.
_WATT_EMBEDDED_RE = re.compile(r"(?<!\d)(\d+(?:[.,]\d+)?)\s*(W\.?|wa\.?)\s*$", re.IGNORECASE)

# Bonus-pack format: "15+2 Kgs" — extract main quantity only (ignore the bonus).
_BONUS_RE = re.compile(
    r"\s+(\d+)\+\d+\s*(Kgs?\.?|Grs?\.?|Ml\.?|L[Tt]s?\.?)\s*$",
    re.IGNORECASE,
)

_WEIGHT_UNITS = {"kg": "kg", "kgs": "kg", "gr": "g", "grs": "g", "g": "g",
                 # "G" alone (e.g. "102 G") — same as grams
                 }
_VOLUME_UNITS = {"lt": "l", "lts": "l", "ml": "ml", "cc": "ml"}
_POWER_UNITS  = {"w": "W", "wa": "W"}    # watts — lamps, appliances

# Bare trailing number with no unit (some products in source data lack unit).
# Returned as sentinel unit "__bare__"; unit is resolved later using product type.
_BARE_NUMBER_RE = re.compile(r"\s+(\d+[xX]\d+(?:[.,]\d+)?|\d+\.?\d*)\s*$")

# Product types (ascii-folded+upper canonical) whose natural unit is ml or g.
# Used to resolve bare numbers extracted without a unit.
_ML_PRODUCT_TYPES = {
    "DESODORANTE", "ANTITRANSPIRANTE", "SHAMPOO", "ACONDICIONADOR",
    "JABON LIQUIDO", "SUAVIZANTE", "GEL FIJADOR", "GEL ANTIBACTERIAL",
    "CREMA PARA PEINAR", "CREMA HIDRATANTE", "CREMA CORPORAL", "CREMA FACIAL",
    "CREMA DEPILATORIA", "CREMA DE ENJUAGUE", "CREMA DE TRATAMIENTO",
    "PROTECTOR SOLAR", "PROT.SOLAR", "GEL MICELAR", "AGUA MICELAR",
    "VINO", "ESPUMANTE", "CHAMPAGNE", "LICOR", "WHISKY", "VODKA", "FERNET",
}
_G_PRODUCT_TYPES = {
    "PROTECTOR LABIAL",
    "JABON GLICERINA", "JABON PASTILLA", "JABON TOCADOR",
}


def _parse_size_match(m) -> tuple[float, str] | None:
    """Parse a _SIZE_RE match into (value, canonical_unit)."""
    raw_num = m.group(1).replace(",", ".")
    raw_unit = m.group(2).rstrip(".").lower()

    # Multipack NxM: return total (N * M)
    if "x" in raw_num.lower():
        parts = raw_num.lower().split("x")
        n, per_unit = int(parts[0]), float(parts[1])
        raw_val = n * per_unit
    else:
        raw_val = float(raw_num)

    if raw_unit in _WEIGHT_UNITS:
        if _WEIGHT_UNITS[raw_unit] == "kg":
            return (raw_val * 1000, "g")
        return (raw_val, "g")

    if raw_unit in _VOLUME_UNITS:
        if _VOLUME_UNITS[raw_unit] == "l":
            return (raw_val * 1000, "ml")
        return (raw_val, "ml")

    if raw_unit == "un":
        return (raw_val, "uni")

    if raw_unit in ("mt", "mts"):
        return (raw_val, "m")

    if raw_unit in _POWER_UNITS:
        return (raw_val, "W")

    return (raw_val, raw_unit)


def extract_size(name: str) -> tuple[float, str] | None:
    """
    Extract trailing size from a Luvik product name.

    Returns (value_in_base_unit, canonical_unit) or None.
    Canonical units: "g" (grams), "ml" (millilitres), "uni", "m" (metres).

    Handles:
    - Standard: "500 Grs", "1.5 Lt.", "750 CC."
    - Multipack: "3x125 Grs" → total weight = 375 g
    - Standalone Un.: "BOLLITO DE ACERO Un." → (1.0, "uni")
    - Preceding size + trailing Un.: "FILM 15 Mts Un." → (15.0, "m")
    """
    m = _SIZE_RE.search(name)
    if m:
        return _parse_size_match(m)

    # Strip color-temperature suffix from lamp/appliance names (e.g. "18W FRIA") so
    # the watt regex can anchor to end-of-string.
    name_no_ct = _COLOR_TEMP_RE.sub("", name)
    if name_no_ct != name:
        m_ct = _SIZE_RE.search(name_no_ct)
        if m_ct:
            return _parse_size_match(m_ct)
        # Watts may be directly adjacent to letters/punctuation (e.g. "CUAD.18W")
        m_we = _WATT_EMBEDDED_RE.search(name_no_ct)
        if m_we:
            raw_val = float(m_we.group(1).replace(",", "."))
            return (raw_val, "W")

    # Bonus-pack: "15+2 Kgs" → extract the main quantity (15 Kgs)
    mb = _BONUS_RE.search(name)
    if mb:
        raw_val = float(mb.group(1))
        raw_unit = mb.group(2).rstrip(".").lower()
        if raw_unit in _WEIGHT_UNITS:
            if _WEIGHT_UNITS[raw_unit] == "kg":
                return (raw_val * 1000, "g")
            return (raw_val, "g")
        if raw_unit in _VOLUME_UNITS:
            if _VOLUME_UNITS[raw_unit] == "l":
                return (raw_val * 1000, "ml")
            return (raw_val, "ml")

    # Try stripping standalone trailing "Un." then re-match the preceding size.
    # E.g. "FILM VIRULANA 15 Mt Un." → strip "Un." → match "15 Mt"
    name_no_un = _UN_SUFFIX_RE.sub("", name)
    if name_no_un != name:
        m2 = _SIZE_RE.search(name_no_un)
        if m2:
            return _parse_size_match(m2)
        # Un. alone with no other size → treat as 1 unit
        return (1.0, "uni")

    # Last resort: bare trailing number or NxM multipack with no unit
    # (e.g. "VINO MALBEC 750", "DESODORANTE REXONA 150", "JABON 3x120").
    # Unit resolved later via product type context.
    # The raw string is encoded into the sentinel so display can preserve "3x120 g".
    mb2 = _BARE_NUMBER_RE.search(name)
    if mb2:
        raw = mb2.group(1)
        if "x" in raw.lower():
            parts = raw.lower().split("x")
            val = int(parts[0]) * float(parts[1].replace(",", "."))
        else:
            val = float(raw.rstrip("."))
        return (val, "__bare__" + raw)

    return None


# ---------------------------------------------------------------------------
# Name cleaning
# ---------------------------------------------------------------------------

def clean_name(raw: str) -> str:
    """Strip extra whitespace and apply NFKC unicode normalization."""
    text = unicodedata.normalize("NFKC", raw)
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# Product type extraction
# ---------------------------------------------------------------------------

def extract_product_type(tokens: list[str]) -> tuple[str, int]:
    """
    Match the product type from the leading tokens.

    Strategy:
      1. Try the alias map (longest match first) — handles abbreviations and
         known multi-word types (FID. AL HUEVO → "Fideos al Huevo").
      2. Fallback: first token, title-cased as-is.

    Returns (canonical_type_string, num_tokens_consumed).
    """
    if not tokens:
        return ("Desconocido", 0)

    # Try alias map (sorted longest-first)
    folded_tokens = [_ascii_fold(t).upper() for t in tokens]
    for key in _PT_ALIAS_SORTED:
        key_words = key.split()
        n = len(key_words)
        if len(folded_tokens) >= n and folded_tokens[:n] == key_words:
            return (_PT_ALIAS_MAP[key], n)

    # Fallback: use first token as-is, title-cased
    return (tokens[0].capitalize(), 1)


# ---------------------------------------------------------------------------
# Brand extraction
# ---------------------------------------------------------------------------

def extract_brand(tokens: list[str]) -> tuple[str, int]:
    """
    Extract brand from leading tokens (called after type tokens removed).

    Strategy:
      1. Try known brand list (longest match, accent-insensitive).
      2. Article heuristic: if first token is in _BRAND_ARTICLES, take 2 tokens.
      3. Fallback: single first token (if not in _NOT_A_BRAND set).

    Returns (brand_string, num_tokens_consumed).
    If no brand is detectable, returns ("Generico", 0).
    """
    if not tokens:
        return ("Generico", 0)

    # Skip all leading descriptor/stop-words (e.g. LIGHT, NATURAL, EXTRA)
    # before any brand matching so the explicit list is checked on effective tokens.
    skip = 0
    while skip < len(tokens) and _ascii_fold(tokens[skip]).upper() in _NOT_A_BRAND:
        skip += 1
    if skip == len(tokens):
        return ("Generico", 0)

    eff_tokens = tokens[skip:]
    eff_folded = [_ascii_fold(t).upper() for t in eff_tokens]

    # 1. Explicit brand list on effective tokens (longest match wins)
    for fb in _BRANDS_FOLDED:
        fb_words = fb.split()
        n = len(fb_words)
        if eff_folded[:n] == fb_words:
            return (_BRAND_FOLD_MAP[fb], skip + n)

    first = eff_folded[0]

    # 2. Article heuristic: "SAN FELIPE", "LA SERENISIMA", etc.
    if first in _BRAND_ARTICLES and len(eff_tokens) >= 2:
        brand = f"{eff_tokens[0]} {eff_tokens[1]}"
        return (brand, skip + 2)

    # 3. Single-token brand — take it as-is
    return (eff_tokens[0], skip + 1)


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------

def extract_features(name: str, category: str = "") -> dict:
    """
    Extract structured features from a raw Luvik product name.

    Returns a dict with keys:
        product_type   str | None
        brand          str | None
        variant        str | None   — everything between brand and size
        size_value     float | None — canonical value (g, ml, uni, m)
        size_unit      str | None   — "g", "ml", "uni", "m"
        category       str          — normalized category
    """
    name = clean_name(name)

    # 1. Extract and strip trailing size
    size = extract_size(name)
    bare_number: float | None = None
    bare_raw: str | None = None  # original token string, e.g. "3x120" or "750"
    if size and size[1].startswith("__bare__"):
        # Bare number found — defer unit resolution until after product type known
        bare_number = size[0]
        bare_raw = size[1][len("__bare__"):]
        size = None
        name_no_size = _BARE_NUMBER_RE.sub("", name).strip()
    elif size:
        name_no_size = _SIZE_RE.sub("", name).strip()
    else:
        name_no_size = name

    # 2. Tokenize
    tokens = name_no_size.split()

    # 2b. Brand-first pre-check: some names start directly with the brand and have
    #     no preceding product type (e.g. "ROJO + NARANJA LATA" where ROJO + is
    #     the brand). Only fires for explicit-list brands containing non-alpha chars
    #     (numbers, +, etc.) so it doesn't interfere with normal TYPE BRAND VARIANT.
    folded_all = [_ascii_fold(t).upper() for t in tokens]
    brand_first = None
    brand_first_n = 0
    for fb in _BRANDS_FOLDED:
        fb_words = fb.split()
        n = len(fb_words)
        if (n >= 2
                and any(not w.isalpha() for w in fb_words)
                and folded_all[:n] == fb_words):
            brand_first = _BRAND_FOLD_MAP[fb]
            brand_first_n = n
            break
    if brand_first:
        variant = " ".join(tokens[brand_first_n:]).strip() or None
        return {
            "product_type": None,
            "brand":        brand_first,
            "variant":      variant,
            "size_value":   size[0] if size else None,
            "size_unit":    size[1] if size else None,
            "category":     normalize_category(category),
        }

    # 3. Product type (leading tokens)
    product_type, pt_count = extract_product_type(tokens)
    remaining = tokens[pt_count:]

    # 4. Brand (next tokens)
    brand, br_count = extract_brand(remaining)
    remaining = remaining[br_count:]

    # 4b. Normalize brand (typos, encoding corruption, capitalization)
    brand = _BRAND_NORMALIZATIONS.get(_ascii_fold(brand).upper(), brand)

    # 5. Variant = whatever is left
    variant = " ".join(remaining).strip() or None

    # 5b. Resolve bare number unit using product type context
    size_display: str | None = None
    if bare_number is not None:
        pt_folded = _ascii_fold(product_type or "").upper()
        if pt_folded in _ML_PRODUCT_TYPES:
            size = (bare_number, "ml")
        elif pt_folded in _G_PRODUCT_TYPES:
            size = (bare_number, "g")
        # else: unit unknown — leave size=None
        # For multipack (NxM), preserve display as "3x120 g" instead of "360 g"
        if size and bare_raw and "x" in bare_raw.lower():
            size_display = f"{bare_raw} {size[1]}"

    return {
        "product_type": product_type,
        "brand":        brand,
        "variant":      variant,
        "size_value":   size[0] if size else None,
        "size_unit":    size[1] if size else None,
        "size_display": size_display,
        "category":     normalize_category(category),
    }


# ---------------------------------------------------------------------------
# Category normalization
# ---------------------------------------------------------------------------

# Map Shopify slug → canonical category name.
# Source: 193 categories from tiendaluvik.com.ar nav.
# Goals: fix encoding artifacts, collapse near-duplicates, drop numeric suffixes.
_CATEGORY_MAP: dict[str, str] = {
    # Encoding fixes
    "Instantaeo":           "Instantáneo",
    "Panales":              "Pañales",
    "Panal":                "Pañal",
    "Panal Para Adulto":    "Pañal para Adulto",
    # Verbose/opaque slug cleanup
    "Non Food 1":           "No Alimentario",
    "T Femenina":           "Higiene Femenina",
    "Rtd Bebidas S Alcohol": "RTD Sin Alcohol",
    "Rtd":                  "RTD",
    "Alimentos 1":          "Alimentos",
    "Cuidado Hogar 1":      "Cuidado del Hogar",
    "Cuidado Bebe 1":       "Cuidado del Bebé",
    "Higiene Mascotas 1":   "Higiene Mascotas",
    "Fiestas 1":            "Fiestas",
    "Frutos Secos 1":       "Frutos Secos",
    "Obleas 1":             "Obleas",
    "Condimentos 1":        "Condimentos",
    "Saquitos 1":           "Saquitos",
    "Saborizadas 1":        "Saborizadas",
    "Oleo 1":               "Óleo",
    "Poroto 1":             "Poroto",
    "Lenteja 1":            "Lenteja",
    "Garbanzo 1":           "Garbanzo",
    "Maiz 1":               "Maíz",
    "Maiz 2":               "Maíz",
    "Maiz":                 "Maíz",
    "Arroz 1":              "Arroz",
    "Arroz 2":              "Arroz",
    "Crema 1":              "Crema",
    "Crema 2":              "Crema",
    "Light 1":              "Light",
    "Ofertas Home 1":       "Ofertas",
    # Gastronomía suffix variants → keep gastronomía in label
    "Girasol Para Gastronomia":          "Aceite Girasol Gastronomía",
    "Aceites Para Gastronomia":          "Aceites Gastronomía",
    "Arroz Para Gastronomia":            "Arroz Gastronomía",
    "Azucar Para Gastronomia 1":         "Azúcar Gastronomía",
    "Caldos Para Gastronomia":           "Caldos Gastronomía",
    "Comun Para Gastronomia":            "Fideos Comunes Gastronomía",
    "Condimentos Para Gastronomia":      "Condimentos Gastronomía",
    "Conservas Para Gastronomia":        "Conservas Gastronomía",
    "Encurtidos Gastronim":              "Encurtidos Gastronomía",
    "Especialidad Para Gastronomia":     "Especialidad Gastronomía",
    "Fina Para Gastronomia":             "Fina Gastronomía",
    "Frutas Para Gastronomia":           "Frutas Gastronomía",
    "Infusiones Para Gastronomia":       "Infusiones Gastronomía",
    "Ketchup Para Gastronomia":          "Ketchup Gastronomía",
    "Lacteos Para Gastronomia":          "Lácteos Gastronomía",
    "Mate Cocido Para Gastronomia":      "Mate Cocido Gastronomía",
    "Mayonesa Para Gastronomia":         "Mayonesa Gastronomía",
    "Mostaza Para Gastronomia":          "Mostaza Gastronomía",
    "Oliva Gastronomia":                 "Aceite Oliva Gastronomía",
    "Pan Rallado Para Gastronomia":      "Pan Rallado Gastronomía",
    "Panificados Para Gastronomia":      "Panificados Gastronomía",
    "Parboil Para Gastronomia":          "Arroz Parboil Gastronomía",
    "Quesos Para Gastronomia":           "Quesos Gastronomía",
    "Sal Para Gastronomia":              "Sal Gastronomía",
    "Salsa Golf Para Gastronomia":       "Salsa Golf Gastronomía",
    "Salsas 1":                          "Salsas",
    "Te Para Gastronomia":               "Té Gastronomía",
    "Tomates Para Gastronomia":          "Tomates Gastronomía",
    "Tradicional Para Gastronomia":      "Tradicional Gastronomía",
    "Variedades Para Gastronomia":       "Variedades Gastronomía",
    "Vegetales Para Gastronomia":        "Vegetales Gastronomía",
    "Vinagres Para Gastronomia":         "Vinagres Gastronomía",
    "Yerbas Para Gastronomia":           "Yerbas Gastronomía",
    # Tiyuca is a mate brand used as a collection
    "Tiyuca":               "Tereré / Mate Bebida",
    # Tea subcategories
    "Te Variedades":        "Té Variedades",
    "Te":                   "Té",
}

# Regex to strip trailing numeric disambiguators Shopify adds: " 1", " 2", " 3"
# Only applied if the category is NOT already in _CATEGORY_MAP
_TRAILING_NUMBER_RE = re.compile(r"\s+\d+$")


def normalize_category(raw: str) -> str:
    """
    Normalize a Luvik Shopify category slug (already title-cased by the scraper).

    Applies explicit remapping first, then strips trailing numeric suffixes,
    then returns the cleaned string as-is.
    """
    raw = clean_name(raw)
    if raw in _CATEGORY_MAP:
        return _CATEGORY_MAP[raw]
    # Strip trailing disambiguation number (e.g. "Arroz 1" not already in map)
    cleaned = _TRAILING_NUMBER_RE.sub("", raw).strip()
    return cleaned if cleaned else raw


# ---------------------------------------------------------------------------
# CLI — coverage report
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import asyncio
    import os
    import random
    import asyncpg
    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    async def run() -> None:
        """Print postprocessed results and coverage stats for all Luvik products."""
        pool = await asyncpg.create_pool(
            host=os.getenv("DB_HOST", "localhost"),
            port=int(os.getenv("DB_PORT", 5432)),
            database=os.getenv("DB_NAME", "prices"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASS"),
        )
        rows = await pool.fetch(
            "SELECT sku, name, category FROM products WHERE supplier='luvik'"
        )
        await pool.close()

        total = len(rows)
        no_size = 0
        generic_brand = 0
        unknown_type: dict[str, int] = {}

        sample_no_size: list[str] = []
        sample_generic: list[str] = []

        for r in rows:
            f = extract_features(r["name"], r["category"])

            if f["size_value"] is None:
                no_size += 1
                if len(sample_no_size) < 8:
                    sample_no_size.append(r["name"])

            if f["brand"] == "Generico":
                generic_brand += 1
                if len(sample_generic) < 8:
                    sample_generic.append(r["name"])

            # Track first token of product type that fell through to fallback
            # (i.e., not in alias map) — helps identify new aliases to add
            raw_first = r["name"].split()[0].upper() if r["name"] else ""
            if raw_first.endswith(".") or "/" in raw_first:
                ft = f["product_type"]
                # Only track if it looks like an abbreviation
                if "." in raw_first or "/" in raw_first:
                    unknown_type[raw_first] = unknown_type.get(raw_first, 0) + 1

        brand_coverage = (total - generic_brand) / total * 100
        size_coverage  = (total - no_size)  / total * 100

        print(f"\n=== Luvik postprocessing coverage ({total} products) ===")
        print(f"  Brand non-generic : {total - generic_brand:5d} / {total}  ({brand_coverage:.1f}%)")
        print(f"  Size extracted    : {total - no_size:5d}  / {total}  ({size_coverage:.1f}%)")

        if unknown_type:
            print(f"\n  Abbreviated first tokens without alias ({len(unknown_type)} distinct):")
            for tok, n in sorted(unknown_type.items(), key=lambda x: -x[1]):
                print(f"    {n:4d}  {tok!r}")

        if sample_no_size:
            print(f"\n  Products without size ({no_size} total, showing up to 8):")
            for n in sample_no_size:
                print(f"    {n!r}")

        if sample_generic:
            print(f"\n  Products with brand=Generico ({generic_brand} total, showing up to 8):")
            for n in sample_generic:
                print(f"    {n!r}")

        # Print 25 random samples
        sample = random.sample(list(rows), min(25, total))
        print(f"\n{'RAW NAME':<55} {'TYPE':<25} {'BRAND':<22} {'VARIANT':<22} {'SIZE'}")
        print("-" * 150)
        for r in sample:
            f = extract_features(r["name"], r["category"])
            sz = f"{f['size_value']}{f['size_unit']}" if f["size_value"] else ""
            print(
                f"{r['name'][:54]:<55} "
                f"{(f['product_type'] or '')[:24]:<25} "
                f"{(f['brand'] or '')[:21]:<22} "
                f"{(f['variant'] or '')[:21]:<22} "
                f"{sz}"
            )

    asyncio.run(run())
