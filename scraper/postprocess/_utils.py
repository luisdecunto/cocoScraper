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
