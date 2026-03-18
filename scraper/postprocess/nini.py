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
    size_display: str | None = None
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
        if variant:
            variant = re.sub(r"\bAnillitos\b", "Anillos", variant, flags=re.IGNORECASE)
        if variant == "Agridulce":
            variant = "Agridulces"
        if variant == "Agridulces Azucarados":
            variant = "Agridulces"
        if variant == "Azucaradas":
            variant = "Azucarados"
        if variant == "Clasico":
            variant = "Clasicos"
        if product_type == "Bizcochos" and variant == "Azucarados" and size == (200, "g"):
            size = (210, "g")
    if _ascii_fold(brand or "").upper() == "919":
        product_type = "Tintura"
        size = (1, "uni")
        if variant:
            variant = re.sub(
                r"^\s*Kit\s+N[°º]?\s*(\d+(?:\.\d+)?)\s*$",
                r"Kit \1",
                variant,
                flags=re.IGNORECASE,
            )
    if _ascii_fold(brand or "").upper() == "AMARGO OBRERO" and size and int(size[0]) == 1000 and size[1] == "ml":
        size = (950, "ml")
    if _ascii_fold(brand or "").upper() in {"BUENAS", "BUENAS Y SANTAS"}:
        brand = "Buenas y Santas"
        product_type = "Yerba"
        variant = "C/Hierbas"
    if _ascii_fold(brand or "").upper() == "CAZALIS":
        product_type = "Aperitivo"
    if _ascii_fold(brand or "").upper() == "CHEF":
        product_type = "Pure"
        if variant:
            variant = re.sub(r"\bInstant[aá]neo\b", "", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\s+", " ", variant).strip() or None
        if not variant and re.search(r"\bPapas\b", name, re.IGNORECASE):
            variant = "Papas"
    if _ascii_fold(brand or "").upper() == "CHOCOLIA" and product_type == "Galletita":
        product_type = "Galletitas"
    if _ascii_fold(brand or "").upper() == "CRISTALINA":
        product_type = "Grasa"
        if variant:
            variant = re.sub(r"\bMargarina\b", "", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\bBovina\b", "Vacuna", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\bRef\.\b", "Refinada", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\s+", " ", variant).strip() or None
        if size and int(size[0]) == 500 and size[1] == "ml":
            size = (500, "g")
    if _ascii_fold(brand or "").upper() == "DONGA" and variant:
        variant = re.sub(r"\bSurtido\b", "", variant, flags=re.IGNORECASE)
        variant = re.sub(r"\s+", " ", variant).strip() or None
    if _ascii_fold(brand or "").upper() == "EDRA":
        if product_type in {"Gallet.", "Galletita"}:
            product_type = "Galletitas"
        if variant:
            variant = re.sub(r"\bDulces\b", "", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\s+", " ", variant).strip() or None
    if _ascii_fold(brand or "").upper() == "EL PULPITO":
        product_type = "Pegamento"
        variant = None
        if re.search(r"\b50\s*Gramos\b", name, re.IGNORECASE):
            size = (50, "g")
    if _ascii_fold(brand or "").upper() == "ELITE ULTRA":
        brand = "Elite"
    if _ascii_fold(brand or "").upper() == "GROLSCH" and variant:
        variant = re.sub(r"\bLata\b", "", variant, flags=re.IGNORECASE)
        variant = re.sub(r"\s+", " ", variant).strip() or None
    if _ascii_fold(brand or "").upper() in {"J B", "J&B"}:
        brand = "J&B"
        if variant and re.fullmatch(r"Rare", variant, re.IGNORECASE):
            variant = None
    if _ascii_fold(brand or "").upper() == "KIMBIES":
        product_type = "Toallitas Humedas"
        variant = None
    if re.search(r"BREEDER", _ascii_fold(name).upper()):
        brand = "Breeders"
        product_type = "Vodka"
        if variant:
            variant = re.sub(r"\bPetaca\b", "", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\bVodka\b", "", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\bOrig\.?\b|\bOriginal\b", "", variant, flags=re.IGNORECASE)
            variant = re.sub(r"[.]+", " ", variant)
            variant = re.sub(r"\s+", " ", variant).strip() or None
    if _ascii_fold(brand or "").upper() == "LA YAPA":
        product_type = "Pastillas"
        variant = "Surtido"
        if size == (1, "uni"):
            size = (17, "g")
            size_display = None
    if _ascii_fold(brand or "").upper() == "QUEMAITA":
        product_type = "Caña"
        if variant:
            variant = re.sub(r"\bQuemada\b", "", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\s+", " ", variant).strip() or None
    if _ascii_fold(brand or "").upper() == "LAS PEPAS" and product_type == "Fragancia":
        product_type = "Perfume"
    if _ascii_fold(brand or "").upper() == "MELITAS":
        if size and int(size[0]) == 170 and size[1] == "g":
            size = (159, "g")
        if variant and re.fullmatch(r"159\s*/?", variant):
            variant = None
    if _ascii_fold(brand or "").upper() == "NOBLE" and _ascii_fold(product_type or "").upper() == "PAPEL HIGIENICO":
        variant = "Hoja Simple"
        size = (4, "uni")
        size_display = "4x30 m"
    if _ascii_fold(brand or "").upper() == "SKARCHITOS":
        if _ascii_fold(product_type or "").upper() == "COPOS":
            product_type = "Cereal"
        if variant and re.search(r"\bAzuc\.?\b", variant, re.IGNORECASE):
            variant = "Azucarados"
    if _ascii_fold(brand or "").upper() == "HOT WHEELS":
        if _ascii_fold(product_type or "").upper() == "AUTO":
            product_type = "Autos"
        if size and int(size[0]) == 5 and size[1] == "uni":
            size = (5, "u")
    if _ascii_fold(brand or "").upper() == "VALLE DE ORO":
        if _ascii_fold(product_type or "").upper() == "ARVEJA":
            product_type = "Arvejas"
        if variant:
            variant = re.sub(r"\bFresca\b", "", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\s+", " ", variant).strip() or None
    if _ascii_fold(brand or "").upper() in {"PIC NIC", "PICNIC"}:
        if product_type in {"Bizc.", "Bizc"}:
            product_type = "Bizcochuelo"
        if variant:
            variant = re.sub(r"\bRell\.?\s*Ddl\b", "Dulce de Leche", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\bDulce Leche\b", "Dulce de Leche", variant, flags=re.IGNORECASE)
    if _ascii_fold(brand or "").upper() == "PLOMERO":
        if _ascii_fold(product_type or "").upper() in {"DESTAPACANERIA", "DESTAPACANERIAS"}:
            product_type = "Destapacañerias"
    if "PUNT E MES" in _ascii_fold(name).upper():
        brand = "Punt e Mes"
        product_type = "Vermouth"
        variant = None
    if _ascii_fold(brand or "").upper() == "FANACOA" and variant:
        variant = re.sub(r"\bSin\s+Tacc\s+Dp\b", "", variant, flags=re.IGNORECASE)
        variant = re.sub(r"\s+", " ", variant).strip() or None
    if _ascii_fold(brand or "").upper() == "PADILLA" and size == (700, "ml"):
        size = (750, "ml")
    if _ascii_fold(brand or "").upper() == "POXILINA":
        product_type = "Adhesivo"
        variant = None
        if size == (1, "uni"):
            size = (70, "g")
            size_display = None
    if _ascii_fold(brand or "").upper() == "RAFFAELLO":
        product_type = "Bombon"
        if variant and re.fullmatch(r"Coco Y Almendra", variant, re.IGNORECASE):
            variant = None
    if _ascii_fold(brand or "").upper() == "RASTA" and variant and re.fullmatch(r"Triple Trico|Trico Maicena", variant, re.IGNORECASE):
        variant = "Trico"
    if _ascii_fold(brand or "").upper() == "SALUS" and variant and re.fullmatch(r"Mate Endulzada C/Stevia", variant, re.IGNORECASE):
        variant = "Natural"
    if _ascii_fold(brand or "").upper() == "AMSTEL":
        variant = None
    if _ascii_fold(brand or "").upper() == "ANGELITA":
        product_type = "Leche"
        if variant and re.fullmatch(r"Tetrabrick\s+Entera\s+2%", variant, re.IGNORECASE):
            variant = "Tetrabrick Entera 2%"
        elif variant and re.fullmatch(r"Tetrabrick\s+Descr\.?", variant, re.IGNORECASE):
            variant = "Tetrabrick Parcialmente Descremada"
    if _ascii_fold(brand or "").upper() == "AXEL":
        product_type = "Alimento a Base de Miel"
        variant = None
    if _ascii_fold(brand or "").upper() in {"BARON B", "BARON"}:
        brand = "Baron B"
        product_type = "Espumante"
        if variant:
            variant = re.sub(r"\bEspumante\b", "", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\s+", " ", variant).strip() or None
    if _ascii_fold(brand or "").upper() == "BRAHMA":
        variant = None
    if _ascii_fold(brand or "").upper() == "BUDWEISER":
        variant = None
    if _ascii_fold(brand or "").upper() == "BURNETTS":
        product_type = "Gin"
        variant = None
    if _ascii_fold(brand or "").upper() == "CINDOR":
        product_type = "Leche"
        variant = "Chocolatada"
    if _ascii_fold(brand or "").upper() in {"D V CATENA", "D.V.CATENA"} and variant and re.fullmatch(r"Cabern-Malbec", variant, re.IGNORECASE):
        variant = "Cabernet Malbec"
    if _ascii_fold(brand or "").upper() == "DIVERSION":
        product_type = "Galletitas"
        variant = "Surtido"
        size = (400, "g")
        size_display = None
    if _ascii_fold(brand or "").upper() == "ECCOLE":
        product_type = "Adhesivo"
        variant = None
    if _ascii_fold(brand or "").upper() == "GAROTO":
        product_type = "Bombones"
        variant = "Surtidos"
    if _ascii_fold(brand or "").upper() == "LIEBIG":
        product_type = "Yerba"
        variant = "Original"
        size = (500, "g")
        size_display = None
    if _ascii_fold(brand or "").upper() == "MATEANDO":
        product_type = "Yerba"
        variant = "Suave"
    if _ascii_fold(brand or "").upper() == "MACUCAS" and _ascii_fold(product_type or "").upper() == "RELLENAS":
        product_type = "Galletitas"
        variant = "Chocolate"
    if _ascii_fold(brand or "").upper() == "MENTOS":
        product_type = "Caramelos"
        if variant:
            variant = re.sub(r"\bConfitados\b", "", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\s+", " ", variant).strip() or None
        size = None
        size_display = None
    if _ascii_fold(brand or "").upper() == "MILLER":
        variant = None
    if _ascii_fold(brand or "").upper() == "OLD SPICE":
        product_type = "Desodorante"
        if size and size[1] == "ml" and variant and not re.search(r"\bRoll-?On\b", variant, re.IGNORECASE):
            variant = f"Roll-on {variant}"
    if (
        _ascii_fold(brand or "").upper() == "NESCAO"
        and _ascii_fold(product_type or "").upper() == "LECHE"
        and variant
        and re.fullmatch(r"En Polvo Chocolatado", variant, re.IGNORECASE)
        and size == (150, "g")
    ):
        product_type = "Cacao"
        variant = None
    if _ascii_fold(brand or "").upper() == "ORIGEN":
        product_type = "Vino"
        if variant:
            variant = re.sub(r"\bTrapich\b", "", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\bVino\b", "", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\bCabern\.?\s*Sauv\.?\b", "Cabernet Sauvignon", variant, flags=re.IGNORECASE)
            variant = re.sub(r"[.]+$", "", variant)
            variant = re.sub(r"\s+", " ", variant).strip() or None
    if _ascii_fold(brand or "").upper() == "PINOLUZ":
        product_type = "Limpiador"
        if variant and re.fullmatch(r"Botella Original", variant, re.IGNORECASE):
            variant = "Pino"
    if _ascii_fold(brand or "").upper() == "SEISEME":
        product_type = "Jabon"
        variant = "Pan"
    if _ascii_fold(brand or "").upper() == "SVELTY":
        product_type = "Leche en Polvo"
        variant = "Descremada"
    if _ascii_fold(brand or "").upper() == "DUFFY":
        product_type = "Pañales"
        if variant:
            variant = re.sub(r"^\s*Pañales\s+", "", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\bHiper\s+G\b", "Xg", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\s+", " ", variant).strip() or None
        if size and size[1] == "uni":
            size = (size[0], "u")
            size_display = None
    if _ascii_fold(brand or "").upper() == "GOLONDRINA" and variant:
        variant = re.sub(r"\bC/Soja\b", "", variant, flags=re.IGNORECASE)
        variant = re.sub(r"\s+", " ", variant).strip() or None
    if _ascii_fold(brand or "").upper() in {"K OTHRINA", "K-OTHRINA"}:
        variant = None
    if _ascii_fold(brand or "").upper() == "BORGHETTI":
        variant = None
    if _ascii_fold(brand or "").upper() == "CARO CUORE":
        if _ascii_fold(product_type or "").upper() == "FRAGANCIA":
            product_type = "Perfume"
        if variant:
            variant = re.sub(r"\bAer\.?\s*Fem\b", "", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\s+", " ", variant).strip() or None
    if _ascii_fold(brand or "").upper() == "CRAZY POP":
        product_type = "Chupetin"
        if variant:
            variant = re.sub(r"\bTira\b", "", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\bChispeante\b", "", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\s+", " ", variant).strip() or None
        size = None
        size_display = None
    if _ascii_fold(brand or "").upper() == "DERMAGLOS":
        product_type = "Protector Solar"
        if variant:
            variant = re.sub(r"\bProt\.?\s*Solar\b", "", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\bEmulsi[oó]n\b", "Emulsion", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\bFp\s*(\d+)\b", r"Fp\1", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\s+", " ", variant).strip() or None
    if _ascii_fold(brand or "").upper() == "MINORA":
        product_type = "Maquina de Afeitar"
        if re.search(r"\bPRO\b", _ascii_fold(name).upper()):
            variant = "Pro Ii"
        if size and size[1] == "uni":
            size = (size[0], "u")
            size_display = None
    if _ascii_fold(brand or "").upper() == "OLD SMUGGLER":
        if _ascii_fold(product_type or "").upper() == "PETACA":
            product_type = "Whisky"
        if variant:
            variant = re.sub(r"\bPetaca\b", "", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\bWhisky\b", "", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\bA[nñ]ejo\b", "", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\s+", " ", variant).strip() or None
    if _ascii_fold(brand or "").upper() == "POXIPOL":
        product_type = "Adhesivo"
        if variant:
            variant = re.sub(r"\b10\s+Minutos\b", "", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\bGris\b", "Original", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\s+", " ", variant).strip() or None
        size = None
        size_display = None
    if _ascii_fold(brand or "").upper() == "PROFUGO" and variant:
        if re.search(r"\bEspecias\b", variant, re.IGNORECASE):
            variant = "Especias"
        elif re.search(r"\bFr(?:\.?\s*Rojos|utos\s+Rojos)\b", variant, re.IGNORECASE):
            variant = "Frutos Rojos"
    if _ascii_fold(brand or "").upper() == "QUILMES" and variant:
        variant = re.sub(r"\bLata\b", "", variant, flags=re.IGNORECASE)
        variant = re.sub(r"\bS/Alcohol\b", "", variant, flags=re.IGNORECASE)
        variant = re.sub(r"\s+", " ", variant).strip() or None
    if _ascii_fold(brand or "").upper() == "RICOMAS" and _ascii_fold(variant or "").upper() == "MASTICABLES":
        variant = "Masticables Surtidos"
    if _ascii_fold(brand or "").upper() == "SAENZ BRIONES":
        product_type = "Sidra"
        variant = "1888"
    if _ascii_fold(brand or "").upper() == "TOSTITOS":
        if _ascii_fold(product_type or "").upper() in {"SNACK", "NACHO", "NACHOS"}:
            product_type = "Nachos"
        variant = None
    if _ascii_fold(brand or "").upper() == "TRES PATITOS":
        if _ascii_fold(product_type or "").upper() == "FOSFORO":
            product_type = "Fosforos"
        if variant:
            variant = re.sub(r"\bEdicion\b", "", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\s+", " ", variant).strip() or None
        if size and size[1] == "uni":
            size = (size[0], "u")
            size_display = None
    if _ascii_fold(brand or "").upper() == "VITTONE":
        if variant and re.fullmatch(r"Speciale", variant, re.IGNORECASE):
            variant = None
        if size == (750000, "ml"):
            size = (750, "ml")
            size_display = None
    if _ascii_fold(brand or "").upper() == "VOLIGOMA":
        if _ascii_fold(product_type or "").upper() == "ADHESIV":
            product_type = "Adhesivo"
        if variant:
            variant = re.sub(r"\bSintetico\b", "", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\s+", " ", variant).strip() or None
    if _ascii_fold(brand or "").upper() == "BALLANTINES":
        if _ascii_fold(product_type or "").upper() in {"S", ""}:
            product_type = "Whisky"
        if _ascii_fold(variant or "").upper() == "WHISKY":
            variant = None
    if _ascii_fold(brand or "").upper() == "CABSHA" and _ascii_fold(product_type or "").upper() == "HUEVO":
        product_type = "Huevo de Pascua"
        variant = None
    if _ascii_fold(brand or "").upper() == "COCOA BEACH":
        if variant and re.match(r"Patrol\b", variant, re.IGNORECASE):
            variant = f"Paw {variant}"
        product_type = "Protector Solar"
        if variant:
            variant = re.sub(r"\bProt\.?\s*Solar\b", "", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\bPaw\s+Patrol\b", "Paw Patrol", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\bFp\s*(\d+)\b", r"Fp\1", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\s+", " ", variant).strip() or None
    if _ascii_fold(brand or "").upper() == "COFFEE MATE":
        if not variant:
            variant = "Original"
        else:
            variant = re.sub(r"\bEn\s+P(?:vo|olvo)\.?\b", "", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\bLite\b|\bLight\b|\bLiviano\b", "Liviano", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\bRegular\b", "Original", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\s+", " ", variant).strip() or None
    if _ascii_fold(brand or "").upper() == "COSMOS":
        original_product_type = _ascii_fold(product_type or "").upper()
        if _ascii_fold(product_type or "").upper() in {"MEGA", "CHUPETINES"}:
            product_type = "Chupetin"
        if variant:
            variant = re.sub(r"\bChupetin(?:es)?\b", "", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\bSurtido\b", "", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\bFrutal\b", "Frutales", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\s+", " ", variant).strip() or None
        if original_product_type == "MEGA" and not variant:
            variant = "Mega"
        if size and size[1] == "uni":
            size = (size[0], "u")
            size_display = None
    if _ascii_fold(brand or "").upper() == "ECO DE LOS ANDES":
        product_type = "Agua Mineral"
        if variant:
            variant = re.sub(r"\bS/Gas\b", "", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\s+", " ", variant).strip() or None
    if _ascii_fold(brand or "").upper() == "FULL MANI" and _ascii_fold(product_type or "").upper() == "HUEVO":
        product_type = "Huevo de Pascua"
        if variant:
            variant = re.sub(r"^\s*De\s+Pascua\s+", "", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\s+", " ", variant).strip() or None
    if _ascii_fold(brand or "").upper() == "LA GOTITA":
        variant = "Instantaneo"
    if _ascii_fold(brand or "").upper() == "LA LECHERA":
        product_type = "Leche en Polvo"
        if variant:
            variant = re.sub(r"\bEn\s+Pvo\.?\b", "", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\bEnt\.?\b", "Entera", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\bNutrifuerza\s+con\s+Hierro\b", "Nutrifuerza", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\bPouch\b|\bBsa\.?x?\s*N/?Balan\.?\b", "Bolsa", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\s+", " ", variant).strip() or None
            if variant:
                variant = variant.strip(" .") or None
    if _ascii_fold(brand or "").upper() == "MERENGADAS" and variant:
        variant = re.sub(r"\bRellenas\b", "", variant, flags=re.IGNORECASE)
        variant = re.sub(r"\bFrutilla\b", "", variant, flags=re.IGNORECASE)
        variant = re.sub(r"\s+", " ", variant).strip() or None
    if _ascii_fold(brand or "").upper() == "NONISEC":
        product_type = "Pañales"
        if variant:
            variant = re.sub(r"\bExtra\s+Grande\b", "Xg", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\bGrande\b", "G", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\bAdulto\b", "Adultos", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\s+", " ", variant).strip() or None
        if size and size[1] == "uni":
            size = (size[0], "u")
            size_display = None
    if _ascii_fold(brand or "").upper() == "NUTELLA":
        product_type = "Crema"
        variant = "Avellanas"
    if _ascii_fold(brand or "").upper() in {"OVEJA", "OVEJA BLACK"}:
        brand = "Oveja Black"
        if variant:
            variant = re.sub(r"^\s*(?:Black|Blak)\s+", "", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\bCabern\.?\s*Sauv\.?\b", "Cabernet Sauvignon", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\s+", " ", variant).strip(" .") or None
    if _ascii_fold(brand or "").upper() == "PASO DE LOS TOROS" and variant:
        variant = re.sub(r"\bAgua\s+Tonica\b", "Tonica", variant, flags=re.IGNORECASE)
        variant = re.sub(r"\s+", " ", variant).strip() or None
    if _ascii_fold(brand or "").upper() == "SPEED":
        if variant:
            variant = re.sub(r"\bLata\b", "", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\s+", " ", variant).strip() or None
    if _ascii_fold(brand or "").upper() == "STELLA ARTOIS" and variant:
        variant = re.sub(r"\b0\.0%\s*S/Alcohol\b", "S/Alcohol", variant, flags=re.IGNORECASE)
        variant = re.sub(r"\bLata\b", "", variant, flags=re.IGNORECASE)
        variant = re.sub(r"\s+", " ", variant).strip() or None
    if _ascii_fold(brand or "").upper() == "PALITOS DE LA SELVA":
        product_type = "Caramelos"
        if not variant or re.fullmatch(r"Masticables?", variant, re.IGNORECASE):
            variant = "Clasicos"
    if _ascii_fold(brand or "").upper() == "CLUB SOCIAL":
        if _ascii_fold(product_type or "").upper() in {"GALLET.", "GALLETITA", "GALLETITAS"}:
            product_type = "Galletitas"
        if variant and re.fullmatch(r"Agrupado", variant, re.IGNORECASE):
            variant = "Original"
        if size == (24, "g"):
            size = (141, "g")
            size_display = None
    if _ascii_fold(brand or "").upper() == "BAYGON":
        product_type = "Insecticida"
        if variant and re.fullmatch(r"M\.?M\.?M\.?", variant, re.IGNORECASE):
            variant = "Mata Moscas y Mosquitos"
    if _ascii_fold(brand or "").upper() == "BLOCK":
        if _ascii_fold(product_type or "").upper() in {"ALFAJOR", "GALLETITA", "GALLETITAS"}:
            brand = "Cofler Block"
        if _ascii_fold(product_type or "").upper() == "GALLETITA":
            product_type = "Galletitas"
        if variant and re.fullmatch(r"Triple", variant, re.IGNORECASE):
            variant = None
    if _ascii_fold(brand or "").upper() == "COFLER":
        if _ascii_fold(product_type or "").upper() == "HUEVO" and variant and re.search(r"\bBlock\b", variant, re.IGNORECASE):
            brand = "Cofler Block"
            product_type = "Huevo de Pascua"
            variant = re.sub(r"^\s*De\s+Pascua\s+Block\b", "", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\s+", " ", variant).strip(" .") or None
    if _ascii_fold(brand or "").upper() == "ALARIS":
        brand = "Trapiche Alaris"
        if variant:
            variant = re.sub(r"^\s*Alaris\s+", "", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\s+", " ", variant).strip(" .") or None
            variant = re.sub(r"\bCabern\.?\s*Sauv\.?\b", "Cabernet Sauvignon", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\s+", " ", variant).strip(" .") or None
    if _ascii_fold(brand or "").upper() == "ALBERIO" and _ascii_fold(product_type or "").upper() == "LENTEJA":
        product_type = "Lentejas"
    if _ascii_fold(brand or "").upper() == "PUROCOL":
        if variant and re.search(r"\bEtil", variant, re.IGNORECASE):
            variant = re.sub(r"\bUso\s+Alim\.?\b", "U/A", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\s+", " ", variant).strip() or None
        if _ascii_fold(product_type or "").upper().startswith("ALCOHOL ETIL"):
            product_type = "Alcohol"
            variant = "Etilico U/A"
    if _ascii_fold(brand or "").upper() == "RESERVA DE LOS ANDES" and variant:
        variant = re.sub(r"\bCabern\.?\s*Sauv\.?\b", "Cabernet Sauvignon", variant, flags=re.IGNORECASE)
        variant = re.sub(r"\s+", " ", variant).strip(" .") or None
    if _ascii_fold(brand or "").upper() == "SCHNEIDER":
        variant = None
    if _ascii_fold(brand or "").upper() == "SANTA FILOMENA":
        if variant and re.fullmatch(r"Botell[oó]n Patero Tinto|Tinto Patero", variant, re.IGNORECASE):
            variant = "Patero Tinto"
    if _ascii_fold(brand or "").upper() in {"CUCATRAP", "CUCA-TRAP"}:
        product_type = "Insecticida"
        variant = None
    if _ascii_fold(brand or "").upper() in {"JIM BEAM", "JIM BEAN"}:
        if variant and re.fullmatch(r"White|White Label|Etiqueta Blanca", variant, re.IGNORECASE):
            variant = "White Label"
    if _ascii_fold(brand or "").upper() == "WARSTEINER":
        variant = None
    if _ascii_fold(brand or "").upper() == "WHITE HORSE":
        variant = None
    if _ascii_fold(brand or "").upper() == "XTREME":
        if _ascii_fold(product_type or "").upper() == "RAINBOW":
            product_type = "Caramelos"
        if variant:
            variant = re.sub(r"^\s*(?:Caram\.?\s+)?De\s+Goma\s+", "", variant, flags=re.IGNORECASE)
            variant = re.sub(r"\s+", " ", variant).strip() or None
    if _ascii_fold(brand or "").upper() == "PIBES":
        product_type = "Colonia"
        variant = None
        size = (80, "ml")
        size_display = None
    if _ascii_fold(brand or "").upper() in {"DRF", "D R F"} or re.search(r"\bD\s*R\s*F\b", name, re.IGNORECASE):
        brand = "DRF"
        if _ascii_fold(product_type or "").upper() == "CARAMELOS DUROS":
            product_type = "Pastillas"
        if variant:
            variant = re.sub(r"#", "", variant)
            variant = re.sub(r"\s+", " ", variant).strip() or None
        size = None
        size_display = None
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
        "size_display": size_display,
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
