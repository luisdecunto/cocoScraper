"""Test _build_name fix for products where largeDescription is just a size."""
from scraper.config import load_supplier_class, get_supplier_config

s = load_supplier_class(get_supplier_config("nini"))

cases = [
    # broken: largeDescription has no brand
    {"id": "2965593", "trademark": "PALITOS", "largeDescription": "     80 G",          "smallDescription": "PALITOS snack 80 G"},
    # normal: trademark already in largeDescription — must NOT duplicate
    {"id": "5794323", "trademark": "CANUELAS", "largeDescription": "CANUELAS  Aceite Girasol  5 Lts", "smallDescription": "CANUELAS  Aceite Girasol"},
    # no trademark at all
    {"id": "0000001", "trademark": None,      "largeDescription": "Producto sin marca 500 ml",       "smallDescription": "Producto 500 ml"},
    # largeDescription totally empty, falls back to small
    {"id": "0000002", "trademark": "ACME",    "largeDescription": "",                                "smallDescription": "ACME Producto"},
]

for c in cases:
    print(f"SKU {c['id']}: '{s._build_name(c)}'")
