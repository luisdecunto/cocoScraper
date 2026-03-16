"""Shared helpers for supplier postprocessing modules."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

_DATA_DIR = Path(__file__).parent / "data"

_LOWER_WORDS = {
    "y",
    "e",
    "o",
    "u",
    "de",
    "del",
    "la",
    "las",
    "el",
    "los",
    "en",
    "con",
    "sin",
    "para",
    "por",
    "al",
    "a",
}


def _ascii_fold(text: str) -> str:
    """Remove accents for accent-insensitive matching (ñ→N, á→A, etc.)."""
    return unicodedata.normalize("NFD", text).encode("ascii", "ignore").decode("ascii")


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


def _load_aliases(filename: str) -> dict[str, str]:
    """Load VARIANT=CANONICAL alias pairs keyed by folded uppercase variant."""
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


def _normalize_brand_str(s: str) -> str:
    """Core normalization: uppercase, no accents, punctuation stripped (no alias lookup)."""
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.upper()
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


_BRAND_ALIASES: dict[str, str] | None = None


def _get_brand_aliases() -> dict[str, str]:
    """Load brand_aliases.txt once and cache. Keys are fully normalized."""
    global _BRAND_ALIASES
    if _BRAND_ALIASES is None:
        path = _DATA_DIR / "brand_aliases.txt"
        _BRAND_ALIASES = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                variant, _, canonical = line.partition("=")
                key = _normalize_brand_str(variant.strip())
                _BRAND_ALIASES[key] = canonical.strip()
    return _BRAND_ALIASES


def normalize_brand(brand: str | None) -> str | None:
    """
    Canonical brand form for DB storage:
      1. Uppercase + no accents + punctuation stripped
      2. Alias lookup (brand_aliases.txt): HELLMANN S → HELLMANS, etc.
    Called by the pipeline on every brand before writing to DB.
    Add new aliases to scraper/postprocess/data/brand_aliases.txt.
    """
    if not brand:
        return brand
    s = _normalize_brand_str(brand)
    if not s:
        return None
    return _get_brand_aliases().get(s, s)


def clean_name(raw: str) -> str:
    """Strip whitespace, fix encoding artifacts, and convert to title case."""
    text = unicodedata.normalize("NFKC", raw)
    text = re.sub(r"\s+", " ", text).strip()
    words = text.split()
    result: list[str] = []
    for i, word in enumerate(words):
        lower = word.lower()
        if i == 0 or lower not in _LOWER_WORDS:
            result.append(word.capitalize())
        else:
            result.append(lower)
    return " ".join(result)
