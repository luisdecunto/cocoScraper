"""
Postprocessing for Santa Maria product data.

Run as a standalone pass after scraping:
    python -m scraper.postprocess.santamaria

Functions are also importable for use in tests or other modules.

Name format (typical): <AbbrevType> <BRAND> <variant> <qty> <unit>
  - Type may be abbreviated with dots/slashes: "D/Amb.", "Gallet.", "T.Fem."
  - Brand is in UPPERCASE immediately after the type (sometimes no space)
  - Unit is a single letter: G=grams, K=kg, M=ml, C=cc, L=litros, U=units, S=sachets
  - UxB count (units per bulk box) is stored separately in stock="uxb:N"
"""

import logging
import re
import unicodedata

from scraper.postprocess._utils import (
    _ascii_fold,
    _load_aliases,
    clean_name,
    _DATA_DIR,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Category mapping: cPath code → human-readable name
# ---------------------------------------------------------------------------

def _load_categories() -> dict[str, str]:
    """Load santamaria_categories.txt as {cpath: name} dict."""
    path = _DATA_DIR / "santamaria_categories.txt"
    if not path.exists():
        logger.warning("santamaria_categories.txt not found — category names unavailable")
        return {}
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        cpath, _, name = line.partition("=")
        result[cpath.strip()] = name.strip()
    return result


_CATEGORIES: dict[str, str] = _load_categories()


def normalize_category(raw: str) -> str:
    """
    Map a raw Santa Maria cPath code to a human-readable category path.

    '1_117' → 'Comestibles / Galletitas'
    '2_205' → 'Bebidas / Gaseosas'
    Unknown codes are returned as-is.
    """
    raw = raw.strip()
    if "_" in raw:
        # Leaf category: prepend top-level parent name
        parent = raw.split("_")[0]
        leaf_raw = _CATEGORIES.get(raw, raw)
        parent_raw = _CATEGORIES.get(parent, "")
        if parent_raw:
            return clean_name(parent_raw) + " / " + clean_name(leaf_raw)
        return clean_name(leaf_raw)
    # Top-level category
    return clean_name(_CATEGORIES.get(raw, raw))


def parse_uxb(stock: str) -> int | None:
    """
    Parse units-per-bulk-box count from stock field.

    'uxb:24' → 24
    'unknown' → None
    """
    if stock and stock.startswith("uxb:"):
        try:
            return int(stock[4:])
        except ValueError:
            pass
    return None


# ---------------------------------------------------------------------------
# Unit patterns
# ---------------------------------------------------------------------------
# Santa Maria uses single-letter unit abbreviations at the end of product names:
#   G = grams      K = kilograms (→ grams × 1000)
#   M = millilitres  C = cc (= ml)  L = litros (→ ml × 1000)
#   U = unidades   S = sobres/sachets
#   W = watts (electrical — excluded from weight/volume/units)
#   E = each/pack  (ambiguous with bag dimensions like 15x20 e — excluded)

# Trailing quantity + unit: must be at end of string, optionally preceded by *
# Handles: "500 G", "750 c", "200 m", "3 l", "1 K", "100 s", "8 U"
_QTY_UNIT_RE = re.compile(
    r"(?<!\d[xX])\b(\d+(?:[.,]\d+)?)\s*([gGkKmMcClLuUsS])\s*[*]?\s*$"
)

# Canonical unit normalization (uppercase key)
_WEIGHT_UNIT = {"G": "g", "K": "kg"}
_VOLUME_UNIT = {"M": "ml", "C": "ml", "L": "l"}
_COUNT_UNIT  = {"U": "units", "S": "sachets"}


def _to_grams(val: float, unit: str) -> float:
    """Convert weight value to grams."""
    return val * 1000 if unit == "K" else val


def _to_ml(val: float, unit: str) -> float:
    """Convert volume value to millilitres."""
    return val * 1000 if unit == "L" else val


# ---------------------------------------------------------------------------
# Brand detection helpers
# ---------------------------------------------------------------------------
# Santa Maria brand is in UPPERCASE immediately after the product type.
# Abbreviations end with a dot: "D/Amb.POETT" → type "D/Amb." + brand "POETT".
# Pre-processing inserts a space at that boundary.

def _insert_space_at_type_brand_boundary(text: str) -> str:
    """Insert spaces at type-abbreviation/brand boundaries to enable tokenization.

    'D/Amb.POETT'     → 'D/Amb. POETT'     (dot before ALL-CAPS brand)
    'NESC.Black'      → 'NESC. Black'       (ALL-CAPS abbrev before Title-Case word)
    'TERRAB.Anillos'  → 'TERRAB. Anillos'   (ALL-CAPS abbrev before Title-Case word)
    'Gallet.P&S'      → 'Gallet. P&S'       (lowercase-ending abbrev before brand)
    'Limp.Mr.MUSC.'   → 'Limp. Mr. MUSC.'  (lowercase letter before uppercase)
    '96*PUROCOL'      → '96* PUROCOL'       (asterisk/digit before ALL-CAPS brand)
    """
    # Case 1: any letter + dot + 2+ uppercase: ".POETT" → ". POETT"
    text = re.sub(r"\.([A-Z]{2,})", r". \1", text)
    # Case 2: 2+ uppercase + dot + Title-case letter: "NESC.Black" → "NESC. Black"
    # Use [A-Z][a-z] (not [A-Za-z]) so "LE.Q" is NOT split — single-uppercase suffix stays glued
    text = re.sub(r"([A-Z]{2,})\.([A-Z][a-z])", r"\1. \2", text)
    # Case 3: lowercase + dot + uppercase: "Gallet.P" → "Gallet. P"
    text = re.sub(r"([a-z])\.([A-Z])", r"\1. \2", text)
    # Case 3b: lowercase + dot + digit: "Gallet.9" → "Gallet. 9"
    # Separates product-type abbreviations glued to a numeric brand prefix (e.g. "9 DE ORO")
    text = re.sub(r"([a-z])\.(\d)", r"\1. \2", text)
    # Case 4: asterisk + 2+ uppercase: "*PUROCOL" → "* PUROCOL"
    text = re.sub(r"\*([A-Z]{2,})", r"* \1", text)
    # Case 5: closing parenthesis glued to uppercase: "(LC)BULL" → "(LC) BULL"
    text = re.sub(r"\)([A-Z]{2,})", r") \1", text)
    # Case 6: uppercase word glued to single-letter parenthetical: "DANVERS(F)" → "DANVERS (F)"
    # These are gender/size markers and must become separate tokens so the stop rule applies.
    text = re.sub(r"([A-Z]{2,})\(([A-Za-z])\)", r"\1 (\2)", text)
    # Case 7: single uppercase initial + dot + Title-case word: "F.S.Roscado" → "F.S. Roscado"
    # Handles brands written as dot-separated initials glued to a title-case description.
    text = re.sub(r"([A-Z])\.([A-Z][a-z])", r"\1. \2", text)
    return text


# Known non-brand suffix abbreviations: product format/variant codes that appear as
# ALL-CAPS tokens immediately after the brand name and must not be absorbed into it.
_BRAND_SUFFIX_ABBREVS: frozenset[str] = frozenset({
    "AA",    # battery size (Philco)
    "AAA",   # battery size (Philco)
    "AOB",   # aerosol oil-based — deodorant format (Rexona)
    "BE",    # barra enjuagada — bar soap format (Armel, Querubin, Heroe, Granby)
    "DDL",   # dulce de leche — flavour suffix (Cusenier)
    "II",    # Roman numeral product line indicator (Minora II)
    "MMM",   # insecticide variant code (Selton, Fuyi)
    "PLAX",  # Colgate mouthwash product line
    "SG",    # S&G — pasta quality designation (Lucchetti, Favorita, Matarazzo)
    "PRONTO", # product line sub-brand (Baggio Pronto)
    "TNT",   # candy product line (Freegells)
    "ULTRA", # product line sub-brand (Babysec Ultra)
})

# Post-extraction brand aliases: correct typos and on-site spelling variants.
# Keys are lowercase; values are the canonical display form.
_BRAND_ALIASES: dict[str, str] = {
    "luccheti":  "Lucchetti",   # typo in Santa Maria database
    "rindedos":  "Rinde 2",     # on-site spelling of the "Rinde 2" juice brand
    "salzano":   "F. Salzano",  # F. initial stripped in step 3, restored here
}

# Post-extraction product_type aliases: normalise Santa Maria abbreviations to canonical form.
# Keys are lowercase (after clean_name title-casing).
# Values are either:
#   str  → replace product_type directly
#   tuple(str, str) → (new_product_type, variant_prefix) — prefix is prepended to variant
_PRODUCT_TYPE_ALIASES: dict[str, str | tuple] = {
    "past. goma":   "Gomitas",                        # "Pastilla de Goma" = gummy candy
    "past.":        "Gomitas",                        # bare "Past." in SM catalog = soft gummy candy
    "caram. mast.": ("Caramelos", "Masticables"),     # type=Caramelos, variant prefix=Masticables
}

# Variant text normalizations: list of (compiled regex, replacement) pairs.
# Applied to variant after clean_name title-casing.
_VARIANT_NORMALIZATIONS: list[tuple] = [
    # "Tutti Fr." abbreviation and alternate spellings → canonical "Tutti Frutti"
    (re.compile(r"\bTutti?\s+Fr\.",                re.IGNORECASE), "Tutti Frutti"),
    (re.compile(r"\bTuttt+i\s+Frutti\b",           re.IGNORECASE), "Tutti Frutti"),
    (re.compile(r"\bTuti\s+Fruti\b",               re.IGNORECASE), "Tutti Frutti"),
    (re.compile(r"\bTutti\s+Frutti\b",             re.IGNORECASE), "Tutti Frutti"),  # normalize casing
    # Flavor/variant abbreviation expansions
    (re.compile(r"\bFrutal\b",                     re.IGNORECASE), "Frutales"),
    (re.compile(r"\bD/[Mm]ani\b",                  re.IGNORECASE), "De Mani"),
    (re.compile(r"\bYog\.",                        re.IGNORECASE), "Yogur"),
    (re.compile(r"\bSurt\.",                       re.IGNORECASE), "Surtidos"),
]


def _is_brand_token(tok: str) -> bool:
    """Return True if token looks like part of an uppercase brand name."""
    # Strip punctuation that legitimately appears inside brand names
    core = re.sub(r"[.\-/!*&'\"()]", "", tok)
    if not core:
        return False
    # All-uppercase letters (and digits inside brand codes like "ORAL-B", "7UP")
    return core == core.upper() and any(c.isalpha() for c in core) and len(core) >= 1


# ---------------------------------------------------------------------------
# Main feature extraction
# ---------------------------------------------------------------------------

def extract_features(name: str) -> dict:
    """
    Extract structured features from a raw Santa Maria product name.

    Returns a dict with keys:
        product_type   str | None  — everything before the brand
        brand          str | None  — UPPERCASE brand name, title-cased
        variant        str | None  — everything after brand, minus measurements
        weight_g       float | None — weight in grams
        volume_ml      float | None — volume in millilitres
        units_in_name  int | None   — pack/unit count from name
        clean_name     str          — product_type + brand + variant, title-cased

    Note: units_per_bulk_box is in stock="uxb:N" — use parse_uxb() to read it.
    Coverage: ~99%. Known gaps (~3 products):
      - Watts (W) and per-dimension bag specs (e) are not extracted.
      - M = meters for paper/foil products (e.g. "Papel Aluminio 5 M") is mis-read as ml.
        Affects ~5–10 products in "Papel", "Rollo" categories.
      - "F.Salzano" (hardware brand, 1 product) not detected — title-case brand after
        single-letter prefix.
      - "Mr.MUSC." splits into product_type="Limp. Mr.", brand="Musc." (acceptable).
      - Suffix abbreviations like "DDL" (Dulce de Leche) may be absorbed into brand name.
    """
    # 1. Normalize encoding and collapse whitespace
    text = unicodedata.normalize("NFKC", name)
    text = re.sub(r"\s+", " ", text).strip()

    weight_g: float | None = None
    volume_ml: float | None = None
    units_in_name: int | None = None
    pack_count: int | None = None
    bag_dimensions: str | None = None
    bag_count: int | None = None
    length: str | None = None
    units_label: str | None = None

    # 2. Extract trailing quantity + unit.
    # Run specialised patterns first (before _QTY_UNIT_RE) because they contain embedded
    # characters (x, saq.) that prevent the standard word-boundary lookbehind from matching.

    # Step 2a: "NuxM unit" — multipack weight/volume (e.g. "12ux35 G", "6ux750 M")
    _mp_m = re.search(
        r"\b(\d+)\s*[uU][xX](\d+(?:[.,]\d+)?)\s*([gGkKmMcClL])\s*[*]?\s*$", text
    )
    # Step 2a2: "NxM U" — display-pack unit count (e.g. "12x14 U", "14x12 U")
    # Detected here because _QTY_UNIT_RE's lookbehind (?<!\d[xX]) would block "14 U" in "12x14 U".
    _up_m = re.search(r"\b(\d+)[xX](\d+)\s*[uU]\s*[*]?\s*$", text) if not _mp_m else None
    # Step 2a3: "NxM G/K" — display-pack weight (e.g. "5x101 G")
    # Same lookbehind issue as NxM U — the weight unit after 'xM' is blocked by _QTY_UNIT_RE.
    _wpack_m = (
        re.search(r"\b(\d+)[xX](\d+(?:[.,]\d+)?)\s*([gGkK])\s*[*]?\s*$", text)
        if not _mp_m and not _up_m else None
    )
    # Step 2a4: "Nsaq." — sachets/saquitos (e.g. "20saq.", "20 saq.")
    _sq_m = (
        re.search(r"\b(\d+)\s*saq\.\s*$", text, re.IGNORECASE)
        if not _mp_m and not _up_m and not _wpack_m else None
    )

    if _mp_m:
        pack_count = int(_mp_m.group(1))
        val = float(_mp_m.group(2).replace(",", "."))
        unit = _mp_m.group(3).upper()
        # Special case: "M" in paper/roll products means meters, not millilitres.
        # e.g. "Papel Hig. CAUTIVA 4ux30 M" → 4 rolls × 30 metres.
        _is_roll = bool(re.search(r"\b(?:Papel|Rollo|Toalla|Serv)\b", text, re.IGNORECASE))
        if unit == "M" and _is_roll:
            units_in_name = pack_count
            pack_count = None
            n = int(val) if val == int(val) else round(val, 3)
            length = f"{n} mt"
        elif unit in _WEIGHT_UNIT:
            weight_g = _to_grams(val, unit)
        else:
            volume_ml = _to_ml(val, unit)
        text = text[: _mp_m.start()].strip().rstrip("*").strip()
    elif _up_m:
        pack_count = int(_up_m.group(1))
        units_in_name = int(_up_m.group(2))
        text = text[: _up_m.start()].strip().rstrip("*").strip()
    elif _wpack_m:
        pack_count = int(_wpack_m.group(1))
        val = float(_wpack_m.group(2).replace(",", "."))
        unit = _wpack_m.group(3).upper()
        weight_g = _to_grams(val, unit)
        text = text[: _wpack_m.start()].strip().rstrip("*").strip()
    elif _sq_m:
        units_in_name = int(_sq_m.group(1))
        units_label = "saquitos"
        text = text[: _sq_m.start()].strip().rstrip("*").strip()
    else:
        # Step 2b: standard trailing qty+unit
        m = _QTY_UNIT_RE.search(text)
        if m:
            raw_val = m.group(1).replace(",", ".")
            val = float(raw_val)
            unit = m.group(2).upper()

            if unit in _WEIGHT_UNIT:
                weight_g = _to_grams(val, unit)
            elif unit in _VOLUME_UNIT:
                volume_ml = _to_ml(val, unit)
            elif unit in _COUNT_UNIT:
                units_in_name = int(val)
                if unit == "S":
                    units_label = "saquitos"

            # Remove quantity+unit from text
            text = text[: m.start()].strip().rstrip("*").strip()

    # 2c. Extract bag dimensions — plastic bags specify their size as NxM with an 'e' suffix
    # (each/pack marker) or with 'cm' embedded, e.g. "15x20 e", "10u 60x90 e", "100x130cm".
    # Pattern A: trailing "NxM e" (most common)
    _dim_m = re.search(r"\b(\d+)[xX](\d+)\s*[eE]\s*$", text)
    if _dim_m:
        bag_dimensions = f"{_dim_m.group(1)}x{_dim_m.group(2)} cm"
        text = text[: _dim_m.start()].strip()
        # Look for bag count "Nu" anywhere remaining (may be directly before dims or embedded)
        # e.g. "10u 60x90 e" → "10u" at end; "Perf.20u Rollo Bca. 34x38 e" → "20u" embedded
        _cnt_m = re.search(r"\b(\d+)\s*[uU]\b", text)
        if _cnt_m:
            bag_count = int(_cnt_m.group(1))
            text = (text[: _cnt_m.start()] + text[_cnt_m.end() :])
            text = re.sub(r"\s+", " ", text).strip()
    else:
        # Pattern B: embedded "NxMcm" — 'cm' already present, no 'e' suffix
        _dim_m2 = re.search(r"\b(\d+)[xX](\d+)\s*cm\b", text, re.IGNORECASE)
        if _dim_m2:
            bag_dimensions = f"{_dim_m2.group(1)}x{_dim_m2.group(2)} cm"
            # Strip the matched span from text for cleaner brand/variant extraction
            text = (text[: _dim_m2.start()] + text[_dim_m2.end() :])
            text = re.sub(r"\s+", " ", text).strip()
            # Look for bag count "Nu" anywhere remaining
            _cnt_m2 = re.search(r"\b(\d+)\s*[uU]\b", text)
            if _cnt_m2:
                bag_count = int(_cnt_m2.group(1))
                text = (text[: _cnt_m2.start()] + text[_cnt_m2.end() :])
                text = re.sub(r"\s+", " ", text).strip()
        else:
            # Pattern C: trailing "N e" or "N.M e" — single length in meters
            # e.g. "Cabo D/Madera BROMY Barnizado 1.20 e" → length="1.20 mt"
            _len_m = re.search(r"\b(\d+(?:[.,]\d+)?)\s*[eE]\s*$", text)
            if _len_m:
                length = f"{_len_m.group(1).replace(',', '.')} mt"
                text = text[: _len_m.start()].strip()

    # 2d. Fallback extractions when no size was found yet.
    _no_size = not (weight_g or volume_ml or units_in_name or pack_count or bag_dimensions or length)
    if _no_size:
        # Embedded "Paq.Nu" — pack count buried in name, e.g. "Pañuelos ELITE Paq.10u Compacto"
        _paq = re.search(r"\bPaq\.?\s*(\d+)\s*[uU]\b", text, re.IGNORECASE)
        if _paq:
            units_in_name = int(_paq.group(1))
            text = text[: _paq.start()] + text[_paq.end() :]
            text = re.sub(r"\s+", " ", text).strip()
        # Bare number at end of Galletitas — assume grams (unit omitted on site)
        # e.g. "Gallet.FRUTIGRAN Avena&Pasas 250" → weight_g=250
        elif re.match(r"Gallet\b", text, re.IGNORECASE):
            _bare = re.search(r"\b(\d+(?:[.,]\d+)?)\s*$", text)
            if _bare:
                weight_g = float(_bare.group(1).replace(",", "."))
                text = text[: _bare.start()].strip()
        else:
            # Wattage at end — lamp and electrical products.
            # "12=100 W" → "12 W" (LED actual watts, =N is the incandescent equivalent)
            # "9/10 W" → "9/10 W"; "7 W" → "7 W"
            _watt_m = re.search(r"\b(\d+(?:/\d+)?)\s*(?:=\d+\s*)?W\s*[*]?\s*$", text)
            if _watt_m:
                length = f"{_watt_m.group(1)} W"
                text = text[: _watt_m.start()].strip().rstrip("*").strip()

    # 2e. Paños extraction — kitchen/toilet rolls specify sheet count as "Np" embedded in name.
    # e.g. "Rollo Coc.CARTABELLA Daily 40p 3 U" → units_in_name=3 (from step 2b), length="40 paños"
    # Only applies when units_in_name is already set (the trailing N U was extracted first).
    if units_in_name and not length:
        _panos = re.search(r"\b(\d+)p\.?\b", text)
        # Embedded "N Grs." — per-unit weight for small-portion products
        # e.g. "Sal Fina CELUSAL Sobre 0.5Grs. 1000 U" → units_in_name=1000, length="0.5 gr"
        _grs = re.search(r"\b(\d+(?:[.,]\d+)?)\s*[Gg]rs?\.\s*", text)
        if _panos:
            length = f"{int(_panos.group(1))} paños"
            text = text[: _panos.start()] + text[_panos.end() :]
            text = re.sub(r"\s+", " ", text).strip()
        elif _grs:
            val = float(_grs.group(1).replace(",", "."))
            n = int(val) if val == int(val) else round(val, 3)
            length = f"{n} gr"
            text = text[: _grs.start()] + text[_grs.end() :]
            text = re.sub(r"\s+", " ", text).strip()

    # 3. Normalize known brand abbreviations and mixed-case brands to ALL-CAPS
    text = re.sub(r"\bCBse\b", "CBSE", text)
    text = re.sub(r"\bCBSe\b", "CBSE", text)
    # NESC. is the abbreviation used on-site for Nescafé — expand and add space before
    # the following token so "NESC.Black" → "NESCAFE Black", "NESC.DOLCA" → "NESCAFE DOLCA"
    text = re.sub(r"\bNESC\.", "NESCAFE ", text)
    # TERRAB. is the abbreviation for Terrabusi
    text = re.sub(r"\bTERRAB\.", "TERRABUSI ", text)
    # EXQ. is the abbreviation for Exquisita
    text = re.sub(r"\bEXQ\.", "EXQUISITA ", text)
    # Mr.MUSC. is the on-site spelling for Mr. Músculo — expand to two all-caps tokens
    text = re.sub(r"\bMr\.MUSC\.", "MR. MUSCULO ", text)
    # Delicias de la Nona: two on-site spellings → canonical ALL-CAPS form for brand detection
    text = re.sub(r"\bDELICIAS\s+(?:de\s+la\s+NONA|DL\.\s*NONA)\b",
                  "DELICIAS DE LA NONA", text)
    # N.GAUCHA is the abbreviation for Nobleza Gaucha
    text = re.sub(r"\bN\.GAUCHA\b", "NOBLEZA GAUCHA", text)
    # D/LA HUERTA — slash-separated abbreviation for "De la Huerta"
    text = re.sub(r"\bD/LA\s+HUERTA\b", "DE LA HUERTA", text)
    # F.SALZANO / F.Salzano: strip the "F." initial entirely — brand is resolved via
    # _BRAND_ALIASES ("salzano" → "F. Salzano"). This avoids lookback ambiguity and
    # title-cases the preceding ALL-CAPS model descriptor (ECONOMICA, BAMBINA…) correctly.
    text = re.sub(r"\bF\.(?:SALZANO|Salzano)\b", "SALZANO", text)
    # Any remaining ALL-CAPS word directly before SALZANO is a model name — title-case it
    text = re.sub(r"\b([A-Z]{2,})\s+SALZANO\b",
                  lambda m: m.group(1).capitalize() + " SALZANO", text)
    # Strip leading digit from dotted abbreviation brands: "3LE.Q" → "LE.Q"
    # (the digit is a product count prefix, not part of the brand name)
    text = re.sub(r"\b\d+([A-Z]{2,}\.[A-Za-z])", r"\1", text)

    # 4. Insert space between type abbreviation and brand (e.g. "D/Amb.POETT")
    text = _insert_space_at_type_brand_boundary(text)
    text = re.sub(r"\s+", " ", text).strip()

    # 5. Find the first all-uppercase token — that is the brand start
    tokens = text.split()
    brand_start = -1
    for i, tok in enumerate(tokens):
        core = re.sub(r"[.\-/!*&'\"()]", "", tok)
        # Skip slash-separated single-letter descriptor codes like T/R, D/P, C/A
        if re.match(r"^[A-Z](/[A-Z])+\.?$", tok):
            continue
        # Skip parenthesized type/size markers like (LC), (M), (F)
        if re.match(r"^\([A-Za-z]+\)$", tok):
            continue
        # Must be 2+ chars all-uppercase or a numeric-only token (numeric brands: 361, 1882)
        is_allcaps = (core and core == core.upper() and len(core) >= 2
                      and any(c.isalpha() for c in core))
        # Numeric brands: pure integers ≥3 digits; exclude decimal measurements like "1.20"
        is_numeric_brand = (core and core.isdigit() and len(core) >= 3
                            and not re.search(r"^\d+\.\d+$", tok))
        if is_allcaps or is_numeric_brand:
            brand_start = i
            break

    if brand_start >= 0:
        # Lookback: if the token immediately before brand_start is a digit-only token or a
        # single uppercase letter, absorb it into the brand.
        # e.g. "9 DE ORO" → digit prefix; "D ALOMO" → single-letter article/prefix
        if brand_start > 0:
            prev = tokens[brand_start - 1]
            prev_core = re.sub(r"[.\-/!*&'\"()]", "", prev)
            # Digit prefix: skip tokens ending with "*" — those are ABV% indicators (e.g. "96*")
            is_digit_prefix = (re.fullmatch(r"\d+", prev_core) and not prev.endswith("*"))
            # Single-letter tokens ending with "." are type abbreviations (e.g. "D." in
            # "Caram.D.FELFORT"), not brand prefixes — never absorb them via lookback.
            is_single_letter = (len(prev_core) == 1 and prev_core.isupper()
                                 and not prev.endswith("."))
            if is_digit_prefix or is_single_letter:
                brand_start -= 1
        product_type_raw = " ".join(tokens[:brand_start]).strip()

        # 6. Extend brand to include all consecutive uppercase tokens
        brand_end = brand_start + 1
        while brand_end < len(tokens):
            tok = tokens[brand_end]
            core = re.sub(r"[.\-/!*&'\"()]", "", tok)
            # Stop if this token has any lowercase letters (variant starts)
            if core and any(c.islower() for c in core):
                break
            # Stop if this token has no alphabetic characters.
            # Exception: years in range 1800–2099 may be brand years (e.g. "1882").
            # Excludes product codes like "(1421)", ABV percentages, flour grades.
            _is_brand_year = (core.isdigit() and re.fullmatch(r"(1[89]\d\d|20\d\d)", core))
            if not any(c.isalpha() for c in core) and not _is_brand_year:
                break
            # Stop at compound dot+slash tokens — these are product format codes like
            # "P.P/I" (Para Piso/Inodoro) or "V.R/DDL" (Variante/DDL), not brand tokens
            if "." in tok and "/" in tok:
                break
            # Stop at slash-separated single-letter abbreviations like T/R, D/P, C/A
            # These are descriptor codes (e.g. Tipo/Restaurant), not brand tokens
            if re.match(r"^[A-Z](/[A-Z])+\.?$", tok):
                break
            # Stop at parenthesized single-letter gender/size markers: (M), (F), (U)
            if re.match(r"^\([A-Z]\)$", tok):
                break
            # Stop at parenthesized pure-digit tokens — product catalog codes like (2003), (2004)
            if re.match(r"^\(\d+\)$", tok):
                break
            # Require 2+ char core for brand extension (single letters like "B" in "ORAL B"
            # are only absorbed when they directly follow a confirmed brand token)
            if len(core) == 1 and brand_end == brand_start + 1:
                break  # don't start brand with a single-letter continuation
            # Stop at alphanumeric tokens (mix of letters AND digits) — product codes like
            # "C2780", "C4900", "(00000)L.F". Pure numeric tokens ("1882") are allowed —
            # they can be part of a brand year/edition ("BULL DOG 1882").
            if any(c.isdigit() for c in core) and any(c.isalpha() for c in core):
                break
            # Stop at short (≤3 char) uppercase abbreviations ending with dot like "DD." — these
            # are descriptor suffixes (e.g. "Dulce de Leche" abbreviation), not part of brand
            if tok.endswith(".") and len(core) <= 3:
                break
            # Stop at known non-brand suffix abbreviations (product format/variant codes)
            if core.upper() in _BRAND_SUFFIX_ABBREVS:
                break
            brand_end += 1

        brand_raw = " ".join(tokens[brand_start:brand_end])
        variant_raw = " ".join(tokens[brand_end:]).strip() or None
    else:
        # No uppercase brand found — treat entire text as product type
        product_type_raw = text
        brand_raw = None
        variant_raw = None

    # 7. Title-case all parts
    product_type = clean_name(product_type_raw) if product_type_raw else None
    brand         = clean_name(brand_raw)        if brand_raw        else None
    variant       = clean_name(variant_raw)      if variant_raw      else None

    # Apply post-extraction brand aliases (typos, on-site spelling variants)
    if brand and brand.lower() in _BRAND_ALIASES:
        brand = _BRAND_ALIASES[brand.lower()]

    # Apply product_type aliases (Santa Maria abbreviations → canonical form)
    if product_type and product_type.lower() in _PRODUCT_TYPE_ALIASES:
        alias = _PRODUCT_TYPE_ALIASES[product_type.lower()]
        if isinstance(alias, tuple):
            product_type, prefix = alias
            variant = f"{prefix} {variant}" if variant else prefix
        else:
            product_type = alias

    # Apply variant text normalizations (abbreviation expansions, typo fixes)
    if variant:
        for pattern, replacement in _VARIANT_NORMALIZATIONS:
            variant = pattern.sub(replacement, variant)
    if (
        brand == "1882"
        and product_type == "Coctel"
        and variant
        and re.search(r"\bFernet\s*&\s*Cola\b", variant, re.IGNORECASE)
    ):
        product_type = "Fernet"
        variant = "Con Cola"
    if _ascii_fold(brand or "").upper() == "8 HERMANOS" and product_type == "Licor Anis":
        product_type = "Licor"
        variant = "Anis Azul"
    if _ascii_fold(brand or "").upper() == "9 DE ORO":
        if product_type == "Gallet.":
            product_type = "Galletitas"
        if product_type == "Pepas" or (variant and re.search(r"\bPepas\b", variant, re.IGNORECASE)):
            product_type = "Pepas"
            variant = "Membrillo"
        if variant == "Agridulce":
            variant = "Agridulces"
        if variant == "Agridulces Azucarados":
            variant = "Agridulces"
        if variant == "Azucaradas":
            variant = "Azucarados"
        if variant == "Clasico":
            variant = "Clasicos"

    # 8. Build clean_name from structured parts
    parts = [p for p in [product_type, brand, variant] if p]
    result_clean = " ".join(parts)

    return {
        "product_type":  product_type,
        "brand":         brand,
        "variant":       variant,
        "weight_g":      weight_g,
        "volume_ml":     volume_ml,
        "units_in_name": units_in_name,
        "pack_count":    pack_count,
        "units_label":   units_label,
        "bag_dimensions": bag_dimensions,
        "bag_count":     bag_count,
        "length":        length,
        "clean_name":    result_clean,
    }


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
        """Print postprocessed results for 20 random Santa Maria products."""
        pool = await asyncpg.create_pool(
            host=os.getenv("DB_HOST", "localhost"),
            port=int(os.getenv("DB_PORT", 5432)),
            database=os.getenv("DB_NAME", "prices"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASS"),
        )
        rows = await pool.fetch(
            "SELECT sku, name, category, stock "
            "FROM products "
            "WHERE supplier = 'santamaria' "
            "ORDER BY RANDOM() LIMIT 20"
        )
        await pool.close()

        print(
            f"\n{'RAW NAME':<52} {'TYPE':<20} {'BRAND':<18} {'VARIANT':<22} "
            f"{'W(g)':<8} {'V(ml)':<8} {'UN':<4} {'UXB':<5} {'CATEGORY'}"
        )
        print("-" * 175)
        for r in rows:
            f = extract_features(r["name"])
            cat = normalize_category(r["category"])
            uxb = parse_uxb(r["stock"] or "")
            w = f"{f['weight_g']:.0f}" if f["weight_g"] else ""
            v = f"{f['volume_ml']:.0f}" if f["volume_ml"] else ""
            u = str(f["units_in_name"]) if f["units_in_name"] else ""
            print(
                f"{r['name']:<52} "
                f"{(f['product_type'] or ''):<20} "
                f"{(f['brand'] or ''):<18} "
                f"{(f['variant'] or ''):<22} "
                f"{w:<8} {v:<8} {u:<4} {str(uxb or ''):<5} {cat}"
            )

    asyncio.run(run())
