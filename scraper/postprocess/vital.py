"""
Postprocessing for Vital product data.

Run as a standalone pass after scraping:
    python -m scraper.postprocess.vital

Functions are also importable for use in tests or other modules.

Name format: PRODUCT_TYPE BRAND VARIANT [measurement]
  Same order as Maxiconsumo. Brand follows product type.
  Known quirks:
    - OCR split artifacts in Vital catalog: "ne gro", "u ltra", "Ha mlet", "u ltrasuave"
    - Some ALL CAPS names (SECAPLATOS, CEPILLO, etc.)
    - Brand S&P (with ampersand)
"""

import logging
import re
import sys

from scraper.postprocess._utils import (
    _ascii_fold,
    _load_lines,
    _load_aliases,
    clean_name,
    _DATA_DIR,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Load lookup data
# ---------------------------------------------------------------------------

_KNOWN_PRODUCT_TYPES: list[str] = sorted(
    _load_lines("vital_product_types.txt"), key=lambda x: -len(x)
)
_KNOWN_PRODUCT_TYPES_FOLDED: list[tuple[str, str]] = [
    (_ascii_fold(pt), pt) for pt in _KNOWN_PRODUCT_TYPES
]

_PRODUCT_TYPE_ALIAS_MAP: dict[str, str] = _load_aliases("vital_product_type_aliases.txt")

_KNOWN_BRANDS_RAW: list[str] = _load_lines("vital_brands.txt")
_BRAND_FOLD_MAP: dict[str, str] = {_ascii_fold(b): b for b in _KNOWN_BRANDS_RAW}
_KNOWN_BRANDS_FOLDED_SORTED: list[str] = sorted(_BRAND_FOLD_MAP.keys(), key=lambda x: -len(x))

# Multi-word brands (2+ words) sorted: most words first, then longest chars first
_MULTI_WORD_BRANDS: list[tuple[str, str, int]] = sorted(
    [
        (folded, canonical, len(folded.split()))
        for folded, canonical in _BRAND_FOLD_MAP.items()
        if len(folded.split()) >= 2
    ],
    key=lambda x: (-x[2], -len(x[0])),
)

# ---------------------------------------------------------------------------
# OCR artifact cleanup — Vital's catalog has split tokens from encoding issues
# ---------------------------------------------------------------------------

# Pairs of (pattern, replacement) applied before any tokenization.
# Ordered from most-specific to least-specific.
_OCR_FIXES: list[tuple[re.Pattern, str]] = [
    # Split-token artifacts (most-specific first)
    (re.compile(r"\bquitaesma\s+lte\b",   re.IGNORECASE), "quitaesmalte"),
    (re.compile(r"\bboli\s+grafo\b",       re.IGNORECASE), "boligrafo"),
    (re.compile(r"\bvina\s+gre\b",         re.IGNORECASE), "vinagre"),
    (re.compile(r"\bmu\s+ltiuso\b",        re.IGNORECASE), "multiuso"),
    (re.compile(r"\balle\s+gra\b",         re.IGNORECASE), "alegra"),
    (re.compile(r"\ba\s+ccesorios\b",      re.IGNORECASE), "accesorios"),
    (re.compile(r"\badu\s+ltos\b",         re.IGNORECASE), "adultos"),
    (re.compile(r"\badu\s+lto\b",          re.IGNORECASE), "adulto"),
    (re.compile(r"\bu ltra\b",             re.IGNORECASE), "ultra"),
    (re.compile(r"\bne gro\b",             re.IGNORECASE), "negro"),
    (re.compile(r"\bne gra\b",             re.IGNORECASE), "negra"),
    (re.compile(r"\bHa mlet\b",            re.IGNORECASE), "Hamlet"),
    (re.compile(r"\bcole\s+ccionables\b",  re.IGNORECASE), "coleccionables"),
    (re.compile(r"\bpedi\s+gree\b",        re.IGNORECASE), "pedigree"),
    (re.compile(r"\blu\s+cchetti\b",       re.IGNORECASE), "luchetti"),
    (re.compile(r"\bS\s*&\s*P\b",          re.IGNORECASE), "S&P"),    # normalise spaced S & P
    (re.compile(r"\bpantente\b",           re.IGNORECASE), "Pantene"),
    (re.compile(r"\bsinovoe\b",            re.IGNORECASE), ""),       # meaningless token
    # Truncated / abbreviated words
    (re.compile(r"\bResa\s+ltador\b",      re.IGNORECASE), "resaltador"),
    (re.compile(r"\bSuvizante\b",          re.IGNORECASE), "suavizante"),
    (re.compile(r"\bconcentrad\b",         re.IGNORECASE), "concentrado"),
    (re.compile(r"\bPa[nñ]\s+o\b",        re.IGNORECASE), "paño"),
    (re.compile(r"\bMu\s+ltiflon\b",       re.IGNORECASE), "Multiflon"),
    (re.compile(r"\bres\.",                re.IGNORECASE), "residuos"),   # "Bolsa res." → residuos
    (re.compile(r"\bpol\.",                re.IGNORECASE), "polvo"),      # "Leche pol." → polvo
    (re.compile(r"\bliq\b",               re.IGNORECASE), "liquido"),
    (re.compile(r"\bdent\b",              re.IGNORECASE), "dental"),
    (re.compile(r"\bdenta\b",             re.IGNORECASE), "dental"),
    (re.compile(r"\ba\s+lta\b",           re.IGNORECASE), "alta"),        # "A lta Via" → "Alta Via"
    (re.compile(r"\bsuav\b",              re.IGNORECASE), "suavizante"),  # "Suav conc" → "Suavizante conc"
    (re.compile(r"\bconc\b",              re.IGNORECASE), "concentrado"), # "Suavizante conc" → "Suavizante concentrado"
    (re.compile(r"pa\xef\xbf\xbdos?",     re.IGNORECASE), "paños"),      # corrupted ñ (Mojibake) in "paños"
    (re.compile(r"Mu\ufffdeca",           re.IGNORECASE), "Muñeca"),      # corrupted ñ
    (re.compile(r"\bJohson\b",            re.IGNORECASE), "Johnson"),     # typo in catalog
    (re.compile(r"\bKellog\s+s\b",        re.IGNORECASE), "Kelloggs"),   # OCR split
    (re.compile(r"et\ufffdlico",          re.IGNORECASE), "etilico"),    # corrupted í
    (re.compile(r"\bDce\.",               re.IGNORECASE), "Dulce de"),   # "Dce. leche" → "Dulce de leche"
    (re.compile(r"\bA\s+ltos\b",          re.IGNORECASE), "Altos"),      # "A ltos del Plata" brand
    (re.compile(r"\bSa\s+lta\b",          re.IGNORECASE), "Salta"),      # "Sa lta Cautiva" brand
    (re.compile(r"\bTthe\b",              re.IGNORECASE), "The"),         # "Tthe Famous Grouse" typo
    (re.compile(r"\bfurbol\b",            re.IGNORECASE), "futbol"),      # "Pelota furbol"
    (re.compile(r"\bmu\s+ltienvase\b",    re.IGNORECASE), "multienvase"), # OCR split
    (re.compile(r"\bmu\s+ltisemillas\b",  re.IGNORECASE), "multisemillas"),
    (re.compile(r"\bmu\s+ltiflon\b",      re.IGNORECASE), "multiflon"),   # "Mu ltiflon" set
    (re.compile(r"\bco\s+ccion\b",         re.IGNORECASE), "coccion"),     # "Co ccion Multiflon"
    (re.compile(r"\bSe\s+lton\b",         re.IGNORECASE), "Selton"),      # "Se lton" insecticide brand
    (re.compile(r"\bMa\s+lta\b",          re.IGNORECASE), "Malta"),       # "Ma lta El Pocillo"
    (re.compile(r"\bparrila\b",            re.IGNORECASE), "parrilla"),   # missing double-l
    (re.compile(r"\bparrilera\b",         re.IGNORECASE), "parrillera"),  # typo in catalog
    (re.compile(r"\bAanclas\b",           re.IGNORECASE), "Anclas"),      # "Dos Aanclas" typo
    (re.compile(r"\bToa\b(?=\s+hum\b)",   re.IGNORECASE), "Toallita"),   # "Toa hum" → toallita humeda
    (re.compile(r"\bdenral\b",            re.IGNORECASE), "dental"),      # "crema denral" typo
    (re.compile(r"\bCasa\s+lta\b",        re.IGNORECASE), "Casalta"),    # "vinagre Casalta"
    (re.compile(r"\bSt\.\s+Tropez\b",     re.IGNORECASE), "St Tropez"),  # "St. Tropez" brand
    (re.compile(r"\binf\.",               re.IGNORECASE), "infantil"),    # "Leche inf." — period BEFORE bare \binf\b
    (re.compile(r"\binf\b",               re.IGNORECASE), "infantil"),   # "Leche inf Nutrilon"
    (re.compile(r"\bMa\s+ltiuse\b",       re.IGNORECASE), "multiuse"),
    (re.compile(r"\bhigienioc\b",         re.IGNORECASE), "higienico"),   # typo in catalog
    (re.compile(r"\bhig\.",               re.IGNORECASE), "higienico"),   # "Papel hig." abbreviation
    (re.compile(r"\best\.",               re.IGNORECASE), "est"),         # "Vino Est. Mendoza"
    (re.compile(r"\bE\s+ccole\b",         re.IGNORECASE), "Eccole"),     # OCR split brand

    (re.compile(r"\bextra\b",             re.IGNORECASE), "extra"),       # "etra brut" → probably "extra"
    (re.compile(r"\betra\b",              re.IGNORECASE), "extra"),       # common OCR for "extra"
    # Brand typos / OCR truncations
    (re.compile(r"\bNoble\b",             re.IGNORECASE), "Noblex"),      # "Smart TV Noble 4k" → Noblex
    (re.compile(r"\bMorie\b",             re.IGNORECASE), "Morixe"),      # "Aceite Morie virgen" → Morixe
    (re.compile(r"\bGilette\b",           re.IGNORECASE), "Gillette"),    # brand typo in catalog
    (re.compile(r"\bRoya\b",              re.IGNORECASE), "Royal"),       # OCR-truncated "Royal"
    (re.compile(r"\bPlmero\b",            re.IGNORECASE), "Plomero"),     # typo for Plomero brand
    (re.compile(r"\bcoffe\s+mate\b",      re.IGNORECASE), "Coffee Mate"), # "Coffe Mate" typo
    (re.compile(r"\bKrach-itos\b",        re.IGNORECASE), "Krachitos"),   # hyphen in catalog variant
    (re.compile(r"\blimp\b",              re.IGNORECASE), "limpiador"),   # "Limp cremoso" → Limpiador
    # Word / abbreviation fixes
    (re.compile(r"\bdulc\b",              re.IGNORECASE), "dulce"),       # "dulc de leche"
    (re.compile(r"\binstantantaneo\b",    re.IGNORECASE), "instantaneo"), # double-t typo
    (re.compile(r"\binst\.",              re.IGNORECASE), "instantaneo"), # "cafe inst." abbreviation
    (re.compile(r"\b9de\b",              re.IGNORECASE), "9 de"),         # "9de oro" → "9 de oro"
    (re.compile(r"Pond[\u00b4\ufffd''']s\s+s\b", re.IGNORECASE), "Pond's"), # "Pond´s s" — spurious trailing s (´ is U+00B4)
    # OCR split artifacts
    (re.compile(r"\bMA\s+ltIFE\b",       re.IGNORECASE), "Maltife"),     # OCR split brand
    (re.compile(r"\bLumila\s+gro\b",     re.IGNORECASE), "Lumilagro"),   # OCR split brand
    (re.compile(r"Sierra\s+D/Padres",    re.IGNORECASE), "Sierra De Los Padres"),
    (re.compile(r"LE\ufffdA",            re.IGNORECASE), "Leña"),        # corrupted ñ in "Leña"
    (re.compile(r"\bPRESTOBARBA\d+\b",   re.IGNORECASE), "Prestobarba"), # "PRESTOBARBA2" → Prestobarba
    (re.compile(r"\bPamola\b",           re.IGNORECASE), "Paloma"),      # typo in catalog
    (re.compile(r"\bProce[n]e\b",        re.IGNORECASE), "Procenex"),    # typo "Procene" in catalog
    (re.compile(r"^WD-40\b",             re.IGNORECASE), "Lubricante WD-40"),  # brand-only name, prepend type
]


def _fix_ocr(text: str) -> str:
    """Apply OCR artifact fixes to a raw product name."""
    # Strip leading artifact quote (e.g. '"Smart TV Bgh 43"""')
    text = re.sub(r'^"', '', text)
    # Collapse trailing runs of 2+ quotes to a single inch marker
    text = re.sub(r'"{2,}$', '"', text)
    for pattern, replacement in _OCR_FIXES:
        text = pattern.sub(replacement, text)
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# Measurement patterns (identical to maxiconsumo)
# ---------------------------------------------------------------------------

def _parse_number(s: str) -> float:
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
    r"\b(\d+(?:[.,]\d+)?)\s*(lts?|litros?|ml|cc|cm3|l)\b",
    re.IGNORECASE,
)
_UNITS_RE = re.compile(
    r"\b(?:x\s*(\d+)\s*(?:u(?:n(?:d|idades?)?)?|piezas?|sobres?|saquitos?|sq|pa[ñn]os?)?|(\d+)\s*(?:u(?:n(?:d|idades?)?)?|piezas?|sobres?|saquitos?|sq|pa[ñn]os?))\b",
    re.IGNORECASE,
)
_UNITS_LABEL_RE = re.compile(
    r"\b(\d+)\s*(piezas?|sobres?|saquitos?|sq|pa[ñn]os?|u(?:n(?:d|idades?)?)?)\b",
    re.IGNORECASE,
)
_CONTAINER_RE = re.compile(
    r"\b(botella|pote|bolsa|caja|lata|frasco|sachet|saquito|doy\s*pack|pet|pvc|petaca|sobre|barra|tira)\b",
    re.IGNORECASE,
)

_WEIGHT_UNITS = {
    "kg": "kg", "kilo": "kg", "kilos": "kg",
    "gr": "g", "grs": "g", "gramos": "g", "g": "g",
}
_VOLUME_UNITS = {
    "lt": "l", "lts": "l", "litro": "l", "litros": "l", "l": "l",
    "ml": "ml", "cc": "ml", "cm3": "ml",
}
_INCHES_RE = re.compile(r'\b(\d+(?:[.,]\d+)?)\s*"', re.IGNORECASE)
_DIMENSIONS_RE = re.compile(r"\b(\d+)\s*[xX]\s*(\d+)\s*(cm)\b", re.IGNORECASE)
# NxM without explicit "cm": both numbers must be ≥10; not followed by a unit suffix
# (avoids matching multipacks like "14X12 UN").
_NOTEBOOK_DIM_RE = re.compile(
    r"\b([1-9]\d+)\s*[xX]\s*([1-9]\d+)\b(?!\s*(?:u(?:n(?:d|idades?)?)?|piezas?|sobres?)\b)",
    re.IGNORECASE,
)
_HOJAS_RE = re.compile(r"\b(\d+)\s*h(?:ojas?)?\b", re.IGNORECASE)
_SINGLE_CM_RE = re.compile(r"\b(\d+(?:[.,]\d+)?)\s*(cm)\b", re.IGNORECASE)
_SINGLE_MM_RE = re.compile(r"\b(\d+(?:[.,]\d+)?)\s*(mm)\b", re.IGNORECASE)
_LENGTH_RE = re.compile(r"\b(\d+(?:[.,]\d+)?)\s*(mts?|metros?|m)\b", re.IGNORECASE)
# Ball / product nominal size (e.g. "n5" → N°5)
_SIZE_CODE_RE = re.compile(r"\bN[°º]?\s*(\d+)\b", re.IGNORECASE)
# Paños (sheets) multipack: "NxM paños" (after preprocessing normalises the source text)
_PANOS_MULTIPACK_RE = re.compile(r"\b(\d+)\s*[xX]\s*(\d+)\s*(pa[ñn]os?)\b", re.IGNORECASE)

# Product-type first words where a trailing bare number is NOT a volume (e.g. grill model codes).
_NO_VOLUME_TRAIL_STARTS = frozenset({"PARRILLA", "PARRILLERA", "ASADOR", "FOGON", "SECADOR", "SMART"})

# NxM units multipack (e.g. "10 x 250 u") — run before UNITS_RE / NOTEBOOK_DIM_RE
_UNITS_PACK_RE = re.compile(
    r"\b(\d+)\s*[xX]\s*(\d+)\s*(u(?:n(?:d|idades?)?)?|piezas?|sobres?)\b",
    re.IGNORECASE,
)
# Product-type first words where a trailing bare integer is a cm dimension, not a volume.
_CM_TRAIL_STARTS = frozenset({"SECADOR"})
# Product-type first words where ALL size data should be suppressed (descriptive, not measurable).
_NO_SIZE_TYPES = frozenset({"SET", "SILLA", "SOPORTE"})

def _to_grams(value: float, unit: str) -> float:
    return value * 1000 if _WEIGHT_UNITS[unit] == "kg" else value

def _to_ml(value: float, unit: str) -> float:
    return value * 1000 if _VOLUME_UNITS[unit] == "l" else value


# ---------------------------------------------------------------------------
# Brand / product-type extraction
# ---------------------------------------------------------------------------

_BRAND_ARTICLES = {"LA", "EL", "LOS", "LAS", "LO", "LE", "DON", "SAN", "SANTA"}
_SKIP_AS_BRAND  = {"DE", "DEL", "EN", "CON", "AL", "A", "Y", "MENU"}

# Product types where the brand is always Generico (these are generic item categories)
_ALWAYS_GENERIC_PRODUCT_TYPES = frozenset({
    "JARRO", "MANTEL", "REPOSERA", "SILLA", "SILLON",
    "TAZA", "TABLA", "TENDER", "TENEDOR", "VASO",
    "PLATO", "PLATOS", "BANQUETA", "MACETA", "SET",
})

# Hard-coded corrections: folded token → canonical brand (or "Generico")
_BRAND_CORRECTIONS: dict[str, str] = {
    "RAZA":      "Generico",   # "Alimento para perro Raza carne" — "Raza" is a descriptor
    "5":         "Generico",   # "Granola 5 semillas" — "5" is not a brand
    "2":         "Generico",   # "Escurreplatos 2 estantes" — "2" is not a brand
    "53":        "Generico",   # "Arroz largo fino 53" — rice variety code
    "00000":     "Generico",   # "Arroz largo fino 00000" — flour/rice grade code
    "SIN":       "Generico",   # catch "sin costura" leaking as brand
    "KIT":       "Generico",   # "Tintura 317 kit 0.11" — "kit" is format, not brand
    # MIX removed — CEREAL MIX is a real brand; MIX alone is in brands.txt
    "ROLLO":     "Generico",   # "Bolsa de arranque rollo" — packaging format
    "PREMIUM":   "Generico",   # "Arbol de navidad premium" — quality descriptor
    "CASERA":    "Generico",   # "Mayonesa casera Hellmanns" — style descriptor
    "PREPARADO": "Generico",   # "Arroz preparado Gallo" — preparation descriptor
    "CAPILAR":   "Generico",   # "Tratamiento capilar Sedal" — hair-care descriptor
    "SURTIDO":   "Generico",   # "Galletitas surtido Bagley" — assortment descriptor
    "CRACKERS":  "Generico",   # "Galletitas crackers S&P" — variety descriptor
    "INODORO":   "Generico",   # "Limpiador inodoro Harpic" — fixture descriptor
    "PAPEL":     "Generico",   # "Toalla de papel Elite" — material descriptor
    "ALGODON":   "Generico",   # "Pano algodon Estrella" — material descriptor
    "FINO":      "Generico",   # "Alcohol fino Carmel" — descriptor (not brand)
    "CONSORCIO":   "Generico",   # "Bolsa de consorcio Bonux" — bag type descriptor
    "DESCARTABLE": "Generico",   # "Maquina de afeitar descartable" — format descriptor
    "GEL":         "Generico",   # "Boligrafo de gel Bic" — material/format descriptor
    "DIGITAL":     "Generico",   # "Freidora digital Bonux" — tech descriptor
    "NYLON":       "Generico",   # "Espatula de nylon" — material descriptor
    "FOIL":        "Generico",   # "Papel aluminio foil" — format descriptor
    "RAYADO":      "Generico",   # "Repasador rayado" — pattern descriptor
    "MADERA":      "Generico",   # "Broches de madera" — material descriptor
    "PLASTICO":    "Generico",   # "Broches de plastico" — material descriptor
    "LISO":        "Generico",   # "Individual liso" — pattern descriptor
    "HERMETICO":   "Generico",   # "Frasco hermetico vidrio" — type descriptor
    "SM":          "Generico",   # "Platos sm" — size code
    "BLANCO":      "Generico",   # "Silla plegable blanca" — color
    "BLANCA":      "Generico",   # same
    "AUTOADHERENTE": "Generico", # "Film pvc autoadherente" — property descriptor
    "SANITARIAS":  "Generico",   # "Piedras sanitarias" → next token is brand
    "AZUL":        "Generico",   # color descriptor
    "VIDRIO":      "Generico",   # material descriptor
    "KING":        "Generico",   # size descriptor (bedding king size)
    "PARA":        "Generico",   # preposition leaking as brand
    "ACERO":       "Generico",   # material descriptor
    "VARIOS":      "Generico",   # generic descriptor
    "PLEGABLE":    "Generico",   # format descriptor (when leaking as brand)
    "BORDADO":     "Generico",   # embroidery descriptor
    "ESPECIAL":    "Generico",   # quality descriptor
    "+":           "Generico",   # "Set Cubiertos + organizador" — punctuation leaking as brand
    "ESPIRAL":     "Generico",   # "Cuaderno espiral" — format descriptor
    "SKINPACK":    "Generico",   # packaging format
    "ASADOR":      "Generico",   # "Set Asador" — set type descriptor
    "BOWL":        "Generico",   # "Set bowl" — container descriptor
    "BOWLS":       "Generico",
    "TARROS":      "Generico",
    "SILLAS":      "Generico",   # "Set de sillas"
    "VALIJAS":     "Generico",
    "JUEGO":       "Generico",   # "Set juego exterior"
    "CONTENEDORES":"Generico",
    "TERCIOPELO":  "Generico",
    "MICROFIBRA":  "Generico",
    "POLYESTER":   "Generico",
    # "INVERTER" removed — it IS a standalone brand for certain appliances (Lavasecarropas Inverter)
    "9":           "Generico",   # tender / numbers
    "11":          "Generico",
    "COCCION":     "Generico",   # "Set coccion" — if type doesn't match
    "PORTABLE":    "Generico",
    "AUTOMATICO":  "Generico",
    "FULL":        "Generico",   # "CUBRECAMA full queen"
    "SPINNING":    "Generico",
    "SOPORTE":     "Generico",   # "ALMOHADA soporte" — pillow type
    "FELICITY":    "Generico",   # "ALMOHADA felicity" — pillow name
    "TIPO":        "Generico",   # "Salame tipo fuet" — type descriptor
    "1.8 M":       "Generico",   # Christmas tree height
    "1.2M":        "Generico",
    "1.8M":        "Generico",
    "CHICO":       "Generico",   # size descriptor
    "PALABRAS":    "Generico",   # decorative descriptor
    "DISENOS":     "Generico",
    "FLOTADORA":   "Generico",   # "Barra flotadora"
    "FIJO":        "Generico",   # "Soporte TV fijo"
    "ARTICULADO":  "Generico",
    "HUM":         "Generico",   # abbreviated product type
    "AJO":         "Generico",   # "Sal con ajo" → ajo is flavor not brand
    "SMART TV":    "Generico",   # leaks from "PHILIPS smart tv" reverse order
    "TAZAS":       "Generico",   # "Set de tazas" — container descriptor
    "VEGANA":      "Generico",   # "Mayonesa vegana" — dietary descriptor
    "CARAMELO":    "Generico",   # "Salsa de caramelo" → next is brand
    "REPOSTERIA":  "Generico",   # "Salsa de reposteria" → descriptor
    "HONDO":       "Generico",   # "Plato hondo" → shape descriptor
    "POSTRE":      "Generico",   # "Plato postre" → usage descriptor
    "CONCENTRADO": "Generico",   # "Jugo concentrado" → next token is brand
    "ELECTRONICA": "Generico",   # "Caja de seguridad electronica"
    "ELECTRICO":   "Generico",   # "Generador electrico Philco"
    "CU4TRO":      "Generico",   # wine label OCR artifact (not a lookup brand)
    "12V":         "Generico",   # voltage code, not brand
    "TURBO":       "Generico",   # "Ventilador turbo" — type descriptor
    "BRONCE":      "Generico",   # "Esponja bronce" — material/type descriptor
    "CORP":        "Generico",   # "Deo corp" — category descriptor
    "LIMPIEZA":    "Generico",   # "Kit limpieza auto" — category descriptor
    # Brand typo / OCR corrections
    "BONU":          "Bonux",          # OCR truncation
    "LU":            "Lux",            # OCR abbreviation of Lux
    "LS":            "La Serenisima",  # abbreviation for La Serenisima
    "BEBE ADVANCED": "Sancor",         # Sancor sub-brand
    "REONA":         "Rexona",         # OCR/typo for Rexona
    "NOBLE":         "Noblex",         # backup for OCR fix
    "MORIE":         "Morixe",         # backup for OCR fix
    "AA":            "Ana",            # OCR "Aa" → Ana brand
    "RE":            "Rex",            # "Galletitas Re" → Rex brand
    # Additional descriptors → Generico
    "MA LIGHT":      "Generico",
    "CENTRO ESTANT": "Generico",
    "AIR PUR":       "Generico",
    "NADIR":         "Generico",
    "ETE":           "Generico",
    "RECLINABLE":    "Generico",
    "CASSIA":        "Generico",       # JARRO color/style
    "CHROMAT":       "Generico",       # JARRO style
    "COLORFUL":      "Generico",       # JARRO color
    "MUG":           "Generico",       # JARRO type
    "FELIZ":         "Generico",
    "TOPES":         "Generico",
    "TRANSPORTADOR": "Generico",
    "ANDADOR":       "Generico",       # baby walker
    "COCHECITO":     "Generico",       # stroller
    "DOMINADAS":     "Generico",       # exercise bar
    "STEFI":         "Generico",
    "AMIGOS":        "Generico",   # "Bolsa ecologica amigos" — design name, not brand
    # Brand via type extraction / descriptor corrections
    "MAIROLLO":      "Elite",      # "Elite Mairollo rollo cocina" — Elite is the brand
    "JA5629":        "Generico",   # product code leaking as brand
    "JA5668":        "Generico",
    "FIJA":          "Generico",   # "Bicicleta fija spinning"
    "AUTOARMABLE":   "Generico",
    "DIDACTICO":     "Generico",
    "HUEVAZOS":      "Generico",
    "JUNGLA":        "Generico",
    "LOOK":          "Generico",
    "SLIM":          "Generico",
    "SOBREMESA":     "Generico",
    "ESTUDIO":       "Generico",
    "ANIMALES":      "Generico",
    "BEIGE":         "Generico",
    "COCKTAILS":     "Generico",
    "AZUL/BLANCO":   "Generico",
    "COCHE/CUNA/SILLA": "Generico",
    "FRUTALES":      "Generico",
    "CEREALES":      "Generico",
    "AZUCARADOS":    "Generico",
    "SONIDO":        "Generico",
    "PRIMAVERA":     "Generico",
    "GRANO":         "Generico",
    "CUBETEADO":     "Generico",
    # MANTECADAS is a real brand — no correction
    "GARBANZO":      "Generico",
    "PELADO":        "Generico",
    "UNTABLE":       "Generico",
    "ESCOLARES":     "Generico",
    "TRAZADO":       "Generico",
    "MANTECA":       "Generico",
    "AUTOS":         "Generico",
    "BATERIA":       "Generico",
    "DORMIR":        "Generico",
    "INDUSTRIAL":    "Generico",
    "LACTEA":        "Generico",
    "ACCESORIOS":    "Generico",
    "MATA":          "Generico",
    "MATACUCARACHAS":"Generico",
    "PERITA":        "Generico",
    "TROZOS":        "Generico",
    "VERDURAS":      "Generico",
    "PISOS":         "Generico",
    "COCINA":        "Generico",
    "MANO":          "Generico",
    "LUCES":         "Generico",
    "LUCES Y":       "Generico",
    "SONIDO Y":      "Generico",
    "DULCE":         "Generico",
    "DULCES":        "Generico",
    "GRASA":         "Generico",
    "CREMOSO":       "Generico",
    "SOLIDA":        "Generico",
    "JA5629 CC":     "Generico",
    "JA5668 CC":     "Generico",
    "SOPA":          "Generico",
    "PASTA":         "Generico",
    "MESA":          "Generico",
    "PIE":           "Generico",
    "MARCO DE":      "Generico",
    "TE":            "Generico",
    "ZULU":          "Generico",
    "VETERINARIA":   "Generico",
    "FAMILIA":       "Generico",
    "6V":            "Generico",
    "6":             "Generico",
    "33MT":          "Generico",   # "Gazebo plegable 33mt" — size code, no brand
    "2026":          "Generico",   # year code leaking as brand (e.g. "Arbol de navidad 1.8 m 2026")
    "FANTASIA":      "Generico",   # design descriptor, not a brand (e.g. "Cartuchera fantasia 2 pisos")
    # Sub-brand / product line → parent brand
    "HILERET STEVIA":  "Hileret",
    "HILERET SWEET":   "Hileret",
    "HILERET ZUCRA":   "Hileret",
    "BAGGIO FRESH":    "Baggio",
    "BAGGIO PRONTO":   "Baggio",
    "ALA CAMELLITO":   "Ala",
    "ALA ULTRA":       "Ala",
    "BABYSEC PREMIUM": "Babysec",
    "BABYSEC ULTRA":   "Babysec",
    "CIEL CRYSTAL":    "Ciel",
    "CIEL NUIT":       "Ciel",
    "AXE BLACK":       "Axe",
    "PRONTO BITT":     "Pronto",
    "PRONTO SHAKE":    "Pronto",
    "PRESTOBARBA":     "Gillette",
    "MACH 3":          "Gillette",
    "PITU":            "Pitusas",
    "SIMPLY VENUS":    "Venus",
    "SENSE":           "La Serenisima",  # La Serenisima product line (e.g. "Bebida lactea Sense")
    # Descriptors / non-brands → Generico
    "MIR FITNESS":     "Generico",
    "ZUCO":            "Generico",
    "RECICLA":         "Generico",
    "REUTILIZABLE":    "Generico",
    "REUTILIZ.AMAR.":  "Generico",
    "HELLMANNS":       "Hellmans",   # normalize to single-n canonical
    "CIF ACTIVE GEL":  "Cif",
    "BIO ACTIVE":      "Cif",
    "S&P":             "S&P",        # fix clean_name capitalize artifact ("S&p" → "S&P")
    "PALOMA":          "Paloma Herrera",
    "REPUESTO":        "Generico",   # "Lampazo repuesto" — spare part descriptor
    "RESPOSTERIA":     "Generico",   # typo for "repostería" — descriptor, not brand
    "LA HUERTA":       "De La Huerta",
    "DE LA HUERTA":    "De La Huerta",  # ensure correct capitalization after clean_name
    "WD-40":           "WD-40",        # fix clean_name "Wd-40" capitalization artifact
    # Near-duplicate brand merges (from similarity analysis)
    "BUDUCCO":         "Bauducco",
    "BULL DOG":        "Bulldog",
    "CLOSEUP":         "Close Up",
    "GILLETE":         "Gillette",
    "LAYS":            "Lay's",
    "LAY'S":           "Lay's",        # ensure canonical apostrophe form
    "LUCHETTI":        "Lucchetti",
    "MR POP":          "Mr Pops",
    "MR MUSCULO":      "Mr. Músculo",
    "MR. MUSCULO":     "Mr. Músculo",
    "MR.MUSCULO":      "Mr. Músculo",
    "NAVARO CORREAS":  "Navarro Correas",
    "NESQUICK":        "Nesquik",
    "Q-SOFT":          "Q Soft",
    "ROYACAMP":        "Royakamp",
    "ST TROPEZ":       "Saint Tropez",
    "TRANQUERA":       "La Tranquera",
}


def _find_multi_word_brand(tokens: list[str]) -> tuple[int, int, str] | None:
    """
    Scan all positions in the token list for any known multi-word brand.

    Returns (start_idx, end_idx_exclusive, canonical_brand) for the longest
    match found (most words wins; on ties, earliest position wins).
    Returns None if no multi-word brand is present.
    """
    folded_tokens = [_ascii_fold(t) for t in tokens]
    for folded_brand, canonical, word_count in _MULTI_WORD_BRANDS:
        brand_toks = folded_brand.split()
        for i in range(len(folded_tokens) - word_count + 1):
            if folded_tokens[i : i + word_count] == brand_toks:
                return (i, i + word_count, canonical)
    return None


def _extract_type_from_tokens(tokens: list[str]) -> tuple[str | None, int]:
    """Extract product type from a token list; returns (type, words_consumed)."""
    if not tokens:
        return None, 0
    text = " ".join(tokens)
    folded = _ascii_fold(text)
    for folded_pt, canonical_pt in _KNOWN_PRODUCT_TYPES_FOLDED:
        n = len(folded_pt)
        if folded.startswith(folded_pt) and (len(folded) == n or folded[n] == " "):
            pt = clean_name(canonical_pt)
            wc = len(canonical_pt.split())
            folded_key = _ascii_fold(pt.upper())
            if folded_key in _PRODUCT_TYPE_ALIAS_MAP:
                pt = clean_name(_PRODUCT_TYPE_ALIAS_MAP[folded_key])
            return pt, wc
    # fallback: first token
    pt = tokens[0].capitalize()
    folded_key = _ascii_fold(pt.upper())
    if folded_key in _PRODUCT_TYPE_ALIAS_MAP:
        pt = clean_name(_PRODUCT_TYPE_ALIAS_MAP[folded_key])
    return pt, 1


def _extract_product_type_and_brand(
    tokens: list[str],
) -> tuple[str | None, str | None, list[str]]:
    # -----------------------------------------------------------------------
    # Brand-first pass: scan full token sequence for any multi-word brand.
    # If found, the brand position splits tokens — everything before is the
    # product type, everything after is the variant.
    # -----------------------------------------------------------------------
    multi = _find_multi_word_brand(tokens)

    if multi is not None:
        brand_start, brand_end, brand_canonical = multi
        brand: str | None = clean_name(brand_canonical)

        # Apply brand corrections
        folded_brand = _ascii_fold(brand.upper())
        if folded_brand in _BRAND_CORRECTIONS:
            brand = _BRAND_CORRECTIONS[folded_brand]

        before_tokens = tokens[:brand_start]
        after_tokens  = tokens[brand_end:]

        if before_tokens:
            product_type, pt_wc = _extract_type_from_tokens(before_tokens)
            remaining = before_tokens[pt_wc:] + after_tokens
        else:
            product_type = None
            remaining = after_tokens

        if brand is None:
            brand = "Generico"

        if product_type is not None and product_type.upper().split()[0] in _ALWAYS_GENERIC_PRODUCT_TYPES:
            brand = "Generico"

        return product_type, brand, remaining

    # -----------------------------------------------------------------------
    # Fallback: original type-first logic (single-word brands / no multi-word
    # brand found in the sequence).
    # -----------------------------------------------------------------------
    upper_text = " ".join(tokens)

    # --- product type ---
    product_type: str | None = None
    pt_word_count = 0
    folded_text = _ascii_fold(upper_text)

    for folded_pt, canonical_pt in _KNOWN_PRODUCT_TYPES_FOLDED:
        n = len(folded_pt)
        if folded_text.startswith(folded_pt) and (len(folded_text) == n or folded_text[n] == " "):
            product_type = clean_name(canonical_pt)
            pt_word_count = len(canonical_pt.split())
            break

    if product_type is None and tokens:
        product_type = tokens[0].capitalize()
        pt_word_count = 1

    if product_type is not None:
        folded_pt = _ascii_fold(product_type.upper())
        if folded_pt in _PRODUCT_TYPE_ALIAS_MAP:
            product_type = clean_name(_PRODUCT_TYPE_ALIAS_MAP[folded_pt])

    remaining = tokens[pt_word_count:]
    remaining_text = " ".join(remaining)

    # --- brand: lookup first ---
    brand = None
    brand_word_count = 0
    folded_remaining = _ascii_fold(remaining_text)

    for folded_b in _KNOWN_BRANDS_FOLDED_SORTED:
        n = len(folded_b)
        if folded_remaining.startswith(folded_b) and (n == len(folded_remaining) or folded_remaining[n] == " "):
            brand = clean_name(_BRAND_FOLD_MAP[folded_b])
            brand_word_count = len(folded_b.split())
            break

    if brand is not None:
        remaining = remaining[brand_word_count:]
    else:
        if remaining:
            if remaining[0] in _SKIP_AS_BRAND and len(remaining) > 1:
                remaining = remaining[1:]
            if remaining:
                first = remaining[0]
                if first in _BRAND_ARTICLES and len(remaining) >= 2:
                    brand = clean_name(f"{remaining[0]} {remaining[1]}")
                    remaining = remaining[2:]
                else:
                    brand = first.capitalize()
                    remaining = remaining[1:]
                    if remaining and len(remaining[0]) <= 2 and remaining[0].isalpha():
                        brand = f"{brand} {remaining[0].upper()}"
                        remaining = remaining[1:]

    if brand is not None:
        folded_brand = _ascii_fold(brand.upper())
        if folded_brand in _BRAND_CORRECTIONS:
            brand = _BRAND_CORRECTIONS[folded_brand]

    if brand is None:
        brand = "Generico"

    # Force Generico for product types that are inherently generic (no meaningful brand)
    if product_type is not None and product_type.upper().split()[0] in _ALWAYS_GENERIC_PRODUCT_TYPES:
        brand = "Generico"

    return product_type, brand, remaining


# ---------------------------------------------------------------------------
# Main feature extraction
# ---------------------------------------------------------------------------

def extract_features(name: str) -> dict:
    """
    Extract structured features from a raw Vital product name.

    Returns a dict with keys:
        product_type   str | None
        brand          str | None
        variant        str | None
        weight         {"value": float, "unit": str} | None   — canonical: g
        volume         {"value": float, "unit": str} | None   — canonical: ml
        units_in_name  int | None
        clean_name     str
    """
    text = _fix_ocr(name).upper()
    _orig_first_word = _ascii_fold(text.strip().split()[0]).upper() if text.strip() else ""
    # Remove known model codes that contain digits followed by "cc" (avoids false volume match)
    text = re.sub(r"\bJA\d{4,}\s*(?:CC)?\b", "", text, flags=re.IGNORECASE)
    # Fix double-letter unit OCR artifacts (e.g. "700mll" → "700ml")
    text = re.sub(r"mll\b", "ml", text, flags=re.IGNORECASE)
    # Strip x-prefix + expand truncated "c" → "cc" (e.g. "x355c" → "355 cc")
    text = re.sub(r"\bx(\d+(?:[.,]\d+)?)\s*c\b", r"\1 cc", text, flags=re.IGNORECASE)
    # Bare Nc at end of string: c means cc (e.g. "750c" → "750 cc")
    text = re.sub(r"\b(\d{3,4})\s*c\s*$", r"\1 cc", text, flags=re.IGNORECASE)
    # Metric prefix "k" before "gr/grs" → kg (e.g. "3k gr" → "3 kg", "1.5k grs" → "1.5 kg")
    text = re.sub(r"\b(\d+(?:[.,]\d+)?)\s*k\s*grs?\b", r"\1 kg", text, flags=re.IGNORECASE)
    # Fix missing space before bare "L" unit (e.g. "1.25L" → "1.25 L", but not "1.25LT")
    text = re.sub(r"(\d)(l)(?![a-z])", r"\1 \2", text, flags=re.IGNORECASE)
    # Fix missing space before bare "M" unit (e.g. "1.2m" → "1.2 m", but not "1.2ml")
    text = re.sub(r"(\d)(m)(?![a-z])", r"\1 \2", text, flags=re.IGNORECASE)
    # Fix missing space before "cm" in dimension strings (e.g. "50x70cm" → "50x70 cm")
    text = re.sub(r"(\d)(cm)\b", r"\1 \2", text, flags=re.IGNORECASE)
    # OCR artifact: repeated dimension without "x" (e.g. "229229 cm" → "229x229 cm")
    text = re.sub(r"\b(\d{2,3})\1\s*(cm)\b", r"\1x\1 \2", text, flags=re.IGNORECASE)
    # Fix missing space between letter and number+unit (e.g. "pollo8 kg" → "pollo 8 kg")
    text = re.sub(
        r"([A-Z])(\d+(?:[.,]\d+)?)\s*(kg|kilo|kilos|gr|grs|gramos|g|lts?|litros?|ml|cc|cm3)\b",
        r"\1 \2 \3", text, flags=re.IGNORECASE,
    )
    # Fix missing space between container word and number (e.g. "botella250" → "botella 250")
    text = re.sub(
        r"\b(botella|pote|bolsa|caja|lata|frasco|sachet|saquito|barra|tira)(\d)",
        r"\1 \2", text, flags=re.IGNORECASE,
    )
    # Strip leading "x" before number+unit (e.g. "x400ml" → "400ml", "x1.5lt" → "1.5lt")
    text = re.sub(
        r"\bx(\d+(?:[.,]\d+)?)\s*(kg|kilo|kilos|gr|grs|gramos|g|lts?|litros?|ml|cc|cm3)\b",
        r"\1 \2", text, flags=re.IGNORECASE,
    )
    # Bare number after container word at end of string → assume CC (e.g. "lata 500" → "lata 500 CC")
    # Using end-of-string anchor avoids backtracking false positives (e.g. "botella 500 cc").
    text = re.sub(
        r'\b(botella|lata|frasco|pote|bidon)\s+(\d+(?:[.,]\d+)?)\s*$',
        r"\1 \2 CC", text, flags=re.IGNORECASE,
    )
    # Convert "pulgadas" (Spanish word for inches) to inch symbol (e.g. "16 pulgadas" → '16"')
    text = re.sub(r"\b(\d+(?:[.,]\d+)?)\s*pulgadas?\b", r'\1"', text, flags=re.IGNORECASE)
    # Smart TV screen sizes: convert bare integer 20-109 to inches (e.g. "32" → '32"', "65" → '65"')
    # Negative lookahead avoids already-converted '"' and unit chars (K for 4K, metric units, digits).
    if re.search(r"\bSMART\s+TV\b", text, re.IGNORECASE):
        text = re.sub(
            r"\b((?:2\d|[3-9]\d|10[0-9]))\b(?!\"|\s*[KkGgMmLlCcIi\d])",
            r'\1"', text, flags=re.IGNORECASE,
        )
    # Paños (paper sheets) normalisation — run in order:
    # 0. Split letter immediately before digit-p (e.g. "gigante200p" → "gigante 200p")
    text = re.sub(r"([a-zA-Z])(\d+p)\b", r"\1 \2", text, flags=re.IGNORECASE)
    # 1. Compact "NuxMp" → "NxM paños" (e.g. "3ux60p" → "3x60 paños")
    text = re.sub(r"\b(\d+)\s*u\s*[xX]\s*(\d+)\s*p\b", r"\1x\2 paños", text, flags=re.IGNORECASE)
    # 2. Bare "Np" → "N paños" (e.g. "60p", "200p")
    text = re.sub(r"\b(\d+)\s*p\b", r"\1 paños", text, flags=re.IGNORECASE)
    # 3. "M u N paños" → "MxN paños" (e.g. "3 u 120 paños" → "3x120 paños")
    text = re.sub(
        r"\b(\d+)\s*u\s+(\d+)\s*(pa[ñn]os?)\b",
        r"\1x\2 \3", text, flags=re.IGNORECASE,
    )
    # 4. "N paños [opt word] M u" → "MxN paños" (e.g. "50 paños premium 3 u" → "3x50 paños")
    text = re.sub(
        r"\b(\d+)\s*(pa[ñn]os?)(?:\s+\w+)?\s+(\d+)\s*u(?:n(?:idad)?)?\b",
        r"\3x\1 \2", text, flags=re.IGNORECASE,
    )
    # Split "Nmt digit" (e.g. "30mt4" → "30 mt 4") so LENGTH_RE and UNITS_RE can parse them
    text = re.sub(r"\b(\d+)(mts?)(\d)", r"\1 \2 \3", text, flags=re.IGNORECASE)
    # Fix missing space around "mm" unit (e.g. "300mm300" → "300 mm 300", "9mm" → "9 mm")
    text = re.sub(r"(\d)(mm)(\d)", r"\1 mm \3", text, flags=re.IGNORECASE)
    text = re.sub(r"(\d)(mm)\b", r"\1 \2", text, flags=re.IGNORECASE)
    # Fix "grc" (truncated "gr" unit, e.g. "70 grc/u" → "70 gr/u")
    text = re.sub(r"\bgrc\b", "gr", text, flags=re.IGNORECASE)
    # Fix doubled unit OCR artifact (e.g. "237 grgr" → "237 gr")
    text = re.sub(r"\b(gr|ml|kg|cc|lt)\1\b", r"\1", text, flags=re.IGNORECASE)
    # Convert "N unit/u M u" to multipack form "MxN unit" (e.g. "70 gr/u 10 u" → "10x70 gr")
    text = re.sub(
        r"\b(\d+(?:[.,]\d+)?)\s*(kg|kilo|kilos|gr|grs|gramos|g|lts?|litros?|ml|cc|cm3)/u\s+(\d+)\s*u(?:n(?:idad)?)?\b",
        r"\3x\1 \2", text, flags=re.IGNORECASE,
    )
    weight = None
    volume = None
    units_in_name = None
    units_label = None
    inches = None
    dimensions = None
    page_count = None
    length = None

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

    if weight is None:
        m = _WEIGHT_RE.search(text)
        if m:
            weight = {"value": _to_grams(_parse_number(m.group(1)), m.group(2).lower()), "unit": "g"}
            text = text[:m.start()] + text[m.end():]

    if volume is None:
        m = _VOLUME_RE.search(text)
        if m:
            volume = {"value": _to_ml(_parse_number(m.group(1)), m.group(2).lower()), "unit": "ml"}
            text = text[:m.start()] + text[m.end():]

    # Explicit NxM cm dimensions — run BEFORE NOTEBOOK_DIM_RE so "NxM cm" is fully
    # consumed (including the "cm" token), preventing "cm" from leaking into variant.
    if dimensions is None:
        m = _DIMENSIONS_RE.search(text)
        if m:
            dimensions = f"{m.group(1)}x{m.group(2)} cm"
            text = text[:m.start()] + text[m.end():]
        else:
            m = _SINGLE_CM_RE.search(text)
            if m:
                dimensions = f"{m.group(1).replace(',', '.')} cm"
                text = text[:m.start()] + text[m.end():]

    # NxM units multipack (e.g. "10 x 250 u") — run BEFORE NOTEBOOK_DIM_RE so "10 x 250 u"
    # is consumed as a units multipack rather than partially matched as a dimension.
    if dimensions is None:
        m = _UNITS_PACK_RE.search(text)
        if m:
            lbl = m.group(3).lower()
            if re.match(r"u(?:n(?:d|idades?)?)?$", lbl, re.IGNORECASE):
                lbl = "u"
            dimensions = f"{m.group(1)}x{m.group(2)} {lbl}"
            text = text[:m.start()] + text[m.end():]

    # Notebook/paper dimensions: NxM (both ≥10) without explicit "cm" — run before UNITS_RE
    # so "21 x 27" is not captured as "27 units". `\b` after second number prevents backtracking
    # into partial matches (e.g. "25" from "250 u").
    if dimensions is None:
        m = _NOTEBOOK_DIM_RE.search(text)
        if m:
            dimensions = f"{m.group(1)}x{m.group(2)} cm"
            text = text[:m.start()] + text[m.end():]

    # Page count (hojas/h) — extracted independently so it works with or without dimensions.
    mh = _HOJAS_RE.search(text)
    if mh:
        page_count = int(mh.group(1))
        text = text[:mh.start()] + text[mh.end():]

    # Paños multipack (NxM paños) — run before UNITS_RE to avoid partial capture.
    if dimensions is None:
        m = _PANOS_MULTIPACK_RE.search(text)
        if m:
            dimensions = f"{m.group(1)}x{m.group(2)} {m.group(3).lower()}"
            text = text[:m.start()] + text[m.end():]

    if units_in_name is None:
        m = _UNITS_RE.search(text)
        if m:
            lm = _UNITS_LABEL_RE.search(text[m.start():m.end()])
            units_label = lm.group(2).lower() if lm else None
            if units_label == "sq":
                units_label = "saquitos"
            units_in_name = int(m.group(1) or m.group(2))
            text = text[:m.start()] + text[m.end():]

    m = _INCHES_RE.search(text)
    if m:
        inches = f'{m.group(1).replace(",", ".")}\"'
        text = text[:m.start()] + text[m.end():]

    if dimensions is None:
        m = _SINGLE_MM_RE.search(text)
        if m:
            dimensions = f"{m.group(1).replace(',', '.')} mm"
            text = text[:m.start()] + text[m.end():]

    m = _LENGTH_RE.search(text)
    if m:
        val = m.group(1).replace(",", ".")
        length = f"{val} m"
        text = text[:m.start()] + text[m.end():]

    # Ball / product nominal size number (e.g. "n5" → N°5).
    # Only fires when no other size was found.
    if dimensions is None and weight is None and volume is None and inches is None and units_in_name is None:
        m = _SIZE_CODE_RE.search(text)
        if m:
            dimensions = f"N°{m.group(1)}"
            text = text[:m.start()] + text[m.end():]

    # Trailing bare number fallback → infer unit from magnitude.
    # Only fires when no other size was found, and not for product types (e.g. parrilla)
    # where a bare number is a model code rather than a volume.
    # Decimal values 0.5–49.9 → liters (e.g. "2.25" → 2.25 l).
    # Integers 50–5000 → ml (e.g. "340" → 340 ml).
    _trail_first = _ascii_fold(text.strip().split()[0]).upper() if text.strip() else ""
    if (_trail_first not in _NO_VOLUME_TRAIL_STARTS
            and weight is None and volume is None and inches is None
            and units_in_name is None and dimensions is None
            and page_count is None and length is None):
        m = re.search(r"(?<!\w)(\d+(?:[.,]\d+)?)\s*$", text)
        if m:
            val = _parse_number(m.group(1))
            raw = m.group(1)
            if 0.5 <= val < 50 and ("." in raw or "," in raw):
                volume = {"value": _to_ml(val, "l"), "unit": "ml"}
                text = text[:m.start()] + text[m.end():]
            elif 50 <= val <= 5000:
                volume = {"value": _to_ml(val, "ml"), "unit": "ml"}
                text = text[:m.start()] + text[m.end():]

    # CM trailing fallback — for product types (e.g. "Secador de piso") where a trailing
    # bare integer is a width in cm rather than a volume.
    if _trail_first in _CM_TRAIL_STARTS and dimensions is None:
        m = re.search(r"(?<!\w)(\d+(?:[.,]\d+)?)\s*$", text)
        if m:
            val = _parse_number(m.group(1))
            if 20 <= val <= 120:
                dimensions = f"{int(val)} cm"
                text = text[:m.start()] + text[m.end():]

    # Suppress all size data for product types that are descriptive sets or furniture
    # (e.g. "Set tazas ... 2 u 350 ml", "Silla plegable ...", "Soporte TV ...").
    if _orig_first_word in _NO_SIZE_TYPES:
        weight = volume = units_in_name = units_label = None
        inches = dimensions = page_count = length = None

    tokens_raw = text.split()
    if tokens_raw:
        first_token = tokens_raw[0]
        rest = _CONTAINER_RE.sub("", " ".join(tokens_raw[1:])).split()
        tokens = [first_token] + rest
    else:
        tokens = []

    product_type, brand, remaining = _extract_product_type_and_brand(tokens)
    variant = clean_name(" ".join(remaining)) if remaining else None
    if variant == "":
        variant = None

    parts = [p for p in [product_type, brand, variant] if p]
    clean = " ".join(parts)

    return {
        "product_type":  product_type,
        "brand":         brand,
        "variant":       variant,
        "weight":        weight,
        "volume":        volume,
        "units_in_name": units_in_name,
        "units_label":   units_label,
        "inches":        inches,
        "dimensions":    dimensions,
        "page_count":    page_count,
        "length":        length,
        "clean_name":    clean,
    }


# ---------------------------------------------------------------------------
# Category parsing
# ---------------------------------------------------------------------------

def parse_category(raw: str) -> dict:
    """
    Vital categories are already single-level top-level department names
    (e.g. "Almacen", "Bebidas"). Return section=raw, subsection=None, leaf=None.
    """
    return {
        "section":    clean_name(raw) if raw else None,
        "subsection": None,
        "leaf":       None,
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
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    sys.stdout.reconfigure(encoding="utf-8")

    async def run() -> None:
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

        print(f"\n{'RAW NAME':<50} {'TYPE':<22} {'BRAND':<20} {'VARIANT':<25} {'W':<10} {'V':<10} {'U':<4} {'CAT'}")
        print("-" * 155)
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
                f"{w:<10} {v:<10} "
                f"{str(f['units_in_name'] or ''):<4} "
                f"{c['section'] or ''}"
            )

    asyncio.run(run())
