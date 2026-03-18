"""
Postprocessing for Maxiconsumo product data.

Run as a standalone pass after scraping:
    python -m scraper.postprocess.maxiconsumo

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
        line.strip().upper()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]


def _ascii_fold(text: str) -> str:
    """Remove accents for accent-insensitive matching (ñ→N, á→A, etc.)."""
    return unicodedata.normalize("NFD", text).encode("ascii", "ignore").decode("ascii")


# Sorted longest-first so greedy matching works correctly
_KNOWN_PRODUCT_TYPES: list[str] = sorted(_load_lines("maxiconsumo_product_types.txt"), key=lambda x: -len(x))
# For product types: also store folded versions for matching
_KNOWN_PRODUCT_TYPES_FOLDED: list[tuple[str, str]] = [
    (_ascii_fold(pt), pt) for pt in _KNOWN_PRODUCT_TYPES
]

def _load_aliases(filename: str) -> dict[str, str]:
    """Load VARIANT=CANONICAL alias pairs. Keys are ascii-folded for matching."""
    path = _DATA_DIR / filename
    if not path.exists():
        return {}
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        variant, _, canonical = line.partition("=")
        result[_ascii_fold(variant.strip().upper())] = canonical.strip()
    return result


# Maps folded variant → UPPERCASE canonical product type
_PRODUCT_TYPE_ALIAS_MAP: dict[str, str] = _load_aliases("maxiconsumo_product_type_aliases.txt")

_KNOWN_BRANDS_RAW: list[str] = _load_lines("maxiconsumo_brands.txt")
# Map folded → canonical for brand lookup
_BRAND_FOLD_MAP: dict[str, str] = {_ascii_fold(b): b for b in _KNOWN_BRANDS_RAW}
# Sorted folded list (longest first) for greedy prefix matching
_KNOWN_BRANDS_FOLDED_SORTED: list[str] = sorted(_BRAND_FOLD_MAP.keys(), key=lambda x: -len(x))

# ---------------------------------------------------------------------------
# Name cleaning
# ---------------------------------------------------------------------------

# Spanish articles/prepositions kept lowercase inside a title-cased string
_LOWER_WORDS = {"y", "e", "o", "u", "de", "del", "la", "las", "el", "los",
                "en", "con", "sin", "para", "por", "al", "a"}


def clean_name(raw: str) -> str:
    """Strip whitespace, fix encoding artifacts, and convert to title case."""
    text = unicodedata.normalize("NFKC", raw)
    text = re.sub(r"\s+", " ", text).strip()
    words = text.split()
    result = []
    for i, word in enumerate(words):
        lower = word.lower()
        if i == 0 or lower not in _LOWER_WORDS:
            result.append(word.capitalize())
        else:
            result.append(lower)
    return " ".join(result)


# ---------------------------------------------------------------------------
# Measurement patterns
# ---------------------------------------------------------------------------

def _parse_number(s: str) -> float:
    """Parse a number that may use comma as decimal separator."""
    return float(s.replace(",", "."))


# Multi-pack: 2X95, 12X12, 6X1 — must be tried BEFORE weight/volume
# Group 1 = pack count, Group 2 = per-unit quantity, Group 3 = unit
_MULTIPACK_RE = re.compile(
    r"\b(\d+)\s*[xX]\s*(\d+(?:[.,]\d+)?)\s*(kg|kilo|kilos|gr|grs|gramos|g|lts?|litros?|ml|cc|cm3)\b",
    re.IGNORECASE,
)

# Single weight: 500GR, 1KG, 250 GR, 1.5KG, 500G, 500 GRAMOS
_WEIGHT_RE = re.compile(
    r"\b(\d+(?:[.,]\d+)?)\s*(kg|kilo|kilos|gr|grs|gramos|g)\b",
    re.IGNORECASE,
)

# Single volume: 750CC, 1L, 2L, 500 ML, 2LT, 2LTS, 2LTR, 1.5 LT, 500 LITROS
_VOLUME_RE = re.compile(
    r"\b(\d+(?:[.,]\d+)?)\s*(lt[rs]?|litros?|ml|cc|cm3|l)\b",
    re.IGNORECASE,
)

# Plain units: X24, X 12, 24UN, 24 UN, 24 UNIDADES, 44 UND
_UNITS_RE = re.compile(
    r"\b(?:x\s*(\d+)|(\d+)\s*un(?:d|idades?)?)\b",
    re.IGNORECASE,
)

# Packet count: 100 SOBRES, 50 SOBRES, 1000 SO (truncated)
_SOBRES_RE = re.compile(r"\b(\d+)\s*(?:sobres?|so)\b", re.IGNORECASE)

# Dimensions: 60X100 CM, 80x110 CM
_DIMENSIONS_RE = re.compile(r"\b(\d+)\s*[xX]\s*(\d+)\s*(cm)\b", re.IGNORECASE)

# Bundle/promo packs: 3X2, 2X1, 14X12 UN, 2X (trailing, no second number)
_BUNDLE_RE = re.compile(r"\b(\d+)\s*[xX](?:\s*(\d+))?\s*(un(?:d|idades?)?)?\b", re.IGNORECASE)

# Length: 5 MT, 50 MTS, 1,5 MT, 10 METROS
_LENGTH_RE = re.compile(r"\b(\d+(?:[.,]\d+)?)\s*(mts?|metros?)\b", re.IGNORECASE)

# Single linear dimension: 22 CM, 18CM (pot diameter, shelf size, etc.)
_SINGLE_CM_RE = re.compile(r"\b(\d+(?:[.,]\d+)?)\s*(cm)\b", re.IGNORECASE)

# Packaging container words — stripped after measurements are extracted
_CONTAINER_RE = re.compile(
    r"\b(botella|pote|bolsa|caja|lata|frasco|sachet|saquito|doy\s*pack|pet|pvc|petaca|sobre|barra|tira)\b",
    re.IGNORECASE,
)

# Canonical unit normalization
_WEIGHT_UNITS = {
    "kg": "kg", "kilo": "kg", "kilos": "kg",
    "gr": "g", "grs": "g", "gramos": "g", "g": "g",
}
_VOLUME_UNITS = {
    "lt": "l", "lts": "l", "ltr": "l", "litro": "l", "litros": "l", "l": "l",
    "ml": "ml", "cc": "ml", "cm3": "ml",
}

def _to_grams(value: float, unit: str) -> float:
    """Convert any weight value to grams."""
    return value * 1000 if _WEIGHT_UNITS[unit] == "kg" else value

def _to_ml(value: float, unit: str) -> float:
    """Convert any volume value to millilitres."""
    return value * 1000 if _VOLUME_UNITS[unit] == "l" else value


# ---------------------------------------------------------------------------
# Product-type dictionary (longest match wins)
# ---------------------------------------------------------------------------
# Each entry is an uppercase string. Sorted longest-first for greedy matching.
# Coverage: ~80%. Known gap: product types not listed here fall back to
# first-word extraction.

# Tokens that signal the next word is part of the brand (2-word brand pickup)
# e.g. "LA GIOCONDA", "DON NICANOR", "SAN JORGE", "SANTA ISABEL"
_BRAND_ARTICLES = {"LA", "EL", "LOS", "LAS", "LO", "LE", "DON", "SAN", "SANTA"}

# Prepositions that should never be treated as a brand on their own
_SKIP_AS_BRAND = {"DE", "DEL", "EN", "CON", "AL", "A", "Y"}

# Hard-coded brand corrections: when the extracted brand token matches a key
# (accent-insensitive), replace it with the canonical brand.
# Used for cases where the supplier encodes the brand as a descriptor/abbreviation.
_BRAND_CORRECTIONS: dict[str, str] = {
    "ANILLOS": "Terrabusi",       # Galletitas Anillos = Terrabusi
    "CHAMPAGNE": "Terrabusi",     # Galletitas Champagne = Terrabusi
    "GRILL": "Atma",              # Freidora de Aire y Grill Digital = Atma
    "AQUA": "Generico",           # Plato Aqua Esmerilado (generic glass)
    "JC": "La Quesera",           # Queso JC = La Quesera (JC is a label code)
    "NORUEGA": "Rigolleau",       # Copa Noruega = Rigolleau (glass style name)
    "FILM": "Generico",           # Combo film + aluminio = generic packaging set
    # Near-duplicate merges (similarity >= 90%)
    "DON ANTONINO": "Don Antonio",
    "100DUCADOS": "100 Ducados",
    "CHOCOLINA": "Chocolinas",
    "HELLMANNS": "Hellmann's",
    "SIMONAGIO": "Simonaggio",
    "DR LEMON": "Dr. Lemon",
    "DR.LEMON": "Dr. Lemon",
    "FREEGELLS": "Freegels",
    "GILLETE": "Gillette",
    "GOMES DE COSTA": "Gomes Da Costa",
    "ALTEZA": "Altezza",
    "LA SERENISA": "La Serenisima",
    "FEDERICO ALVEAR": "Federico de Alvear",
    "LA TRANQUE": "La Tranquera",
    "PRESTOBAR": "Prestobarba",
    "CAMPAGNOLA": "La Campagnola",
    "CABANA DON TOMAS": "Cabaña Tomas",
    "GRAMBY": "Granby",
    "BARON": "Baron B",
}


def _extract_product_type_and_brand(tokens: list[str]) -> tuple[str | None, str | None, list[str]]:
    """
    Given a list of uppercase tokens (measurements/containers already removed),
    return (product_type, brand, remaining_tokens).

    Priority:
      1. Lookup in _KNOWN_PRODUCT_TYPES (file-driven, longest match)
      2. Fallback: first token
    Then brand:
      1. Lookup in _KNOWN_BRANDS (file-driven, longest match)
      2. Fallback: article heuristic or single token
    """
    upper_text = " ".join(tokens)

    # --- product type ---
    product_type: str | None = None
    pt_word_count = 0

    folded_text = _ascii_fold(upper_text)
    for folded_pt, canonical_pt in _KNOWN_PRODUCT_TYPES_FOLDED:
        if folded_text.startswith(folded_pt):
            product_type = clean_name(canonical_pt)
            pt_word_count = len(canonical_pt.split())
            break

    if product_type is None and tokens:
        product_type = tokens[0].capitalize()
        pt_word_count = 1

    # Apply product-type alias: normalise typos/variants to canonical form
    if product_type is not None:
        folded_pt = _ascii_fold(product_type.upper())
        if folded_pt in _PRODUCT_TYPE_ALIAS_MAP:
            product_type = clean_name(_PRODUCT_TYPE_ALIAS_MAP[folded_pt])

    remaining = tokens[pt_word_count:]
    remaining_text = " ".join(remaining)

    # --- brand: try lookup first (longest match, accent-insensitive) ---
    brand: str | None = None
    brand_word_count = 0
    folded_remaining = _ascii_fold(remaining_text)

    for folded_b in _KNOWN_BRANDS_FOLDED_SORTED:
        if folded_remaining.startswith(folded_b):
            brand = clean_name(_BRAND_FOLD_MAP[folded_b])  # use canonical (accented) form
            brand_word_count = len(folded_b.split())
            break

    if brand is not None:
        remaining = remaining[brand_word_count:]
    else:
        # Fallback heuristic
        if remaining:
            if remaining[0] in _SKIP_AS_BRAND and len(remaining) > 1:
                remaining = remaining[1:]  # skip leading preposition
            if remaining:
                first = remaining[0]
                if first in _BRAND_ARTICLES and len(remaining) >= 2:
                    brand = clean_name(f"{remaining[0]} {remaining[1]}")
                    remaining = remaining[2:]
                else:
                    brand = first.capitalize()
                    remaining = remaining[1:]
                    # Absorb a trailing short alpha token (e.g. "ORAL" + "B")
                    if remaining and len(remaining[0]) <= 2 and remaining[0].isalpha():
                        brand = f"{brand} {remaining[0].upper()}"
                        remaining = remaining[1:]

    # Apply hard-coded brand corrections (accent-insensitive key lookup)
    if brand is not None:
        folded_brand = _ascii_fold(brand.upper())
        if folded_brand in _BRAND_CORRECTIONS:
            brand = _BRAND_CORRECTIONS[folded_brand]

    # Final fallback: products with no detectable brand are labeled Generico
    if brand is None:
        brand = "Generico"

    # Brand-aware product-type corrections
    # Giacomo labels its capelettini as "Capelletis"/"Capellettis" — fix the type.
    # For all other brands, "Capellettis" is a typo for "Capelletis".
    if product_type is not None:
        folded_pt_final = _ascii_fold(product_type.upper())
        if _ascii_fold((brand or "").upper()) == "GIACOMO" and folded_pt_final in ("CAPELLETIS", "CAPELLETTIS"):
            product_type = "Capelettini"
        elif folded_pt_final == "CAPELLETTIS":
            product_type = "Capelletis"

    return product_type, brand, remaining


# ---------------------------------------------------------------------------
# Main feature extraction
# ---------------------------------------------------------------------------

def extract_features(name: str) -> dict:
    """
    Extract structured features from a raw Maxiconsumo product name.

    Returns a dict with keys:
        product_type   str | None
        brand          str | None
        variant        str | None   — everything after brand, minus measurements
        weight         {"value": float, "unit": str} | None  — canonical unit: g
        volume         {"value": float, "unit": str} | None  — canonical unit: ml
        units_in_name  int | None
        clean_name     str          — product_type + brand + variant, title-cased
    """
    text = name.upper()
    weight = None
    volume = None
    units_in_name = None
    units_label = "un"
    dimensions = None
    length = None

    # 0. Keyword substitutions before numeric extraction
    #    AGRUPADOS = display box of 6 individual units (no count in name)
    text = re.sub(r"\bAGRUPADOS\b", "6 UN", text)
    #    EXTENS N,N (no unit) = extension measured in meters
    text = re.sub(r"\bEXTENS\s+(\d+[,.]\d+)\b", r"EXTENS \1 MT", text)
    #    Truncated CC → CC: "750 C" at end of string or before whitespace
    text = re.sub(r"\b(\d+)\s+C\b", r"\1 CC", text)
    #    Letter directly followed by a number+unit (missing space): SUIPACHENSE180 GR
    text = re.sub(
        r"([A-Z])(\d+(?:[.,]\d+)?\s*(?:kg|kilo|kilos|gr|grs|gramos|g|lt[rs]?|litros?|ml|cc|cm3|mts?|metros?|cm|l))\b",
        r"\1 \2", text, flags=re.IGNORECASE,
    )

    # 1. Multi-pack (must run before weight/volume to avoid partial matches)
    m = _MULTIPACK_RE.search(text)
    if m:
        units_in_name = int(m.group(1))
        per_unit_val = _parse_number(m.group(2))
        unit_raw = m.group(3).lower()
        if unit_raw in _WEIGHT_UNITS:
            weight = {"value": _to_grams(per_unit_val, unit_raw), "unit": "g"}
        else:
            volume = {"value": _to_ml(per_unit_val, unit_raw), "unit": "ml"}
        text = text[:m.start()] + text[m.end():]

    # 2. Single weight
    if weight is None:
        m = _WEIGHT_RE.search(text)
        if m:
            val = _parse_number(m.group(1))
            unit_raw = m.group(2).lower()
            weight = {"value": _to_grams(val, unit_raw), "unit": "g"}
            text = text[:m.start()] + text[m.end():]

    # 3. Single volume
    if volume is None:
        m = _VOLUME_RE.search(text)
        if m:
            val = _parse_number(m.group(1))
            unit_raw = m.group(2).lower()
            volume = {"value": _to_ml(val, unit_raw), "unit": "ml"}
            text = text[:m.start()] + text[m.end():]

    # 4. Dimensions (60X100 CM) — must run before bundle/units to avoid consuming NxN cm
    if dimensions is None and weight is None and volume is None:
        m = _DIMENSIONS_RE.search(text)
        if m:
            dimensions = f"{m.group(1)}x{m.group(2)} {m.group(3).lower()}"
            text = text[:m.start()] + text[m.end():]

    # 4b. Bundle/promo packs: 3X2, 2X1, 14X12 UN, 2X — runs before _UNITS_RE so
    #     "14X12 UN" is consumed whole rather than _UNITS_RE grabbing just "12 UN"
    if dimensions is None and units_in_name is None:
        m = _BUNDLE_RE.search(text)
        if m:
            n1, n2, unit_part = m.group(1), m.group(2), m.group(3)
            bundle = f"{n1}x{n2}" if n2 else f"{n1}x"
            if unit_part:
                bundle += " un"
            dimensions = bundle
            text = text[:m.start()] + text[m.end():]

    # 4c. Plain units (X24, 24UN)
    if units_in_name is None:
        m = _UNITS_RE.search(text)
        if m:
            units_in_name = int(m.group(1) or m.group(2))
            text = text[:m.start()] + text[m.end():]

    # 4d. Packet count (100 SOBRES)
    if units_in_name is None:
        m = _SOBRES_RE.search(text)
        if m:
            units_in_name = int(m.group(1))
            units_label = "sobres"
            text = text[:m.start()] + text[m.end():]

    # 4e. Length: 5 MT, 50 MTS, 1,5 MT
    if length is None and dimensions is None:
        m = _LENGTH_RE.search(text)
        if m:
            val = m.group(1).replace(",", ".")
            n = float(val)
            length = f"{int(n) if n == int(n) else val.replace('.', ',')} mt"
            text = text[:m.start()] + text[m.end():]

    # 4e. Single linear dimension: 22 CM, 18CM
    if length is None and dimensions is None and weight is None and volume is None:
        m = _SINGLE_CM_RE.search(text)
        if m:
            val = m.group(1).replace(",", ".")
            n = float(val)
            length = f"{int(n) if n == int(n) else val} cm"
            text = text[:m.start()] + text[m.end():]

    # 5. Tokenize first, then strip container tokens — but keep the first token
    #    intact so product types that ARE containers (e.g. "BOLSA", "LATA") survive.
    tokens_raw = text.split()
    if tokens_raw:
        first_token = tokens_raw[0]
        rest = _CONTAINER_RE.sub("", " ".join(tokens_raw[1:])).split()
        tokens = [first_token] + rest
    else:
        tokens = []

    # 7. Extract product type, brand, variant
    product_type, brand, remaining = _extract_product_type_and_brand(tokens)
    variant = clean_name(" ".join(remaining)) if remaining else None
    if variant == "":
        variant = None

    # Product-specific overrides (known cases where the name omits key info)
    if brand == "Billiken" and product_type == "Turron" and variant is None:
        variant = "De Mani"
    if _ascii_fold(brand or "").upper() == "8 HERMANOS" and product_type == "Anis":
        product_type = "Licor"
        variant = "Anis Azul"
    if _ascii_fold(brand or "").upper() == "9 DE ORO":
        if product_type == "Pepas" or (variant and re.search(r"\bPepas\b", variant, re.IGNORECASE)):
            product_type = "Pepas"
            variant = "Membrillo"
        if variant:
            variant = re.sub(r"\bAnillitos\b", "Anillos", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\s*120\s*/\s*$", "", variant, flags=re.IGNORECASE).strip() or None
        if product_type == "Galletitas" and variant in {"Queso", "Clasicas"} and weight == {"value": 120, "unit": "g"}:
            product_type = "Crujitas"
        if variant == "Agridulce":
            variant = "Agridulces"
        if variant == "Agridulces Azucarados":
            variant = "Agridulces"
        if variant == "Azucaradas":
            variant = "Azucarados"
        if variant == "Clasico":
            variant = "Clasicos"
        if re.search(r"\b120\s*/\s*180\s*GR\b", name, re.IGNORECASE):
            weight = {"value": 180, "unit": "g"}
    if _ascii_fold(brand or "").upper() == "919":
        product_type = "Tintura"
        units_in_name = 1
        units_label = "uni"
        if variant:
            variant = re.sub(
                r"^\s*N[°º]?\s*(\d+(?:\.\d+)?)\s*$",
                r"Kit \1",
                variant,
                flags=re.IGNORECASE,
            )
    if _ascii_fold(brand or "").upper() == "ASTRA":
        product_type = "Maquina Afeitar"
        variant = None
    if _ascii_fold(brand or "").upper() in {"BUENAS", "BUENAS Y SANTAS"}:
        brand = "Buenas y Santas"
        product_type = "Yerba"
        variant = "C/Hierbas"
    if _ascii_fold(brand or "").upper() == "CAZALIS":
        product_type = "Aperitivo"
        variant = None
    if brand == "1882" and product_type == "Aperitivo" and _ascii_fold((variant or "").upper()) == "CON COLA":
        product_type = "Fernet"
    if (
        brand == "1882"
        and product_type == "Fernet"
        and variant is None
        and volume is not None
        and int(round(volume["value"])) == 1008
    ):
        volume["value"] = 1000
    if _ascii_fold(brand or "").upper() == "JELLY ROLL" and weight is not None and int(round(weight["value"])) == 1100:
        weight["value"] = 1000
    if _ascii_fold(brand or "").upper() == "NAMUR" and weight is None:
        weight = {"value": 25, "unit": "g"}
    if _ascii_fold(brand or "").upper() == "BREEDERS":
        product_type = "Vodka"
    if _ascii_fold(brand or "").upper() == "KIMBIES":
        product_type = "Toallitas Humedas"
        if variant and re.fullmatch(r"Wipes", variant, re.IGNORECASE):
            variant = None
        if units_in_name is not None and units_label == "un":
            units_label = "uni"
    if _ascii_fold(brand or "").upper() == "LA YAPA":
        product_type = "Pastillas"
        variant = "Surtido"
    if _ascii_fold(brand or "").upper() == "MARCELA" and _ascii_fold(product_type or "").upper() == "APERITIVO AMERICANO":
        product_type = "Americano"
    if _ascii_fold(brand or "").upper() == "POXILINA":
        product_type = "Adhesivo"
        variant = None
    if _ascii_fold(brand or "").upper() == "BORGHETTI":
        variant = None
    if _ascii_fold(brand or "").upper() == "CASCABEL":
        product_type = "Atun"
        if variant:
            if re.search(r"\bAceite\b", variant, re.IGNORECASE):
                variant = "Desmenuzado Aceite"
            elif re.search(r"\bNatural\b", variant, re.IGNORECASE):
                variant = "Desmenuzado Natural"
            else:
                variant = "Desmenuzado"
    if _ascii_fold(brand or "").upper() == "CRANDALL" and _ascii_fold(product_type or "").upper() == "DESODORANTE MASCULINO":
        product_type = "Desodorante"
        if variant:
            variant = f"Masculino {variant}"
    if _ascii_fold(brand or "").upper() in {"K OTHRINA", "K-OTHRINA"}:
        variant = None
    if _ascii_fold(brand or "").upper() == "MINORA":
        product_type = "Maquina de Afeitar"
    if _ascii_fold(brand or "").upper() == "POXIPOL":
        product_type = "Adhesivo"
        if variant:
            variant = re.sub(r"\b10\s+Minutos\b", "", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\bGris\b", "Original", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\s+", " ", variant).strip() or None
        weight = None
        volume = None
        units_in_name = None
        units_label = None
    if _ascii_fold(brand or "").upper() == "SAENZ BRIONES":
        product_type = "Sidra"
        variant = "1888"
    if _ascii_fold(brand or "").upper() == "TOSTITOS":
        product_type = "Nachos"
        variant = None
    if _ascii_fold(brand or "").upper() == "NUTELLA":
        product_type = "Crema"
        variant = "Avellanas"
    if _ascii_fold(brand or "").upper() == "CLUB SOCIAL":
        if _ascii_fold(product_type or "").upper() in {"GALLET.", "GALLETITA", "GALLETITAS"}:
            product_type = "Galletitas"
        if variant and re.fullmatch(r"Agrupado", variant, re.IGNORECASE):
            variant = "Original"
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
        units_label = None
    if _ascii_fold(brand or "").upper() == "ARMONIA UAT":
        brand = "Armonia"
    if _ascii_fold(brand or "").upper() == "BAYGON":
        product_type = "Insecticida"
    if _ascii_fold(brand or "").upper() == "BLOCK":
        if _ascii_fold(product_type or "").upper() in {"ALFAJOR", "GALLETITA", "GALLETITAS"}:
            brand = "Cofler Block"
        if _ascii_fold(product_type or "").upper() == "GALLETITA":
            product_type = "Galletitas"
        if variant and re.fullmatch(r"I", variant, re.IGNORECASE):
            variant = "Individual"
        if variant and re.fullmatch(r"Triple", variant, re.IGNORECASE):
            variant = None
    if re.search(r"\bALARIS\b", _ascii_fold(name).upper()):
        brand = "Trapiche Alaris"
        if variant:
            variant = re.sub(r"^\s*Alaris\s+", "", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\s+", " ", variant).strip(" .") or None
    if _ascii_fold(brand or "").upper() == "ACQUA DI COLBERT" and _ascii_fold(product_type or "").upper() == "DESODORANTE MASCULINO":
        product_type = "Desodorante"
    if _ascii_fold(brand or "").upper() in {"OVEJA", "OVEJA BLACK"}:
        brand = "Oveja Black"
        if variant:
            variant = re.sub(r"^\s*(?:Black|Blak)\s+", "", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\bCabern\.?\s*Sauv\.?\b", "Cabernet Sauvignon", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\s+", " ", variant).strip(" .") or None
    if _ascii_fold(brand or "").upper() == "PORTENITAS" and variant:
        variant = re.sub(r"\bDulce\b", "", variant, flags=re.IGNORECASE)
        variant = re.sub(r"\s+", " ", variant).strip() or None
    if _ascii_fold(brand or "").upper() == "VOLIGOMA":
        if _ascii_fold(product_type or "").upper() == "ADHESIV":
            product_type = "Adhesivo"
        if variant:
            variant = re.sub(r"\bSintetico\b", "", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\s+", " ", variant).strip() or None
    if _ascii_fold(brand or "").upper() == "BLANCANUEZ":
        if _ascii_fold(product_type or "").upper() == "NUEZ":
            product_type = "Nueces"
        if variant:
            variant = re.sub(r"\b34\.?36\b", "", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\bPelada\b", "Peladas", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\s+", " ", variant).strip() or None
    if _ascii_fold(brand or "").upper() in {"COFFEE MATE", "COFFE MATE"}:
        brand = "Coffee Mate"
        if not variant:
            variant = "Original"
        else:
            variant = re.sub(r"\bLight\b|\bLite\b", "Liviano", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\bRegular\b", "Original", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\bEn\s+Polvo\b", "", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\s+", " ", variant).strip() or None
    if _ascii_fold(brand or "").upper() == "COSMOS":
        if _ascii_fold(product_type or "").upper() == "CHUPETINES":
            product_type = "Chupetin"
        if variant:
            variant = re.sub(r"\bSurtido\b", "", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\bFrutal\b", "Frutales", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\bMega\b", "Mega", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\s+", " ", variant).strip() or None
        if units_in_name is not None:
            units_label = "u"
    if _ascii_fold(brand or "").upper() == "ETIQUET":
        if _ascii_fold(product_type or "").upper() == "ANTITRANSPIRANTE MASCULINO":
            product_type = "Antitranspirante"
        if variant and re.fullmatch(r"Roll On Hombre|Rolit Original Men Rollo-?On", variant, re.IGNORECASE):
            variant = "Roll On Men Original"
        if volume is not None:
            weight = {"value": volume["value"], "unit": "g"}
            volume = None
    if _ascii_fold(brand or "").upper() == "FULBITO" and variant:
        variant = re.sub(r"\bR/", "", variant, flags=re.IGNORECASE)
        variant = re.sub(r"\bMarroc\b", "Mani", variant, flags=re.IGNORECASE)
        variant = re.sub(r"\s+", " ", variant).strip() or None
    if _ascii_fold(brand or "").upper() == "QUEMAITA":
        product_type = "Caña"
        if variant:
            variant = re.sub(r"\bQuemada\b", "", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\s+", " ", variant).strip() or None
    if _ascii_fold(brand or "").upper() == "BUHERO NEGRO" and _ascii_fold(variant or "").upper() == "NEGRO":
        variant = None
    if _ascii_fold(brand or "").upper() in {"SPIRITO", "SPIRITO BLU", "SPIRITU BLU"}:
        brand = "Spirito Blu"
        variant = None
    if _ascii_fold(brand or "").upper() == "DANCING" and _ascii_fold(product_type or "").upper() == "BOCADITO":
        product_type = "Bombon"
    if _ascii_fold(brand or "").upper() == "DESAFIO":
        if units_in_name == 50:
            units_label = "u"
    if _ascii_fold(brand or "").upper() in {"ETCHART", "ETCHART PRIVADO"} and variant:
        variant = re.sub(r"^\s*Privado\s+", "", variant, flags=re.IGNORECASE)
        variant = re.sub(r"\s+", " ", variant).strip() or None
    if _ascii_fold(brand or "").upper() == "FRESITA" and _ascii_fold(product_type or "").upper() == "CHAMPAGNE":
        product_type = "Espumante"
    if _ascii_fold(brand or "").upper() == "MELITTA" and _ascii_fold(product_type or "").upper() == "GALLETITAS":
        brand = "Melitas"
    if _ascii_fold(brand or "").upper() == "PUSH POP" and _ascii_fold(product_type or "").upper() in {"CHUPETINES", "CHUP."}:
        product_type = "Chupetin"
    if _ascii_fold(brand or "").upper() in {"RAFFAELLO", "RAFAELLO"}:
        product_type = "Bombon"
        if units_in_name is not None:
            units_label = "u"
    if _ascii_fold(brand or "").upper() in {"PIC NIC", "PICNIC"}:
        if product_type in {"Bizc.", "Bizc"}:
            product_type = "Bizcochuelo"
        if variant:
            variant = re.sub(r"\bRell\.?\s*Ddl\b", "Dulce de Leche", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\bDulce Leche\b", "Dulce de Leche", variant, flags=re.IGNORECASE)
    if _ascii_fold(brand or "").upper() in {"PLOMERO", "PLOMERO LIQUIDO"}:
        if _ascii_fold(product_type or "").upper() in {"DESTAPACANERIA", "DESTAPACANERIAS"}:
            product_type = "Destapacañerias"
    if _ascii_fold(brand or "").upper() in {"POXI RAN", "POXI-RAN"}:
        product_type = "Adhesivo"
        if variant and re.fullmatch(r"Sin Tolueno", variant, re.IGNORECASE):
            variant = "Contacto"
    if _ascii_fold(brand or "").upper() == "MARROC" and units_in_name == 60:
        units_label = "u"
    if _ascii_fold(brand or "").upper() == "TRIX" and variant:
        variant = re.sub(r"\bFuente de Hierro\b", "", variant, flags=re.IGNORECASE)
        variant = re.sub(r"\s+", " ", variant).strip() or None
    if _ascii_fold(brand or "").upper() == "BARON B":
        product_type = "Espumante"
        if variant:
            variant = re.sub(r'"b"', "", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\s+", " ", variant).strip() or None
    if _ascii_fold(brand or "").upper() == "BOMBAY" and variant and re.fullmatch(r"Shapphire", variant, re.IGNORECASE):
        variant = "Sapphire"
    if _ascii_fold(brand or "").upper() == "BOOS" and _ascii_fold(product_type or "").upper() == "DESOD.":
        product_type = "Desodorante"
    if _ascii_fold(brand or "").upper() == "ECCOLE":
        product_type = "Adhesivo"
        variant = None
    if _ascii_fold(brand or "").upper() == "GAROTO":
        product_type = "Bombones"
        variant = "Surtidos"
        if weight is not None:
            weight["value"] = 250
    if _ascii_fold(brand or "").upper() == "LIEBIG":
        product_type = "Yerba"
        variant = "Original"
        weight = {"value": 500, "unit": "g"}
    if _ascii_fold(brand or "").upper() == "MATEANDO":
        product_type = "Yerba"
        variant = "Suave"
    if "PUNT E MES" in _ascii_fold(name).upper():
        brand = "Punt e Mes"
        product_type = "Vermouth"
        variant = None

    # 8. Build clean_name from structured parts
    parts = [p for p in [product_type, brand, variant] if p]
    clean = " ".join(parts)

    return {
        "product_type": product_type,
        "brand":        brand,
        "variant":      variant,
        "weight":       weight,
        "volume":       volume,
        "units_in_name": units_in_name,
        "units_label":  units_label,
        "dimensions":   dimensions,
        "length":       length,
        "clean_name":   clean,
    }


# ---------------------------------------------------------------------------
# Category parsing
# ---------------------------------------------------------------------------

# Known encoding artifacts in category strings
_CATEGORY_FIXES = {
    "Pa Ales": "Pañales",
}


def parse_category(raw: str) -> dict:
    """
    Split a Maxiconsumo category path into section, subsection, and leaf.

    Input:  "Almacen > Dulces Y Mermeladas > Mermeladas Y Jaleas En Frasco"
    Output: {"section": "Almacen", "subsection": "Dulces Y Mermeladas",
             "leaf": "Mermeladas Y Jaleas En Frasco"}

    Missing levels are returned as None.
    """
    result = raw
    for bad, good in _CATEGORY_FIXES.items():
        result = result.replace(bad, good)

    parts = [p.strip() for p in result.split(">")]
    return {
        "section":    parts[0] if len(parts) > 0 else None,
        "subsection": parts[1] if len(parts) > 1 else None,
        "leaf":       parts[2] if len(parts) > 2 else None,
    }


# Keep the old normalize_category for backward compatibility
def normalize_category(raw: str) -> str:
    """Return the full category path as a normalized string."""
    cat = parse_category(raw)
    parts = [p for p in [cat["section"], cat["subsection"], cat["leaf"]] if p]
    return " > ".join(parts)


# ---------------------------------------------------------------------------
# CLI — dry-run preview
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import asyncio
    import os
    import asyncpg
    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    async def run() -> None:
        """Print postprocessed results for 20 random Maxiconsumo products."""
        pool = await asyncpg.create_pool(
            host=os.getenv("DB_HOST", "localhost"),
            port=int(os.getenv("DB_PORT", 5432)),
            database=os.getenv("DB_NAME", "prices"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASS"),
        )
        rows = await pool.fetch(
            "SELECT sku, name, category FROM products "
            "WHERE supplier = 'maxiconsumo' ORDER BY RANDOM() LIMIT 20"
        )
        await pool.close()

        print(f"\n{'RAW NAME':<50} {'TYPE':<22} {'BRAND':<20} {'VARIANT':<25} {'WEIGHT':<12} {'VOLUME':<12} {'UN':<4} {'SECTION':<15} {'LEAF'}")
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
